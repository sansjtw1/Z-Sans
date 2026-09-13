"""回归测试：重复发现的资产仍必须记录「发现」边与事件。

历史缺陷：main.py 中 add_edge / emit_hook 被 add_asset 的返回值 gate。
当被发现的资产已处于 scanned / eliminated / scanning 状态时，add_asset 返回
False，于是边与 on_asset_discovered 事件被一并跳过，造成拓扑缺边、事件丢失。
"""
from core.zsans_engine import DomainAsset
from main import BreedingEngine


class _StaticBreeder:
    """不做任何真实探测，直接返回预置的「新发现」资产。"""

    def __init__(self, produced):
        self._produced = produced

    def execute(self, asset, tool_orchestrator):
        return list(self._produced)


def _make_engine(plugins_dir):
    config = {
        "plugins": {"dir": str(plugins_dir)},
        "asset_types": {"domain": {"enabled": True}},
        "max_depth": 5,
        "resource_limits": {"max_domains": 1000},
    }
    return BreedingEngine(config=config, register_signals=False)


def _shutdown(engine):
    orchestrator = getattr(engine, "tool_orchestrator", None)
    if orchestrator is not None and getattr(orchestrator, "executor", None) is not None:
        orchestrator.executor.shutdown(wait=False)


def test_rediscovery_of_scanned_asset_records_edge(tmp_path):
    engine = _make_engine(tmp_path)
    try:
        parent = DomainAsset("parent.example.com", source="manual", depth=0)
        child = DomainAsset("scanned.example.com", source="other", depth=1)
        engine.asset_graph.add_asset(parent)
        engine.asset_graph.add_asset(child)
        child.state = "scanned"  # 已被其它父节点扫描完成

        engine.breeder_factory.get_breeder = lambda *a, **k: _StaticBreeder([child])

        seen = []
        engine.register_hook("on_asset_discovered", lambda **kw: seen.append(kw["asset"].uid))

        queued_before = engine.queue.size()
        engine._process_asset(parent)

        # 关键：即使 add_asset 返回 False，发现边也必须存在
        assert (parent.uid, child.uid) in engine.asset_graph.edges
        assert child.uid in seen
        # 已扫描完成的资产不应被重新入队
        assert engine.queue.size() == queued_before
    finally:
        _shutdown(engine)


def test_new_asset_records_edge_and_is_queued(tmp_path):
    engine = _make_engine(tmp_path)
    try:
        parent = DomainAsset("parent.example.com", source="manual", depth=0)
        engine.asset_graph.add_asset(parent)
        fresh = DomainAsset("fresh.example.com", source="parent", depth=1)
        engine.breeder_factory.get_breeder = lambda *a, **k: _StaticBreeder([fresh])

        engine._process_asset(parent)

        assert (parent.uid, fresh.uid) in engine.asset_graph.edges
        assert engine.queue.size() == 1
    finally:
        _shutdown(engine)


def test_discovery_stops_at_type_limit(tmp_path):
    # max_domains=2：父节点占 1，3 个新子域里只有 1 个能入库
    config = {
        "plugins": {"dir": str(tmp_path)},
        "asset_types": {"domain": {"enabled": True}},
        "max_depth": 5,
        "resource_limits": {"max_domains": 2},
    }
    engine = BreedingEngine(config=config, register_signals=False)
    try:
        parent = DomainAsset("parent.example.com", source="manual", depth=0)
        engine.asset_graph.try_add_asset(parent)
        children = [DomainAsset("c{0}.example.com".format(i), source="parent", depth=1) for i in range(3)]
        engine.breeder_factory.get_breeder = lambda *a, **k: _StaticBreeder(children)

        engine._process_asset(parent)

        assert engine.asset_graph.type_counts["domain"] == 2
        assert len(engine.asset_graph.nodes) == 2
        rejected = [c for c in children if c.state == "excluded"]
        assert len(rejected) == 2
        assert all(c.properties.get("excluded_reason") for c in rejected)
    finally:
        _shutdown(engine)

