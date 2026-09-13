"""Phase 6 回归测试：Neo4j CSV、SARIF 导出与报告 Findings 页签。"""
import csv
import json
import os

import pytest

from core.findings import make_finding, SEVERITY_HIGH, SEVERITY_MEDIUM
from main import BreedingEngine


def _engine(tmp_path):
    config = {
        "plugins": {"dir": str(tmp_path / "plugins")},
        "output": {"dir": str(tmp_path / "output"), "output_prefix": "t"},
        "max_depth": 5,
        "resource_limits": {"max_domains": 100},
    }
    return BreedingEngine(config=config, register_signals=False)


def _seed_findings(engine):
    from core.zsans_engine import DomainAsset
    asset = DomainAsset("example.com", source="seed", depth=0)
    asset.properties.setdefault("findings", [])
    asset.properties["findings"].append(make_finding(
        "subdomain-takeover", SEVERITY_HIGH, "Possible subdomain takeover (GitHub Pages)",
        {"cname_chain": ["foo.github.io"]}, "domain:example.com"))
    asset.properties["findings"].append(make_finding(
        "cert-expiring", SEVERITY_MEDIUM, "TLS certificate is expiring soon",
        {"days_left": 10}, "domain:example.com"))
    engine.asset_graph.add_asset(asset)
    return asset


def test_export_sarif(tmp_path):
    engine = _engine(tmp_path)
    try:
        _seed_findings(engine)
        path = engine.output_handler._export_sarif("base")

        data = json.load(open(path, encoding="utf-8"))
        assert data["version"] == "2.1.0"
        assert data["$schema"].endswith("sarif-2.1.0.json")
        driver = data["runs"][0]["tool"]["driver"]
        assert driver["name"] == "Z-Sans"
        assert {r["id"] for r in driver["rules"]} == {"subdomain-takeover", "cert-expiring"}

        results = {r["ruleId"]: r for r in data["runs"][0]["results"]}
        assert results["subdomain-takeover"]["level"] == "error"
        assert results["cert-expiring"]["level"] == "warning"
        assert results["subdomain-takeover"]["locations"][0]["logicalLocations"][0]["fullyQualifiedName"] == "domain:example.com"
    finally:
        engine.tool_orchestrator.executor.shutdown(wait=False)


def test_export_neo4j_csv(tmp_path):
    engine = _engine(tmp_path)
    try:
        _seed_findings(engine)
        nodes_file, rels_file = engine.output_handler._export_neo4j_csv("base")

        with open(nodes_file, encoding="utf-8-sig", newline="") as f:
            rows = list(csv.reader(f))
        assert rows[0] == ["uid:ID", ":LABEL", "value", "type", "depth", "state", "source", "findings_count"]
        node = {r[0]: r for r in rows[1:]}
        assert node["domain:example.com"][1] == "domain"
        assert node["domain:example.com"][7] == "2"

        with open(rels_file, encoding="utf-8-sig", newline="") as f:
            rel_rows = list(csv.reader(f))
        assert rel_rows[0] == [":START_ID", ":END_ID", ":TYPE", "relation"]
    finally:
        engine.tool_orchestrator.executor.shutdown(wait=False)


def test_csv_has_findings_column(tmp_path):
    engine = _engine(tmp_path)
    try:
        _seed_findings(engine)
        assets_file, _rels = engine.output_handler._export_csv("base")
        with open(assets_file, encoding="utf-8-sig", newline="") as f:
            rows = list(csv.reader(f))
        assert "Findings" in rows[0]
        findings_col = rows[0].index("Findings")
        row = {r[0]: r for r in rows[1:]}["domain:example.com"]
        assert "subdomain-takeover" in row[findings_col]
    finally:
        engine.tool_orchestrator.executor.shutdown(wait=False)


def test_report_contains_findings_tab(tmp_path):
    engine = _engine(tmp_path)
    try:
        _seed_findings(engine)
        path = engine.output_handler._generate_report("base")
        html = open(path, encoding="utf-8").read()
        assert "tab-findings" in html
        assert "subdomain-takeover" in html
    finally:
        engine.tool_orchestrator.executor.shutdown(wait=False)


def test_generate_output_registers_new_formats(tmp_path):
    engine = _engine(tmp_path)
    try:
        _seed_findings(engine)
        results = engine.output_handler.generate_output(formats=["json", "sarif", "neo4j"])
        assert "sarif" in results
        assert "neo4j" in results
        assert os.path.exists(results["sarif"])
        assert all(os.path.exists(p) for p in results["neo4j"])
    finally:
        engine.tool_orchestrator.executor.shutdown(wait=False)


def test_list_projects_detects_sarif(tmp_path):
    import webapp
    output = tmp_path / "output"
    proj = output / "20260101_000000"
    proj.mkdir(parents=True)
    (proj / "t_20260101_000000.sarif").write_text("{}", encoding="utf-8")
    (proj / "t_20260101_000000.json").write_text("{}", encoding="utf-8")

    projects = webapp.list_projects(str(output))
    assert len(projects) == 1
    assert projects[0]["has_sarif"] is True
    assert projects[0]["sarif_file"].endswith(".sarif")
    assert webapp.find_project_file(str(output), "20260101_000000", ".sarif") is not None
    assert webapp.find_project_file(str(output), "../etc", ".sarif") is None
