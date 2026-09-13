"""回归测试：资产在「已出队、尚未置 scanning」窗口内不得被重复入队。

历史缺陷：get_next() 出队时就把 uid 从 _queued 移除，而资产状态要等
_process_asset 才置 scanning。窗口内如果别处再次发现同一资产，add_asset 仍
返回可处理，于是 queue.add 会把它二次入队，两个 worker 并发扫描同一资产。
"""
from core.zsans_engine import DomainAsset, PriorityBreedingQueue


def test_asset_not_requeued_while_in_flight():
    queue = PriorityBreedingQueue({})
    asset = DomainAsset("a.example.com")
    assert queue.add(asset) is True

    got = queue.get_next()
    assert got is asset

    # 出队但未 done：仍视为「处理中」，重复发现不得再次入队
    assert queue.add(asset) is False
    assert queue.size() == 0

    queue.done(asset)
    assert queue.add(asset) is True
    assert queue.size() == 1


def test_done_is_idempotent():
    queue = PriorityBreedingQueue({})
    asset = DomainAsset("a.example.com")
    queue.add(asset)
    queue.get_next()

    queue.done(asset)
    queue.done(asset)  # 重复调用不应抛异常
    assert queue.size() == 0


def test_duplicate_queue_add_rejected_for_queued_asset():
    queue = PriorityBreedingQueue({})
    asset = DomainAsset("a.example.com")
    assert queue.add(asset) is True
    assert queue.add(asset) is False
    assert queue.size() == 1
