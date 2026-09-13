"""回归测试：core/tools 不再泄漏临时文件。

历史缺陷：
  * _run_internal_dns_resolver / _run_internal_port_scanner 创建了
    NamedTemporaryFile 却从不使用、也从不删除（每次调用泄漏一个空文件）；
  * naabu / JSfinder 的临时文件清理不在 finally 中，异常路径会残留。
"""
import os
import tempfile
import types

import pytest

from core.tools import tools as tools_module
from core.tools.tools import ToolOrchestrator


@pytest.fixture
def orchestrator():
    orch = ToolOrchestrator(config={}, engine=None)
    yield orch
    orch.executor.shutdown(wait=False)


def _forbid_named_temporary_file(record):
    def _fake(*args, **kwargs):
        record.append((args, kwargs))
        raise AssertionError("该路径不应创建临时文件")
    return _fake


def test_internal_dns_resolver_creates_no_tempfile(orchestrator, monkeypatch):
    created = []
    monkeypatch.setattr(
        tools_module.tempfile, "NamedTemporaryFile", _forbid_named_temporary_file(created)
    )
    fake = types.SimpleNamespace(stdout=b"1.2.3.4\n", stderr=b"", returncode=0)
    monkeypatch.setattr(tools_module.subprocess, "run", lambda *a, **k: fake)
    monkeypatch.setattr(orchestrator, "run_free_subfinder", lambda domain: [])

    result = orchestrator._run_internal_dns_resolver("example.com")

    assert created == []
    assert isinstance(result, list)


def test_internal_port_scanner_creates_no_tempfile(orchestrator, monkeypatch):
    created = []
    monkeypatch.setattr(
        tools_module.tempfile, "NamedTemporaryFile", _forbid_named_temporary_file(created)
    )
    fake = types.SimpleNamespace(stdout=b"80\n443\n", stderr=b"", returncode=0)
    monkeypatch.setattr(tools_module.subprocess, "run", lambda *a, **k: fake)

    result = orchestrator._run_internal_port_scanner("1.2.3.4")

    assert created == []
    assert result == {80: "unknown", 443: "unknown"}


def test_naabu_tempfile_removed_even_on_error(orchestrator, monkeypatch):
    created = []
    real_ntf = tempfile.NamedTemporaryFile

    def _tracking_ntf(*args, **kwargs):
        handle = real_ntf(*args, **kwargs)
        created.append(handle.name)
        return handle

    monkeypatch.setattr(tools_module.tempfile, "NamedTemporaryFile", _tracking_ntf)
    monkeypatch.setattr(orchestrator, "_check_tool_exists", lambda name: True)
    monkeypatch.setattr(orchestrator, "_run_internal_port_scanner", lambda ip: {})
    orchestrator.tool_paths = {"naabu": "/usr/bin/naabu"}

    def _boom(*args, **kwargs):
        raise RuntimeError("naabu exploded")

    monkeypatch.setattr(tools_module.subprocess, "run", _boom)

    result = orchestrator.run_naabu("1.2.3.4")

    assert result == {}
    assert created, "naabu 应创建临时输出文件"
    assert not os.path.exists(created[0]), "临时文件必须被删除"
