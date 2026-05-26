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


def setup_profiler_logger():
    """初始化并获取全链路高精度时延追踪专用 Logger"""
    logger = logging.getLogger("ProxyProfiler")
    if not logger.handlers:
        logs_dir = Path(__file__).resolve().parents[1] / "logs"
        logs_dir.mkdir(exist_ok=True)
        
        # 1. 采用自定义 DailyRotatingFileHandler，今天日志直接命名为 smart-proxy-YYYY-MM-DD.log
        rotating_handler = DailyRotatingFileHandler(logs_dir, backup_count=7)
        formatter = logging.Formatter("[sp_profiler %(asctime)s] %(message)s", datefmt="%H:%M:%S")
        rotating_handler.setFormatter(formatter)
        logger.addHandler(rotating_handler)
        
        # 2. 智能 TTY 检测：仅当在前台交互终端运行时，才开启控制台屏幕输出，彻底防止后台运行下的 stdout 重定向双写
        if sys.stdout.isatty():
            console_handler = logging.StreamHandler(sys.stdout)
            console_handler.setFormatter(formatter)
            logger.addHandler(console_handler)
            
        logger.setLevel(logging.INFO)
    return logger


# 暴露极简、高内聚的模块化访问接口
profiler_logger = setup_profiler_logger()
