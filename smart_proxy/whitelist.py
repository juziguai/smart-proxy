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

    def get(self):
        self._whitelist.refresh_if_needed()
        store = self._store_getter()
        candidates = store.get_whitelist_candidates(limit=12) if store else []
        entries = self._whitelist.entries()
        return {
            "entries": entries,
            "path": self._whitelist.path,
            "count": len(entries),
            "loaded_at": self._whitelist.loaded_at,
            "candidates": candidates,
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

