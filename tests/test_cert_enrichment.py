"""Phase 3 回归测试：TLS 证书探测、SAN 提取、证书类 finding。"""
from datetime import datetime, timedelta, timezone

import core.zsans_engine as engine
import core.breeders.breeders as breeders
from core.breeders.breeders import (
    DomainBreeder, _cert_covers_domain, _name_to_str, _parse_cert_time,
)
from core.zsans_engine import DomainAsset


class _FakeSock:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeSSLSock:
    def __init__(self, der):
        self._der = der

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def getpeercert(self, binary_form=False):
        return self._der


class _FakeContext:
    def __init__(self, der):
        self._der = der

    def wrap_socket(self, sock, server_hostname=None):
        return _FakeSSLSock(self._der)


def _cert_dict(**overrides):
    base = {
        'subject': 'CN=example.com',
        'issuer': "CN=Let's Encrypt",
        'not_after': 'Jan  1 00:00:00 2035 GMT',
        'san_dns': ['example.com', 'www.example.com'],
        'san_ip': [],
        'self_signed': False,
    }
    base.update(overrides)
    return base


def test_name_to_str_and_parse_time():
    assert _name_to_str(((('commonName', 'example.com'),),)) == 'commonName=example.com'
    assert _parse_cert_time('Jan  1 00:00:00 2035 GMT') == datetime(2035, 1, 1, tzinfo=timezone.utc)
    assert _parse_cert_time('garbage') is None


def test_cert_covers_domain_wildcard():
    cert = {'san_dns': ['*.example.com'], 'subject': ''}
    assert _cert_covers_domain('a.example.com', cert) is True
    assert _cert_covers_domain('example.com', cert) is False
    assert _cert_covers_domain('a.b.example.com', cert) is False


def test_decode_cert_der_none():
    assert DomainBreeder._decode_cert_der(None) is None


def test_probe_certificate_extracts_san(monkeypatch):
    monkeypatch.setattr(breeders.ssl, 'create_default_context', lambda: _FakeContext(b'DER'))
    monkeypatch.setattr(breeders.socket, 'create_connection', lambda addr, timeout=None: _FakeSock())
    monkeypatch.setattr(
        DomainBreeder, '_decode_cert_der',
        staticmethod(lambda der: _cert_dict()),
    )

    cert = DomainBreeder({}, None)._probe_certificate('example.com')
    assert cert['san_dns'] == ['example.com', 'www.example.com']


def test_check_certificate_returns_san_without_self(monkeypatch):
    monkeypatch.setattr(DomainBreeder, '_probe_certificate',
                        lambda self, domain, port=443: _cert_dict())

    result = DomainBreeder({}, None)._check_certificate('example.com')
    assert result == ['www.example.com']


def test_probe_certificate_uses_cache(tmp_path, monkeypatch):
    calls = {'n': 0}

    def fake_decode(der):
        calls['n'] += 1
        return _cert_dict()

    monkeypatch.setattr(breeders.ssl, 'create_default_context', lambda: _FakeContext(b'DER'))
    monkeypatch.setattr(breeders.socket, 'create_connection', lambda addr, timeout=None: _FakeSock())
    monkeypatch.setattr(DomainBreeder, '_decode_cert_der', staticmethod(fake_decode))

    engine.init_http_config({'cache': {'enabled': True, 'dir': str(tmp_path), 'ttl': {'cert': 60}}})
    try:
        breeder = DomainBreeder({}, None)
        first = breeder._probe_certificate('cached.example.com')
        second = breeder._probe_certificate('cached.example.com')
        assert first == second
        assert calls['n'] == 1
    finally:
        engine.init_http_config({})


def test_cert_findings_expired_and_self_signed():
    asset = DomainAsset('example.com')
    cfg = {'expiry_warn_days': 30, 'self_signed_finding': True}
    cert = _cert_dict(not_after='Jan  1 00:00:00 2000 GMT', self_signed=True,
                      san_dns=['example.com'])

    DomainBreeder({}, None)._emit_cert_findings(asset, 'example.com', cert, cfg)
    rules = {f['rule'] for f in asset.properties['findings']}
    assert 'cert-expired' in rules
    assert 'cert-self-signed' in rules
    assert 'cert-hostname-mismatch' not in rules


def test_cert_findings_expiring_soon():
    asset = DomainAsset('example.com')
    soon = (datetime.now(timezone.utc) + timedelta(days=10)).strftime('%b %d %H:%M:%S %Y GMT')
    cert = _cert_dict(not_after=soon, san_dns=['example.com'])

    DomainBreeder({}, None)._emit_cert_findings(asset, 'example.com', cert, {'expiry_warn_days': 30})
    rules = {f['rule'] for f in asset.properties['findings']}
    assert 'cert-expiring' in rules
    assert 'cert-expired' not in rules


def test_cert_findings_hostname_mismatch():
    asset = DomainAsset('example.com')
    cert = _cert_dict(subject='CN=other.example.net', san_dns=['other.example.net'])

    DomainBreeder({}, None)._emit_cert_findings(asset, 'example.com', cert, {'expiry_warn_days': 0})
    rules = {f['rule'] for f in asset.properties['findings']}
    assert 'cert-hostname-mismatch' in rules


def test_findings_are_deduplicated():
    asset = DomainAsset('example.com')
    cert = _cert_dict(not_after='Jan  1 00:00:00 2000 GMT', san_dns=['example.com'])
    breeder = DomainBreeder({}, None)

    breeder._emit_cert_findings(asset, 'example.com', cert, {'expiry_warn_days': 30})
    breeder._emit_cert_findings(asset, 'example.com', cert, {'expiry_warn_days': 30})

    expired = [f for f in asset.properties['findings'] if f['rule'] == 'cert-expired']
    assert len(expired) == 1
