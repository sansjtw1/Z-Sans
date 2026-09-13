"""回归测试：HTTP 会话必须按线程隔离，且配置变更能传播到所有线程。

历史缺陷：core/zsans_engine.py 暴露单个全局 requests.Session 供多 worker 共享。
requests.Session 并非线程安全（cookie jar / 连接池 / 重定向状态可变），并发抓取
会产生数据竞争。
"""
import threading

import pytest

import core.zsans_engine as engine


@pytest.fixture(autouse=True)
def _reset_http_settings():
    """每个用例结束后恢复默认（无代理），避免污染其它用例。"""
    yield
    engine.set_http_proxy(None)


def test_same_thread_reuses_session():
    assert engine.get_http_session() is engine.get_http_session()


def test_other_thread_gets_its_own_session():
    main_session = engine.get_http_session()
    captured = {}

    def worker():
        captured["session"] = engine.get_http_session()

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join()

    assert captured["session"] is not main_session


def test_config_change_rebuilds_session_and_applies_proxy():
    before = engine.get_http_session()
    engine.set_http_proxy("http://127.0.0.1:3128")
    after = engine.get_http_session()

    assert after is not before
    assert after.proxies.get("http") == "http://127.0.0.1:3128"
    assert after.proxies.get("https") == "http://127.0.0.1:3128"


def test_existing_worker_sees_config_change_on_next_use():
    seen = {}

    def first_use():
        seen["first"] = engine.get_http_session()

    thread = threading.Thread(target=first_use)
    thread.start()
    thread.join()

    engine.set_http_proxy("http://127.0.0.1:9999")

    def second_use():
        seen["second"] = engine.get_http_session()

    thread2 = threading.Thread(target=second_use)
    thread2.start()
    thread2.join()

    assert seen["second"] is not seen["first"]
    assert seen["second"].proxies.get("http") == "http://127.0.0.1:9999"


def test_init_http_config_applies_verify_and_user_agent():
    engine.init_http_config({"http": {"verify_ssl": True, "user_agent": "UA-TEST"}})

    session = engine.get_http_session()

    assert session.verify is True
    assert session.headers["User-Agent"] == "UA-TEST"


def test_legacy_http_session_alias_forwards_to_thread_local():
    # 旧插件 `from core.zsans_engine import http_session` 必须继续可用
    engine.set_http_proxy("http://127.0.0.1:2222")

    assert engine.http_session.proxies.get("http") == "http://127.0.0.1:2222"
    assert engine.http_session is not engine.get_http_session()


def test_legacy_http_session_alias_is_per_thread():
    captured = {}

    def worker():
        captured["bound_get"] = engine.http_session.get

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join()

    # 不同线程通过别名拿到的是各自 Session 的绑定方法
    assert captured["bound_get"] != engine.http_session.get

