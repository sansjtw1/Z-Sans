"""Phase 2 回归测试：HTTP 限速、跨运行缓存、每工具并发上限。"""
import threading
import time

import pytest
import requests
from requests.adapters import HTTPAdapter

import core.zsans_engine as engine
from core.cache import DiskCache
from core.tools.tools import ToolOrchestrator


@pytest.fixture(autouse=True)
def _reset_http_layer():
    yield
    engine.init_http_config({})
    engine.set_cache_bypass(False)


class _FakeClock:
    def __init__(self):
        self.t = 0.0
        self.slept = []

    def monotonic(self):
        return self.t

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.t += seconds


class _CountingAdapter(HTTPAdapter):
    def __init__(self):
        super().__init__()
        self.calls = 0

    def send(self, request, **kwargs):
        self.calls += 1
        response = requests.Response()
        response.status_code = 200
        response.url = request.url
        response._content = b"hello-cache"
        response.headers["Content-Type"] = "text/plain"
        response.request = request
        return response


# ---------- DiskCache ----------

def test_diskcache_roundtrip_and_ttl(tmp_path):
    cache = DiskCache(str(tmp_path / "c.sqlite"), enabled=True)
    key = cache.make_key("GET", "http://example.test/a")

    assert cache.get("http", key) is None
    cache.set("http", key, b"value", ttl=60)
    assert cache.get("http", key) == b"value"

    cache.set("http", key, b"expired", ttl=-1)
    assert cache.get("http", key) is None


def test_diskcache_disabled_is_noop(tmp_path):
    cache = DiskCache(str(tmp_path / "c.sqlite"), enabled=False)
    key = cache.make_key("a")
    cache.set("a", key, b"1")
    assert cache.get("a", key) is None


def test_make_key_is_deterministic():
    assert DiskCache.make_key("GET", "u") == DiskCache.make_key("GET", "u")
    assert DiskCache.make_key("GET", "u") != DiskCache.make_key("POST", "u")


# ---------- Rate limiter ----------

def test_rate_limiter_token_pacing(monkeypatch):
    clock = _FakeClock()
    monkeypatch.setattr(engine, "time", clock)

    limiter = engine._RateLimiter()
    limiter.configure(rps=2, burst=1, per_host_concurrency=0)

    limiter.wait_token()          # 消耗突发令牌，立即返回
    assert clock.slept == []

    limiter.wait_token()          # 需按 2 rps 等待 0.5s
    assert clock.slept
    assert abs(clock.t - 0.5) < 0.05


def test_rate_limiter_per_host_concurrency():
    limiter = engine._RateLimiter()
    limiter.configure(per_host_concurrency=1)

    inside = [0]
    peak = [0]
    lock = threading.Lock()

    def worker():
        with limiter.acquire_host("example.test"):
            with lock:
                inside[0] += 1
                peak[0] = max(peak[0], inside[0])
            time.sleep(0.03)
            with lock:
                inside[0] -= 1

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert peak[0] == 1


def test_rate_limiter_disabled_is_cheap():
    limiter = engine._RateLimiter()
    limiter.configure()
    assert limiter.enabled() is False
    limiter.wait_token()  # 不应阻塞


# ---------- ThrottledSession ----------

def test_get_http_session_is_throttled():
    assert isinstance(engine.get_http_session(), engine.ThrottledSession)


def test_http_cache_hit_skips_adapter(tmp_path):
    engine.init_http_config({"cache": {"enabled": True, "dir": str(tmp_path), "ttl": {"http": 60}}})
    session = engine.get_http_session()
    adapter = _CountingAdapter()
    session.mount("https://", adapter)

    request = requests.Request("GET", "https://cache-test.example/x").prepare()
    first = session.send(request)
    second = session.send(request)

    assert adapter.calls == 1
    assert first.content == b"hello-cache"
    assert second.content == b"hello-cache"


def test_http_cache_bypass(tmp_path):
    engine.init_http_config({"cache": {"enabled": True, "dir": str(tmp_path), "ttl": {"http": 60}}})
    session = engine.get_http_session()
    adapter = _CountingAdapter()
    session.mount("https://", adapter)

    request = requests.Request("GET", "https://cache-test.example/y").prepare()
    session.send(request)
    engine.set_cache_bypass(True)
    session.send(request)

    assert adapter.calls == 2


# ---------- Per-tool concurrency ----------

def test_tool_semaphore_limits_concurrency():
    orchestrator = ToolOrchestrator({"concurrency": {"tools": {"subfinder": 1}}}, engine=None)
    try:
        inside = [0]
        peak = [0]
        lock = threading.Lock()

        def worker():
            with orchestrator._tool_slot("subfinder"):
                with lock:
                    inside[0] += 1
                    peak[0] = max(peak[0], inside[0])
                time.sleep(0.03)
                with lock:
                    inside[0] -= 1

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert peak[0] == 1
    finally:
        orchestrator.executor.shutdown(wait=False)


def test_tool_slot_absent_is_noop():
    orchestrator = ToolOrchestrator({}, engine=None)
    try:
        with orchestrator._tool_slot("not-configured"):
            pass
    finally:
        orchestrator.executor.shutdown(wait=False)
