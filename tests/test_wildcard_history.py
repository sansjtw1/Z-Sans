"""Phase 4 回归测试：泛解析检测/过滤 与 历史 URL 采集。"""
import dns.resolver
import pytest

import core.zsans_engine as engine
import core.breeders.breeders as breeders
from core.breeders.breeders import DomainBreeder
from core.zsans_engine import URLAsset, ASSET_TYPE_URL


class _Rdata:
    def __init__(self, address):
        self.address = address


class _FakeResp:
    def __init__(self, status=200, payload=None, text=""):
        self.status_code = status
        self._payload = payload
        self.text = text

    def json(self):
        return self._payload


class _SeqSession:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def get(self, url, timeout=None):
        self.calls.append(url)
        return self._responses.pop(0)


def test_detect_wildcard_true(monkeypatch):
    monkeypatch.setattr(dns.resolver, "resolve",
                        lambda *a, **k: [_Rdata("1.2.3.4")])
    info = DomainBreeder({}, None)._detect_wildcard("example.com")
    assert info["is_wildcard"] is True
    assert info["ips"] == {"1.2.3.4"}


def test_detect_wildcard_false_when_nxdomain(monkeypatch):
    def _raise(*a, **k):
        raise dns.resolver.NXDOMAIN()
    monkeypatch.setattr(dns.resolver, "resolve", _raise)
    info = DomainBreeder({}, None)._detect_wildcard("example.com")
    assert info["is_wildcard"] is False
    assert info["ips"] == set()


def test_detect_wildcard_cached(tmp_path, monkeypatch):
    calls = {"n": 0}

    def _count(*a, **k):
        calls["n"] += 1
        return [_Rdata("9.9.9.9")]
    monkeypatch.setattr(dns.resolver, "resolve", _count)

    engine.init_http_config({"cache": {"enabled": True, "dir": str(tmp_path), "ttl": {"dns": 60}}})
    try:
        breeder = DomainBreeder({}, None)
        breeder._detect_wildcard("cached.test")
        breeder._detect_wildcard("cached.test")
        assert calls["n"] == 2  # samples=2，第二次读缓存不再查询
    finally:
        engine.init_http_config({})


def test_dns_brute_filters_wildcard_ips(monkeypatch):
    monkeypatch.setattr(dns.resolver, "resolve",
                        lambda *a, **k: [_Rdata("1.2.3.4")])
    monkeypatch.setattr(DomainBreeder, "_BRUTE_SUBDOMAINS", ["a", "b", "c"])
    breeder = DomainBreeder({}, None)

    without = breeder._dns_brute_subdomains("example.com", wildcard_ips=set())
    with_filter = breeder._dns_brute_subdomains("example.com", wildcard_ips={"1.2.3.4"})

    assert len(without) == 3
    assert with_filter == []


def test_query_wayback_cdx(monkeypatch):
    payload = [["original"], ["http://example.com/a"], ["https://example.com/b"]]
    session = _SeqSession([_FakeResp(200, payload)])
    monkeypatch.setattr(engine, "get_http_session", lambda: session)

    urls = DomainBreeder({}, None)._query_wayback_cdx("example.com")
    assert urls == {"http://example.com/a", "https://example.com/b"}


def test_query_commoncrawl(monkeypatch):
    collinfo = [{"cdx-api": "https://index.example/cc"}]
    index_body = '{"url": "http://example.com/x"}\n{"url": "https://example.com/y"}'
    session = _SeqSession([_FakeResp(200, collinfo), _FakeResp(200, text=index_body)])
    monkeypatch.setattr(engine, "get_http_session", lambda: session)

    urls = DomainBreeder({}, None)._query_commoncrawl("example.com")
    assert urls == {"http://example.com/x", "https://example.com/y"}


def test_execute_emits_historical_url_assets(monkeypatch):
    breeder = DomainBreeder({
        "historical_urls": {"enabled": True, "wayback": {"enabled": True}},
    }, None)
    monkeypatch.setattr(breeder, "_discover_subdomains", lambda d, tm: [])
    monkeypatch.setattr(breeder, "_resolve_domain", lambda d: [])
    monkeypatch.setattr(breeder, "_probe_certificate", lambda d, port=443: None)
    monkeypatch.setattr(breeder, "_query_wayback_cdx", lambda d: {"http://example.com/old"})

    asset = URLAsset("https://example.com", source="seed", depth=0)
    # 用域名资产执行
    from core.zsans_engine import DomainAsset
    asset = DomainAsset("example.com", source="seed", depth=0)
    results = breeder.execute(asset, None)

    hist = [a for a in results if getattr(a, "type", None) == ASSET_TYPE_URL
            and a.properties.get("source_tool") == "wayback"]
    assert hist
    assert hist[0].value == "http://example.com/old"
