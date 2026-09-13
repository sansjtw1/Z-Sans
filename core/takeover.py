#!/usr/bin/env python3
# coding: utf-8
"""子域接管（Subdomain Takeover）检测。

安全边界：本模块**仅检测**——只做 DNS CNAME 查询与（可选的）一次 HTTP GET，
绝不注册、认领或利用任何服务。证据仅记录 CNAME 链、命中的服务与响应摘要。

判定思路：
1. 解析目标的 CNAME 链；
2. 若某个 CNAME 目标命中已知云服务指纹（cname_patterns），则产生"疑似接管"；
3. 可选再发一次 HTTP 请求，若响应命中该服务的错误指纹（body/status），
   则提升为"确认"级别。
"""
import hashlib
import re

from core.i18n import _
from core.findings import (
    make_finding,
    SEVERITY_CRITICAL,
    SEVERITY_HIGH,
    SEVERITY_MEDIUM,
)

# CNAME 目标匹配 + 服务错误页指纹。fingerprints 中：
#   status  精确匹配 HTTP 状态码
#   body_re 在响应体中匹配的正则
TAKEOVER_SIGNATURES = [
    {
        "service": "GitHub Pages",
        "severity": SEVERITY_HIGH,
        "cname_patterns": [re.compile(r"github\.io$", re.I)],
        "fingerprints": [
            {"status": 404, "body_re": re.compile(r"There isn't a GitHub Pages site here", re.I)},
        ],
    },
    {
        "service": "Heroku",
        "severity": SEVERITY_HIGH,
        "cname_patterns": [re.compile(r"herokuapp\.com$", re.I),
                            re.compile(r"herokudns\.com$", re.I)],
        "fingerprints": [
            {"body_re": re.compile(r"No such app", re.I)},
            {"status": 404, "body_re": re.compile(r"herokucdn\.com/error-pages", re.I)},
        ],
    },
    {
        "service": "AWS S3",
        "severity": SEVERITY_CRITICAL,
        "cname_patterns": [re.compile(r"s3[.-][a-z0-9-]*\.amazonaws\.com$", re.I),
                            re.compile(r"\.s3\.amazonaws\.com$", re.I)],
        "fingerprints": [
            {"body_re": re.compile(r"NoSuchBucket", re.I)},
            {"body_re": re.compile(r"The specified bucket does not exist", re.I)},
        ],
    },
    {
        "service": "Azure",
        "severity": SEVERITY_HIGH,
        "cname_patterns": [re.compile(r"azurewebsites\.net$", re.I),
                            re.compile(r"cloudapp\.net$", re.I),
                            re.compile(r"cloudapp\.azure\.com$", re.I),
                            re.compile(r"azureedge\.net$", re.I)],
        "fingerprints": [
            {"body_re": re.compile(r"404 Web Site not found", re.I)},
        ],
    },
    {
        "service": "Netlify",
        "severity": SEVERITY_HIGH,
        "cname_patterns": [re.compile(r"netlify\.app$", re.I),
                            re.compile(r"netlify\.com$", re.I)],
        "fingerprints": [
            {"status": 404, "body_re": re.compile(r"Not Found - Request ID", re.I)},
        ],
    },
    {
        "service": "Vercel",
        "severity": SEVERITY_HIGH,
        "cname_patterns": [re.compile(r"vercel\.app$", re.I),
                            re.compile(r"now\.sh$", re.I)],
        "fingerprints": [
            {"status": 404, "body_re": re.compile(r"The deployment could not be found", re.I)},
            {"status": 404, "body_re": re.compile(r"DEPLOYMENT_NOT_FOUND", re.I)},
        ],
    },
    {
        "service": "Shopify",
        "severity": SEVERITY_MEDIUM,
        "cname_patterns": [re.compile(r"myshopify\.com$", re.I)],
        "fingerprints": [
            {"body_re": re.compile(r"Sorry, this shop is currently unavailable", re.I)},
        ],
    },
    {
        "service": "Fastly",
        "severity": SEVERITY_MEDIUM,
        "cname_patterns": [re.compile(r"fastly\.net$", re.I)],
        "fingerprints": [
            {"body_re": re.compile(r"Fastly error: unknown domain", re.I)},
        ],
    },
    {
        "service": "Pantheon",
        "severity": SEVERITY_MEDIUM,
        "cname_patterns": [re.compile(r"pantheonsite\.io$", re.I)],
        "fingerprints": [
            {"status": 404, "body_re": re.compile(r"The gods are wise, but do not know of the site", re.I)},
        ],
    },
    {
        "service": "Surge.sh",
        "severity": SEVERITY_MEDIUM,
        "cname_patterns": [re.compile(r"surge\.sh$", re.I)],
        "fingerprints": [
            {"body_re": re.compile(r"project not found", re.I)},
        ],
    },
    {
        "service": "Bitbucket",
        "severity": SEVERITY_MEDIUM,
        "cname_patterns": [re.compile(r"bitbucket\.io$", re.I)],
        "fingerprints": [
            {"body_re": re.compile(r"Repository not found", re.I)},
        ],
    },
    {
        "service": "Tumblr",
        "severity": SEVERITY_MEDIUM,
        "cname_patterns": [re.compile(r"domains\.tumblr\.com$", re.I)],
        "fingerprints": [
            {"body_re": re.compile(r"Whatever you were looking for doesn't currently exist", re.I)},
        ],
    },
    {
        "service": "Zendesk",
        "severity": SEVERITY_MEDIUM,
        "cname_patterns": [re.compile(r"zendesk\.com$", re.I)],
        "fingerprints": [
            {"body_re": re.compile(r"Help Center Closed", re.I)},
        ],
    },
    {
        "service": "WordPress.com",
        "severity": SEVERITY_MEDIUM,
        "cname_patterns": [re.compile(r"wordpress\.com$", re.I)],
        "fingerprints": [
            {"body_re": re.compile(r"Do you want to register", re.I)},
        ],
    },
]


def resolve_cname_chain(domain, max_depth=10):
    """返回从 domain 出发的 CNAME 链（已去尾点、小写）。无 CNAME 返回 []。"""
    chain = []
    current = (domain or "").lower().rstrip('.')
    seen = set()
    try:
        import dns.resolver
    except Exception:
        return chain

    for _ in range(max_depth):
        if not current or current in seen:
            break
        seen.add(current)
        try:
            answers = dns.resolver.resolve(current, 'CNAME', lifetime=5.0)
        except Exception:
            break
        target = None
        for rdata in answers:
            target = str(getattr(rdata, 'target', rdata)).rstrip('.').lower()
            break
        if not target:
            break
        chain.append(target)
        current = target
    return chain


def match_signature(cname_chain):
    """在 CNAME 链上匹配已知服务的接管指纹，返回命中的签名或 None。"""
    for target in cname_chain or []:
        for signature in TAKEOVER_SIGNATURES:
            for pattern in signature["cname_patterns"]:
                if pattern.search(target):
                    return signature
    return None


def _probe_http(domain, http_get, timeout):
    """尝试 https/http 各一次，返回 (response, scheme) 或 (None, None)。"""
    for scheme in ("https", "http"):
        try:
            response = http_get("{0}://{1}".format(scheme, domain), timeout)
            if response is not None:
                return response, scheme
        except Exception:
            continue
    return None, None


def detect_takeover(domain, http_get=None, timeout=8):
    """检测 domain 是否存在子域接管风险，返回 finding 列表（可能为空）。

    仅检测：CNAME 查询 + 可选一次 HTTP GET；不会对目标做任何写操作。
    """
    chain = resolve_cname_chain(domain)
    if not chain:
        return []
    signature = match_signature(chain)
    if signature is None:
        return []

    target_uid = "domain:{0}".format(domain)
    evidence = {
        "cname_chain": chain,
        "service": signature["service"],
    }
    confidence = "cname"

    if http_get is not None and signature.get("fingerprints"):
        response, scheme = _probe_http(domain, http_get, timeout)
        if response is not None:
            status = getattr(response, "status_code", None)
            try:
                body = response.text or ""
            except Exception:
                body = ""
            for fingerprint in signature["fingerprints"]:
                expected_status = fingerprint.get("status")
                body_re = fingerprint.get("body_re")
                if expected_status is not None and status != expected_status:
                    continue
                if body_re is not None and not body_re.search(body[:200000]):
                    continue
                confidence = "confirmed"
                break
            evidence["http_status"] = status
            evidence["scheme"] = scheme
            evidence["body_sha256"] = hashlib.sha256(body.encode("utf-8", "replace")).hexdigest()[:16]

    evidence["confidence"] = confidence
    title = _("Possible subdomain takeover ({service})").format(service=signature["service"])
    return [make_finding(
        "subdomain-takeover",
        signature.get("severity", SEVERITY_HIGH),
        title,
        evidence,
        target_uid,
    )]
