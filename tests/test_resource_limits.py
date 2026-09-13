"""回归测试：类型配额（max_domains 等）必须在锁内原子占位。

历史缺陷：main.py 先调 _type_capacity_reached() 读计数、再 add_asset() 自增，
两步之间没有锁保护；并发 worker 会同时通过检查，导致类型数量最多超发
（worker 数）个。
"""
import threading

from core.zsans_engine import AssetGraph, DomainAsset


def test_try_add_asset_enforces_limit_atomically():
    graph = AssetGraph()
    limit = 10
    total = 60
    results = []
    results_lock = threading.Lock()

    def worker(index):
        asset = DomainAsset("d{0}.example.com".format(index))
        status = graph.try_add_asset(asset, limit)
        with results_lock:
            results.append(status)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(total)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert results.count("added") == limit
    assert results.count("rejected") == total - limit
    assert graph.type_counts["domain"] == limit
    assert len(graph.nodes) == limit


def test_rejected_asset_is_marked_excluded_with_reason():
    graph = AssetGraph()
    graph.try_add_asset(DomainAsset("a.example.com"), 1)

    rejected = DomainAsset("b.example.com")
    assert graph.try_add_asset(rejected, 1) == "rejected"
    assert rejected.state == "excluded"
    assert rejected.properties.get("excluded_reason")
    assert "b.example.com" not in {a.value for a in graph.nodes.values()}


def test_add_asset_boolean_wrapper_still_works():
    graph = AssetGraph()
    asset = DomainAsset("a.example.com")

    assert graph.add_asset(asset) is True
    asset.state = "scanned"
    assert graph.add_asset(asset) is False


def test_no_limit_allows_unbounded_add():
    graph = AssetGraph()
    for i in range(5):
        assert graph.try_add_asset(DomainAsset("d{0}.example.com".format(i))) == "added"
    assert graph.type_counts["domain"] == 5
