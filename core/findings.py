#!/usr/bin/env python3
# coding: utf-8
"""统一的「发现（finding）」数据结构。

finding 挂在 ``asset.properties["findings"]`` 列表上，随 JSON/CSV/GraphML
自然导出，并作为 SARIF 导出的数据源。字段：

    id           稳定标识（默认为 ``<rule>:<target_uid>``，用于去重）
    rule         规则名（稳定、机器可读，如 cert-expired、subdomain-takeover）
    severity     critical / high / medium / low / info
    title        人类可读标题（已按当前语言翻译）
    evidence     证据字典（链、状态码、指纹等）
    target_uid   关联资产 uid
    detected_at  Unix 时间戳
"""
import time

SEVERITY_CRITICAL = "critical"
SEVERITY_HIGH = "high"
SEVERITY_MEDIUM = "medium"
SEVERITY_LOW = "low"
SEVERITY_INFO = "info"

# 由高到低，便于排序与 SARIF level 映射
_SEVERITY_ORDER = {
    SEVERITY_CRITICAL: 0,
    SEVERITY_HIGH: 1,
    SEVERITY_MEDIUM: 2,
    SEVERITY_LOW: 3,
    SEVERITY_INFO: 4,
}


def severity_rank(severity):
    return _SEVERITY_ORDER.get(severity, len(_SEVERITY_ORDER))


def make_finding(rule, severity, title, evidence=None, target_uid=None):
    """构造一个 finding 字典。"""
    return {
        "id": "{0}:{1}".format(rule, target_uid or ""),
        "rule": rule,
        "severity": severity,
        "title": title,
        "evidence": evidence or {},
        "target_uid": target_uid,
        "detected_at": time.time(),
    }


def add_finding(asset, finding):
    """把 finding 追加到 ``asset.properties["findings"]``，按 id 去重。

    返回 True 表示新增，False 表示已存在（不重复添加）。
    """
    findings = asset.properties.setdefault("findings", [])
    existing = {f.get("id") for f in findings if isinstance(f, dict)}
    if finding.get("id") in existing:
        return False
    findings.append(finding)
    return True


def get_findings(asset):
    """返回资产的 findings 列表（不存在则空列表）。"""
    findings = asset.properties.get("findings")
    return findings if isinstance(findings, list) else []
