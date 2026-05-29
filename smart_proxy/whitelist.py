from datetime import datetime, timezone
import fnmatch
import os
import time


class Whitelist:
    def __init__(self, path, reload_interval):
        self._path = os.fspath(path)
        self._interval = reload_interval
        self._expires = 0
        self._patterns = set()
        self._loaded_at = ""

    def _load(self):
        try:
            with open(self._path, encoding="utf-8") as f:
                self._patterns = {
                    line.strip() for line in f
                    if line.strip() and not line.startswith("#")
                }
        except FileNotFoundError:
            self._patterns = set()
        self._loaded_at = datetime.now(timezone.utc).isoformat()
        self._expires = time.monotonic() + self._interval

    def refresh_if_needed(self):
        now = time.monotonic()
        if now >= self._expires:
            self._load()

    def reload(self):
        self._load()

    def match(self, host):
        self.refresh_if_needed()
        if not self._patterns:
            return False
        return any(fnmatch.fnmatch(host, pattern) for pattern in self._patterns)

    def entries(self):
        self.refresh_if_needed()
        return sorted(self._patterns)

    def save_entries(self, entries):
        cleaned = []
        seen = set()
        for entry in entries:
            value = str(entry).strip()
            if not value or value.startswith("#"):
                continue
            if any(char.isspace() for char in value):
                raise ValueError(f"invalid whitelist entry: {value}")
            if value not in seen:
                seen.add(value)
                cleaned.append(value)
        directory = os.path.dirname(self._path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(self._path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write("# Managed by Smart Proxy Console\n")
            for entry in cleaned:
                handle.write(f"{entry}\n")
        self._load()
        return self.entries()

    @property
    def path(self):
        return self._path

    @property
    def pattern_count(self):
        return len(self._patterns)

    @property
    def loaded_at(self):
        return self._loaded_at


class WhitelistProvider:
    def __init__(self, whitelist_obj, store_getter):
        self._whitelist = whitelist_obj
        self._store_getter = store_getter

    def _generalize_candidates(self, candidates, entries):
        """
        智能化白名单通配符预测与去重推荐算法：
        1. 当发现候选人中有同父域的其他子域名(如 token-plan-cn.xiaomimimo.com)，
           而白名单中已存在该父域下的精确规则(如 platform.xiaomimimo.com)时，
           自动聚合成 `*.xiaomimimo.com` 升级推荐，高亮排在候选人最顶端；
        2. 若候选人已被现有的通配符规则覆盖，直接标记为 is_covered = True 以过滤干扰。
        """
        wildcards = [e for e in entries if "*" in e]
        exacts = [e for e in entries if "*" not in e]

        processed = []
        parent_exacts = {}
        for e in exacts:
            parts = e.split(".")
            if len(parts) >= 2:
                parent = ".".join(parts[-2:])
                parent_exacts[parent] = e

        for c in candidates:
            host = c["host"]

            # 1. 检查是否已被现有通配符覆盖
            import fnmatch
            is_covered = any(fnmatch.fnmatch(host, w) for w in wildcards)
            c["is_covered"] = is_covered
            c["suggestion_type"] = "normal"
            c["reason"] = ""

            # 2. 检查是否有同父域升级机会
            parts = host.split(".")
            if len(parts) >= 3:
                parent = ".".join(parts[-2:])
                if parts[-2] in ("com", "org", "net", "gov", "edu") and len(parts) >= 4:
                    parent = ".".join(parts[-3:])

                if parent in parent_exacts and not is_covered:
                    wildcard_pattern = f"*.{parent}"
                    if not any(p["host"] == wildcard_pattern for p in processed):
                        processed.append({
                            "host": wildcard_pattern,
                            "total_requests": c["total_requests"],
                            "proxy_requests": c["proxy_requests"],
                            "whitelist_requests": c["whitelist_requests"],
                            "failed_requests": c["failed_requests"],
                            "slow_requests": c["slow_requests"],
                            "average_connect_latency_ms": c["average_connect_latency_ms"],
                            "is_covered": False,
                            "suggestion_type": "wildcard_upgrade",
                            "reason": parent_exacts[parent]
                        })

            processed.append(c)

        processed.sort(key=lambda x: (x.get("suggestion_type") == "wildcard_upgrade", x["proxy_requests"]), reverse=True)
        return processed

    def get(self):
        self._whitelist.refresh_if_needed()
        store = self._store_getter()
        candidates = store.get_whitelist_candidates(limit=12) if store else []
        entries = self._whitelist.entries()

        # 智能白名单通配符预测升级
        generalized = self._generalize_candidates(candidates, entries)

        return {
            "entries": entries,
            "path": self._whitelist.path,
            "count": len(entries),
            "loaded_at": self._whitelist.loaded_at,
            "candidates": generalized,
        }

    def save(self, payload):
        entries = payload.get("entries")
        if not isinstance(entries, list):
            raise ValueError("entries must be a list")
        saved = self._whitelist.save_entries(entries)
        return {
            "ok": True,
            "entries": saved,
            "count": len(saved),
            "path": self._whitelist.path,
            "loaded_at": self._whitelist.loaded_at,
        }


class Blocklist(Whitelist):
    """屏蔽名单：glob 匹配即快速拒绝，不转发到上游。

    与 Whitelist 完全相同的文件格式和匹配逻辑，
    语义上独立命名，使路由决策代码意图更清晰。
    """
    pass


class BlocklistProvider:
    """为 Dashboard API 提供 blocklist 读取与写入能力。"""

    def __init__(self, blocklist_obj):
        self._blocklist = blocklist_obj

    def get(self):
        self._blocklist.refresh_if_needed()
        entries = self._blocklist.entries()
        return {
            "entries": entries,
            "path": self._blocklist.path,
            "count": len(entries),
            "loaded_at": self._blocklist.loaded_at,
        }

    def save(self, payload):
        entries = payload.get("entries")
        if not isinstance(entries, list):
            raise ValueError("entries must be a list")
        saved = self._blocklist.save_entries(entries)
        return {
            "ok": True,
            "entries": saved,
            "count": len(saved),
            "path": self._blocklist.path,
            "loaded_at": self._blocklist.loaded_at,
        }

