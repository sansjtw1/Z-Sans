"""Phase 5 回归测试：子域接管检测（仅检测，无利用行为）。"""
from types import SimpleNamespace

import dns.resolver
import pytest

import core.takeover as takeover
from core.takeover import (
    TAKEOVER_SIGNATURES, detect_takeover, match_signature, resolve_cname_chain,
)
from core.breeders.breeders import DomainBreeder
from core.zsans_engine import DomainAsset


class _Rdata:
    def __init__(self, target):
        self.target = target


def test_signature_table_wellformed():
    assert TAKEOVER_SIGNATURES
    for signature in TAKEOVER_SIGNATURES:
        assert signature["service"]
        assert signature["cname_patterns"]
        assert all(hasattr(p, "search") for p in signature["cname_patterns"])


def test_match_signature_hits_github_pages():
    signature = match_signature(["foo.github.io"])
    assert signature is not None
    assert signature["service"] == "GitHub Pages"


def test_match_signature_no_match():
    assert match_signature(["cdn.example.com"]) is None


def test_resolve_cname_chain(monkeypatch):
    calls = {"n": 0}

    def fake_resolve(name, rtype, lifetime=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return [_Rdata("edge.herokuapp.com.")]
        raise dns.resolver.NXDOMAIN()

    monkeypatch.setattr(dns.resolver, "resolve", fake_resolve)
    assert resolve_cname_chain("www.example.com") == ["edge.herokuapp.com"]


def test_detect_takeover_returns_empty_without_cname(monkeypatch):
    monkeypatch.setattr(takeover, "resolve_cname_chain", lambda d, max_depth=10: [])
    assert detect_takeover("example.com") == []


def test_detect_takeover_cname_only(monkeypatch):
    monkeypatch.setattr(takeover, "resolve_cname_chain",
                        lambda d, max_depth=10: ["foo.github.io"])

    findings = detect_takeover("www.example.com")
    assert len(findings) == 1
    finding = findings[0]
    assert finding["rule"] == "subdomain-takeover"
    assert finding["target_uid"] == "domain:www.example.com"
    assert finding["evidence"]["confidence"] == "cname"
    assert finding["evidence"]["cname_chain"] == ["foo.github.io"]


def test_detect_takeover_confirmed_by_http(monkeypatch):
    monkeypatch.setattr(takeover, "resolve_cname_chain",
                        lambda d, max_depth=10: ["foo.github.io"])

    def http_get(url, timeout):
        return SimpleNamespace(status_code=404, text="There isn't a GitHub Pages site here.")

    findings = detect_takeover("www.example.com", http_get=http_get, timeout=3)
    assert findings[0]["evidence"]["confidence"] == "confirmed"
    assert findings[0]["evidence"]["http_status"] == 404


def test_execute_adds_takeover_finding(monkeypatch):
    monkeypatch.setattr(takeover, "resolve_cname_chain",
                        lambda d, max_depth=10: ["foo.github.io"])

    breeder = DomainBreeder({"takeover": {"enabled": True, "http_probe": False}}, None)
    monkeypatch.setattr(breeder, "_discover_subdomains", lambda d, tm: [])
    monkeypatch.setattr(breeder, "_resolve_domain", lambda d: [])
    monkeypatch.setattr(breeder, "_probe_certificate", lambda d, port=443: None)

    asset = DomainAsset("www.example.com", source="seed", depth=0)
    breeder.execute(asset, None)

    rules = {f["rule"] for f in asset.properties.get("findings", [])}
    assert "subdomain-takeover" in rules
