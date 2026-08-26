#!/usr/bin/env python3
# coding: utf-8

import os
import threading

logger = None  # 延迟初始化，避免循环导入；见 _log()


def _log(level, msg):
    global logger
    if logger is None:
        try:
            import logging
            logger = logging.getLogger('zsans.domain_utils')
        except Exception:
            return
    getattr(logger, level, logger.debug)(msg)


_DATA_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), 'data', 'public_suffix_list.dat')

# 内置兜底表：仅当捆绑 PSL 缺失且无第三方库时使用。
# 覆盖常见多段公共后缀，未知多段后缀按普通两段处理（与旧行为一致）。
_FALLBACK_MULTI_SUFFIXES = {
    # China
    'com.cn', 'net.cn', 'org.cn', 'gov.cn', 'edu.cn', 'ac.cn', 'mil.cn',
    'com.hk', 'org.hk', 'edu.hk', 'gov.hk', 'net.hk', 'idv.hk',
    'com.tw', 'org.tw', 'edu.tw', 'gov.tw', 'net.tw', 'idv.tw',
    'com.mo', 'net.mo', 'org.mo', 'edu.mo', 'gov.mo',
    # Japan / Korea / Singapore / India
    'co.jp', 'ne.jp', 'or.jp', 'ac.jp', 'go.jp', 'ad.jp', 'ed.jp', 'gr.jp', 'lg.jp',
    'co.kr', 'ne.kr', 'or.kr', 'ac.kr', 'go.kr', 're.kr', 'pe.kr',
    'com.sg', 'net.sg', 'org.sg', 'edu.sg', 'gov.sg', 'per.sg',
    'co.in', 'net.in', 'org.in', 'gen.in', 'firm.in', 'ac.in', 'edu.in', 'res.in', 'gov.in', 'nic.in',
    # UK / Ireland / Israel
    'co.uk', 'org.uk', 'me.uk', 'ltd.uk', 'plc.uk', 'net.uk', 'sch.uk', 'ac.uk', 'gov.uk', 'nhs.uk', 'police.uk',
    'co.ie', 'org.il', 'net.il', 'ac.il', 'gov.il',
    # Oceania
    'com.au', 'net.au', 'org.au', 'edu.au', 'gov.au', 'asn.au', 'id.au',
    'co.nz', 'net.nz', 'org.nz', 'govt.nz', 'ac.nz', 'school.nz', 'geek.nz', 'gen.nz', 'maori.nz',
    'com.fj', 'org.pg',
    # Americas / Africa
    'com.br', 'net.br', 'org.br', 'gov.br', 'edu.br', 'mil.br', 'art.br', 'blog.br',
    'com.mx', 'org.mx', 'net.mx', 'edu.mx', 'gob.mx',
    'com.ar', 'net.ar', 'org.ar', 'gob.ar', 'edu.ar',
    'com.co', 'net.co', 'org.co', 'gov.co', 'edu.co',
    'com.pe', 'net.pe', 'org.pe', 'edu.pe', 'gob.pe',
    'com.ve', 'net.ve', 'org.ve', 'gob.ve', 'edu.ve',
    'com.uy', 'net.uy', 'org.uy', 'gub.uy', 'edu.uy',
    'com.tr', 'net.tr', 'org.tr', 'gov.tr', 'edu.tr',
    'co.za', 'org.za', 'net.za', 'gov.za', 'ac.za', 'web.za',
    'com.ng', 'org.ng', 'net.ng', 'gov.ng', 'edu.ng',
    'com.eg', 'net.eg', 'org.eg', 'gov.eg', 'edu.eg',
    'co.ke', 'or.ke', 'ne.ke', 'go.ke', 'ac.ke',
    'com.pk', 'net.pk', 'org.pk', 'gov.pk', 'edu.pk',
    'com.bd', 'net.bd', 'org.bd', 'gov.bd', 'edu.bd',
    'com.th', 'net.th', 'org.th', 'go.th', 'ac.th', 'in.th', 'mi.th',
    'com.my', 'net.my', 'org.my', 'gov.my', 'edu.my',
    'com.vn', 'net.vn', 'org.vn', 'gov.vn', 'edu.vn',
    'com.ph', 'net.ph', 'org.ph', 'gov.ph', 'edu.ph',
    'co.id', 'net.id', 'or.id', 'web.id', 'ac.id', 'go.id', 'sch.id',
    # Europe misc
    'com.es', 'org.es', 'gob.es', 'edu.es',
    'com.pt', 'org.pt', 'edu.pt', 'gov.pt',
    'com.pl', 'net.pl', 'org.pl', 'gov.pl', 'edu.pl', 'waw.pl', 'biz.pl',
    'com.gr', 'net.gr', 'org.gr', 'gov.gr', 'edu.gr',
    'co.at', 'or.at', 'ac.at', 'gv.at',
    'co.hu', 'org.hu', 'info.hu', '2000.hu',
    'com.ru', 'net.ru', 'org.ru', 'gov.ru', 'msk.ru', 'spb.ru',
    'co.ua', 'kiev.ua', 'net.ua', 'org.ua', 'gov.ua', 'edu.ua',
    'com.ua',
}

_lock = threading.Lock()
_exact = set()          # 精确规则：'com', 'co.uk', ...
_wildcards = set()      # 通配规则的父集：'*.ck' -> 记录 'ck'
_exceptions = set()     # 例外规则：'!www.ck' -> 记录 'www.ck'
_source = None          # 数据来源描述


def _to_ascii(value):
    """IDN/Unicode 域名转 ASCII 小写；失败原样小写返回。"""
    v = value.strip().lower().rstrip('.')
    if not v:
        return v
    if all(ord(c) < 128 for c in v):
        return v
    try:
        return v.encode('idna').decode('ascii').lower()
    except Exception:
        return v


def _parse_psl_file(path):
    exact, wild, exc = set(), set(), set()
    with open(path, 'r', encoding='utf-8') as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith('//'):
                continue
            rule = line.split()[0].lower()
            if rule.startswith('!'):
                exc.add(_to_ascii(rule[1:]))
            elif rule.startswith('*.'):
                parent = _to_ascii(rule[2:])
                if parent:
                    wild.add(parent)
            else:
                exact.add(_to_ascii(rule))
    return exact, wild, exc


def _try_load():
    """加载规则集。返回 (exact, wildcards, exceptions, source)。"""
    # 1) 捆绑 PSL 文件（版本随仓库固定，行为可复现）
    if os.path.isfile(_DATA_FILE):
        try:
            e, w, x = _parse_psl_file(_DATA_FILE)
            return e, w, x, 'bundled public_suffix_list.dat'
        except Exception as exc:
            _log('warning', 'Failed to parse bundled PSL file: %s' % exc)
    # 2) 第三方库
    try:
        from publicsuffixlist import PublicSuffixList  # noqa: F401
        # 该库自带数据但接口是整体查询；为统一算法仍用其数据文件不可行，
        # 直接退回库的 privatesuffix 由 get_registrable_domain 分支处理。
        return None, None, None, 'publicsuffixlist'
    except ImportError:
        pass
    try:
        import publicsuffix2  # noqa: F401
        return None, None, None, 'publicsuffix2'
    except ImportError:
        pass
    # 3) 兜底表
    e = {s.encode('idna').decode('ascii') if not s.isascii() else s
         for s in _FALLBACK_MULTI_SUFFIXES}
    return e, set(), set(), 'built-in fallback table'


def _ensure_loaded():
    global _exact, _wildcards, _exceptions, _source
    if _source is not None:
        return
    with _lock:
        if _source is not None:
            return
        e, w, x, src = _try_load()
        if e is None:
            # 第三方库模式：不填充规则表，由查询函数直接走库实现
            _source = src
            return
        _exact, _wildcards, _exceptions, _source = e, w, x, src
        _log('debug', 'Public suffix rules loaded from %s (%d rules)' % (src, len(e)))


def _is_ip_literal(value):
    import ipaddress
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


def _public_suffix_len(labels):
    """按 PSL 匹配算法返回公共后缀的标签数。

    规则：默认规则 '*'（1 个标签）；精确/通配取最长匹配；例外规则减去最左标签。
    """
    n = len(labels)
    best = 1  # 无任何规则命中时按默认规则 '*' 处理
    for i in range(n):
        cand = '.'.join(labels[i:])
        part_len = n - i
        if cand in _exceptions:
            return part_len - 1  # 例外规则：去掉最左标签即为公共后缀
        if cand in _exact and part_len > best:
            best = part_len
        if part_len >= 2:
            parent = '.'.join(labels[i + 1:])
            if parent in _wildcards and part_len > best:
                best = part_len  # '*.ck' 命中 X.ck，通配符消耗一个标签
    return best


def _registrable_via_rules(domain):
    labels = domain.split('.')
    slen = _public_suffix_len(labels)
    if len(labels) <= slen:
        return None  # 域名本身就是公共后缀（如 edu.cn），没有注册域
    return '.'.join(labels[-(slen + 1):])


def get_registrable_domain(domain):
    """返回域名的注册域（eTLD+1）；无法确定或域名本身即公共后缀时返回 None。

    示例：
      www.jiyu.com    -> jiyu.com
      beijing.edu.cn  -> beijing.edu.cn   （edu.cn 是公共后缀，不再上拆）
      a.b.example.com -> example.com
      example.com     -> example.com
      edu.cn          -> None             （本身是公共后缀）
      1.2.3.4         -> None             （IP 字面量）
    """
    global _exact, _wildcards, _exceptions, _source
    d = _to_ascii(domain or '')
    if not d or len(d) > 253 or _is_ip_literal(d):
        return None
    if any(not lbl for lbl in d.split('.')):
        return None

    _ensure_loaded()

    if _source in ('publicsuffixlist', 'publicsuffix2'):
        try:
            if _source == 'publicsuffixlist':
                from publicsuffixlist import PublicSuffixList
                reg = PublicSuffixList().privatesuffix(d)
                return reg.lower() if reg else None
            from publicsuffix2 import get_sld
            reg = get_sld(d)
            return reg.lower() if reg else None
        except Exception as exc:
            _log('warning', 'Third-party PSL library failed (%s), using fallback table' % exc)
            # 库失败：就地降级到兜底表，避免反复尝试
            with _lock:
                _exact = set(_FALLBACK_MULTI_SUFFIXES)
                _wildcards, _exceptions = set(), set()
                _source = 'built-in fallback table'
            return _registrable_via_rules(d)

    return _registrable_via_rules(d)


def get_data_source():
    """返回当前使用的规则来源（诊断用）。"""
    _ensure_loaded()
    return _source
