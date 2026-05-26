import asyncio
import logging
import os
import sys
import time
from pathlib import Path

class DailyRotatingFileHandler(logging.FileHandler):
    """工业级日滚动日志处理器：直接将当天日志记录为 smart-proxy-YYYY-MM-DD.log"""
    def __init__(self, logs_dir, backup_count=7):
        self.logs_dir = Path(logs_dir)
        self.backup_count = backup_count
        self.current_date = time.strftime("%Y-%m-%d")
        filename = self.logs_dir / f"smart-proxy-{self.current_date}.log"
        super().__init__(filename, encoding="utf-8")
        self._cleanup_old_logs()
        
    def emit(self, record):
        # 每次写入前自适应检测是否跨天
        now_date = time.strftime("%Y-%m-%d")
        if now_date != self.current_date:
            self.current_date = now_date
            self.close()
            # 动态更新物理路径，实现跨天无缝切分
            self.baseFilename = os.path.abspath(self.logs_dir / f"smart-proxy-{self.current_date}.log")
            self.stream = self._open()
            self._cleanup_old_logs()
        super().emit(record)
        
    def _cleanup_old_logs(self):
        # 自动保留最近 N 天的日志，删除更早的
        try:
            log_files = sorted(self.logs_dir.glob("smart-proxy-*.log"))
            if len(log_files) > self.backup_count:
                for old_file in log_files[:-self.backup_count]:
                    old_file.unlink(missing_ok=True)
        except Exception:
            pass


from concurrent.futures import ThreadPoolExecutor

# 全局异步日志队列与消费者任务管理
_log_queue = None
_consumer_task = None
_thread_pool = ThreadPoolExecutor(max_workers=1)

class AsyncDailyRotatingFileHandler(logging.Handler):
    """
    零阻塞、高吞吐的异步日滚动日志处理器。
    直接将 LogRecord 塞入内存 asyncio.Queue 中，由后台专门的线程池异步写盘。
    """
    def __init__(self, logs_dir, backup_count=7):
        super().__init__()
        # 底层使用同步 DailyRotatingFileHandler 负责实际写盘和滚动
        self.underlying_handler = DailyRotatingFileHandler(logs_dir, backup_count)
        
    def setFormatter(self, formatter):
        super().setFormatter(formatter)
        self.underlying_handler.setFormatter(formatter)
        
    def emit(self, record):
        global _log_queue
        # 必须确保在 asyncio Event Loop 运行且队列已拉起场景下投递
        if _log_queue is not None:
            try:
                # 极其轻量的非阻塞投递，如队满则丢弃防内存无限膨胀
                _log_queue.put_nowait(record)
            except asyncio.QueueFull:
                pass
            except Exception:
                # 异常容错兜底：回退到同步写盘
                try:
                    self.underlying_handler.emit(record)
                except Exception:
                    pass
        else:
            # 早期启动阶段（Loop 启动前）：同步写盘以防日志丢失
            try:
                self.underlying_handler.emit(record)
            except Exception:
                pass

    def write_batch_sync(self, records):
        """线程池内同步批量写入磁盘并刷盘"""
        for r in records:
            try:
                self.underlying_handler.emit(r)
            except Exception:
                pass
        try:
            self.underlying_handler.flush()
        except Exception:
            pass


async def consume_logs_loop(queue, handler):
    """后台日志非阻塞消费与微批合并落盘循环"""
    loop = asyncio.get_running_loop()
    try:
        while True:
            # 1. 挂起等待队列中第一个日志
            first_record = await queue.get()
            if first_record is None:  # 收到退出哨兵
                queue.task_done()
                break
                
            batch = [first_record]
            queue.task_done()
            
            # 2. 微批合并：在当前事件循环周期中尽量捞出队列里的堆积日志（最多合并 100 条）
            while len(batch) < 100:
                if queue.empty():
                    break
                r = queue.get_nowait()
                if r is None:  # 收到退出哨兵，但还有数据未写完
                    # 先把退出哨兵重新塞回队列以便下一轮循环彻底退出
                    await queue.put(None)
                    queue.task_done()
                    break
                batch.append(r)
                queue.task_done()
                
            # 3. 将本批次日志投递给线程池异步写盘，绝对不影响主协程的 CPU 时间片
            if batch:
                await loop.run_in_executor(_thread_pool, handler.write_batch_sync, batch)
                
    except asyncio.CancelledError:
        pass
    except Exception:
        pass


def setup_profiler_logger():
    """初始化并获取全链路高精度时延追踪专用 Logger"""
    logger = logging.getLogger("ProxyProfiler")
    if not logger.handlers:
        logs_dir = Path(__file__).resolve().parents[1] / "logs"
        logs_dir.mkdir(exist_ok=True)
        
        # 1. 采用零阻塞异步 Handler 替代传统的同步 FileHandler
        async_handler = AsyncDailyRotatingFileHandler(logs_dir, backup_count=7)
        formatter = logging.Formatter("[sp_profiler %(asctime)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
        async_handler.setFormatter(formatter)
        logger.addHandler(async_handler)
        
        # 2. 智能 TTY 检测：仅当在前台交互终端运行时，才开启控制台屏幕输出，彻底防止后台运行下的 stdout 重定向双写
        if sys.stdout.isatty():
            console_handler = logging.StreamHandler(sys.stdout)
            console_handler.setFormatter(formatter)
            logger.addHandler(console_handler)
            
        logger.setLevel(logging.INFO)
    return logger


def start_async_logging_listener():
    """由主程序拉起事件循环后调用，初始化异步队列与后台消费任务"""
    global _log_queue, _consumer_task
    try:
        loop = asyncio.get_running_loop()
        _log_queue = asyncio.Queue(maxsize=10000)
        
        # 获取 Logger 上的异步 Handler
        logger = logging.getLogger("ProxyProfiler")
        async_handler = None
        for h in logger.handlers:
            if isinstance(h, AsyncDailyRotatingFileHandler):
                async_handler = h
                break
                
        if async_handler:
            _consumer_task = loop.create_task(consume_logs_loop(_log_queue, async_handler))
    except RuntimeError:
        pass  # 防止在非 asyncio 环境下报错


async def shutdown_async_logging():
    """优雅退出：强制冲刷所有残留日志并关闭线程池"""
    global _log_queue, _consumer_task
    if _log_queue is not None and _consumer_task is not None:
        try:
            # 投递 None 退出哨兵
            await _log_queue.put(None)
            # 等待消费者任务彻底终结
            await _consumer_task
        except Exception:
            pass
    _thread_pool.shutdown(wait=True)


# 暴露极简、高内聚的模块化访问接口
profiler_logger = setup_profiler_logger()
