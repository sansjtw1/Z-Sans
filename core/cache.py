#!/usr/bin/env python3
# coding: utf-8
"""跨运行磁盘缓存（sqlite / WAL / 每线程连接）。

用途：缓存 DNS 解析、TLS 证书探测与 HTTP GET 响应，重扫与 `--watch`
模式可显著减少对目标的重复请求。

设计要点：
- 每线程独立 sqlite 连接，避免 sqlite 的跨线程限制；
- WAL 模式 + 短超时 + 写冲突重试，兼顾并发写；
- key 由调用方用 :func:`DiskCache.make_key` 生成（sha256）；
- TTL 过期即视为未命中；后台维护时清理过期项并强制容量上限；
- 默认关闭（enabled=False 时不建库、不落盘）。
"""
import hashlib
import os
import sqlite3
import threading
import time


class DiskCache:
    """一个极简的键值磁盘缓存。值统一以 bytes 存储。"""

    def __init__(self, path, enabled=True, max_entries=100000, max_mb=200):
        self.enabled = bool(enabled)
        self.path = path
        self.max_entries = max(1, int(max_entries or 1))
        self.max_bytes = max(1, int(max_mb or 1)) * 1024 * 1024
        self._local = threading.local()
        self._write_lock = threading.Lock()
        self._set_count = 0
        if self.enabled:
            directory = os.path.dirname(os.path.abspath(path)) or '.'
            os.makedirs(directory, exist_ok=True)
            try:
                os.chmod(directory, 0o700)
            except OSError:
                pass
            self._init_db()

    # ---------- 内部 ----------
    def _conn(self):
        conn = getattr(self._local, 'conn', None)
        if conn is None:
            conn = sqlite3.connect(self.path, timeout=5, check_same_thread=False)
            conn.execute('PRAGMA journal_mode=WAL')
            conn.execute('PRAGMA synchronous=NORMAL')
            conn.execute(
                'CREATE TABLE IF NOT EXISTS cache ('
                'ns TEXT, k TEXT, v BLOB, created REAL, expires REAL, '
                'PRIMARY KEY (ns, k))'
            )
            self._local.conn = conn
        return conn

    def _init_db(self):
        try:
            self._conn()
        except sqlite3.Error:
            # 无法建库时降级为禁用，不影响主流程
            self.enabled = False

    @staticmethod
    def make_key(*parts):
        digest = hashlib.sha256()
        for part in parts:
            if isinstance(part, bytes):
                digest.update(part)
            else:
                digest.update(str(part).encode('utf-8', 'replace'))
            digest.update(b'\x00')
        return digest.hexdigest()

    # ---------- 读写 ----------
    def get(self, namespace, key):
        if not self.enabled:
            return None
        try:
            row = self._conn().execute(
                'SELECT v, expires FROM cache WHERE ns=? AND k=?', (namespace, key)
            ).fetchone()
        except sqlite3.Error:
            return None
        if not row:
            return None
        value, expires = row
        if expires and expires < time.time():
            return None
        return value

    def set(self, namespace, key, value, ttl=None):
        if not self.enabled:
            return
        if isinstance(value, str):
            value = value.encode('utf-8')
        expires = (time.time() + ttl) if ttl else 0
        with self._write_lock:
            for _attempt in range(3):
                try:
                    conn = self._conn()
                    conn.execute(
                        'INSERT OR REPLACE INTO cache (ns, k, v, created, expires) '
                        'VALUES (?, ?, ?, ?, ?)',
                        (namespace, key, value, time.time(), expires),
                    )
                    conn.commit()
                    break
                except sqlite3.OperationalError:
                    time.sleep(0.05)
                except sqlite3.Error:
                    return
            self._set_count += 1
            if self._set_count % 200 == 0:
                self.maintain()

    # ---------- 维护 ----------
    def maintain(self):
        """清理过期项并按容量上限淘汰最旧记录。"""
        if not self.enabled:
            return
        try:
            conn = self._conn()
            conn.execute('DELETE FROM cache WHERE expires > 0 AND expires < ?', (time.time(),))
            count = conn.execute('SELECT COUNT(*) FROM cache').fetchone()[0]
            if count > self.max_entries:
                excess = count - self.max_entries
                conn.execute(
                    'DELETE FROM cache WHERE rowid IN '
                    '(SELECT rowid FROM cache ORDER BY created ASC LIMIT ?)',
                    (excess,),
                )
            conn.commit()
        except sqlite3.Error:
            pass

    def close(self):
        conn = getattr(self._local, 'conn', None)
        if conn is not None:
            try:
                conn.close()
            except sqlite3.Error:
                pass
            self._local.conn = None
