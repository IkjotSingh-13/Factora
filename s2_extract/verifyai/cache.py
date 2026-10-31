"""Tiny thread-safe TTL cache with an optional on-disk JSON layer.

Why: every external call must fail soft and never be the reason a demo dies.
During the hackathon we warm this cache before the demo so verification still
works with hotel wifi / a rate-limited API.

Usage
-----
    cache = Cache(ttl_s=600, disk_dir=".cache/retrieval")
    cache.get("wiki:quantum entanglement")      # -> value | None
    cache.set("wiki:quantum entanglement", val)
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Optional


def make_key(*parts: Any) -> str:
    """Stable cache key for an arbitrary tuple of primitives."""
    raw = json.dumps(parts, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


class Cache:
    def __init__(
        self,
        ttl_s: int = 600,
        disk_dir: str | os.PathLike | None = None,
        max_entries: int = 2048,
    ) -> None:
        self.ttl_s = ttl_s
        self.max_entries = max_entries
        self._mem: dict[str, tuple[float, Any]] = {}
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0
        self.disk_dir: Optional[Path] = Path(disk_dir) if disk_dir else None
        if self.disk_dir:
            self.disk_dir.mkdir(parents=True, exist_ok=True)

    # ---------------------------------------------------------------- memory
    def get(self, key: str) -> Any | None:
        now = time.time()
        with self._lock:
            hit = self._mem.get(key)
            if hit is not None:
                expires, value = hit
                if expires > now:
                    self.hits += 1
                    return value
                self._mem.pop(key, None)
        disk = self._disk_get(key)
        if disk is not None:
            self.hits += 1
            self._mem_put(key, disk)  # promote to memory
            return disk
        self.misses += 1
        return None

    def set(self, key: str, value: Any) -> None:
        self._mem_put(key, value)
        self._disk_set(key, value)

    def _mem_put(self, key: str, value: Any) -> None:
        with self._lock:
            if len(self._mem) >= self.max_entries:
                # drop the entry closest to expiry
                oldest = min(self._mem.items(), key=lambda kv: kv[1][0])[0]
                self._mem.pop(oldest, None)
            self._mem[key] = (time.time() + self.ttl_s, value)

    # ------------------------------------------------------------------ disk
    def _path(self, key: str) -> Path | None:
        if not self.disk_dir:
            return None
        return self.disk_dir / f"{key}.json"

    def _disk_get(self, key: str) -> Any | None:
        path = self._path(key)
        if not path or not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if payload.get("expires_at", 0) <= time.time():
                return None
            return payload["value"]
        except (OSError, json.JSONDecodeError, KeyError):
            return None

    def _disk_set(self, key: str, value: Any) -> None:
        path = self._path(key)
        if not path:
            return
        try:
            path.write_text(
                json.dumps({"expires_at": time.time() + self.ttl_s, "value": value}, default=str),
                encoding="utf-8",
            )
        except (OSError, TypeError):
            pass  # disk cache is best-effort only

    # ----------------------------------------------------------------- misc
    def clear(self) -> None:
        with self._lock:
            self._mem.clear()
        if self.disk_dir and self.disk_dir.exists():
            for file in self.disk_dir.glob("*.json"):
                file.unlink(missing_ok=True)

    def stats(self) -> dict[str, Any]:
        return {"hits": self.hits, "misses": self.misses, "entries": len(self._mem)}
