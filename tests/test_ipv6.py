"""Phase 1 回归测试：IPv6 全链路（校验、作用域、资产 uid/端口方括号）。"""
from core.breeders.breeders import DomainBreeder
from core.zsans_engine import (
    AssetFactory, IPAsset, PortAsset, ASSET_TYPE_PORT, _normalize_ip,
)


def _breeder():
    return DomainBreeder({}, None)


def test_normalize_ip_strips_brackets_and_zone():
    assert _normalize_ip("[2001:0db8::1]") == "2001:db8::1"
    assert _normalize_ip("fe80::1%eth0") == "fe80::1"
    assert _normalize_ip("2001:0DB8::0001") == "2001:db8::1"
    assert _normalize_ip("1.2.3.4") == "1.2.3.4"


def test_normalize_ip_passthrough_invalid():
    assert _normalize_ip("not-an-ip") == "not-an-ip"


def test_is_valid_ip_accepts_v6_and_v4():
    b = _breeder()
    assert b._is_valid_ip("1.2.3.4") is True
    assert b._is_valid_ip("2001:db8::1") is True
    assert b._is_valid_ip("999.999.999.999") is False
    assert b._is_valid_ip("not-an-ip") is False


def test_ip_in_range_v6_cidr():
    b = _breeder()
    assert b._ip_in_range("2001:db8::5", "2001:db8::/32") is True
    assert b._ip_in_range("2001:dead::5", "2001:db8::/32") is False


def test_ip_in_range_preserves_legacy_v4_slash16():
    b = _breeder()
    assert b._ip_in_range("192.168.3.9", "192.168.0.0") is True
    assert b._ip_in_range("10.0.0.1", "192.168.0.0") is False


def test_ip_in_range_v6_exact():
    b = _breeder()
    assert b._ip_in_range("2001:db8::1", "2001:db8::1") is True
    assert b._ip_in_range("2001:db8::2", "2001:db8::1") is False


def test_ip_asset_uid_v4_stable_and_v6_normalized():
    assert IPAsset("1.2.3.4").uid == "ip:1.2.3.4"
    assert IPAsset("[2001:0DB8::1]").uid == "ip:2001:db8::1"


def test_port_asset_brackets_only_ipv6():
    ipv4 = PortAsset("1.2.3.4", 80)
    assert ipv4.value == "1.2.3.4:80"
    assert ipv4.uid == "port:1.2.3.4:80"

    ipv6 = PortAsset("2001:0DB8::1", 443)
    assert ipv6.value == "[2001:db8::1]:443"
    assert ipv6.properties["ip"] == "2001:db8::1"


def test_asset_factory_parses_bracketed_ipv6_port():
    a = AssetFactory.create_asset("[2001:db8::1]:443", ASSET_TYPE_PORT)
    assert a.properties["ip"] == "2001:db8::1"
    assert a.properties["port"] == 443
    assert a.value == "[2001:db8::1]:443"


def test_asset_factory_parses_ipv4_port_unchanged():
    a = AssetFactory.create_asset("1.2.3.4:80", ASSET_TYPE_PORT)
    assert a.properties["ip"] == "1.2.3.4"
    assert a.properties["port"] == 80
    assert a.value == "1.2.3.4:80"
