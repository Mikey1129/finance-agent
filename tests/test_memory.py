"""agent.memory 长期记忆与会话状态测试：读写/更新/失效/删除规则。"""

from agent.memory import MemoryItem, MemoryStore, SessionStore


def test_set_and_get():
    ms = MemoryStore()
    ms.set("用户偏好", "输出用中文", kind="preference")
    assert ms.get("用户偏好") == "输出用中文"


def test_get_expired_returns_none():
    ms = MemoryStore()
    ms.set("临时", "x", ttl_seconds=-1)  # 立即过期
    assert ms.get("临时") is None


def test_update_existing_and_missing():
    ms = MemoryStore()
    ms.set("k", 1)
    assert ms.update("k", 2)
    assert ms.get("k") == 2
    assert not ms.update("不存在", 3)


def test_delete_and_invalidate():
    ms = MemoryStore()
    ms.set("k", 1)
    assert ms.delete("k")
    assert ms.get("k") is None
    ms.set("k2", 2)
    assert ms.invalidate("k2")
    assert ms.get("k2") is None


def test_cleanup_expired():
    ms = MemoryStore()
    ms.set("a", 1)
    ms.set("b", 2, ttl_seconds=-1)
    assert ms.cleanup_expired() == 1
    assert ms.get("a") == 1
    assert ms.get("b") is None


def test_search_by_kind():
    ms = MemoryStore()
    ms.set("p", 1, kind="preference")
    ms.set("f", 2, kind="financial", source="fact_id=x")
    prefs = ms.search("preference")
    assert len(prefs) == 1
    assert prefs[0].key == "p"
    assert isinstance(prefs[0], MemoryItem)


def test_session_history():
    ss = SessionStore()
    ss.append("t1", "2024 营收多少")
    ss.append("t1", "那净利率呢")
    assert ss.get_history("t1") == ["2024 营收多少", "那净利率呢"]
    assert ss.get_history("t1", max_turns=1) == ["那净利率呢"]
    ss.clear("t1")
    assert ss.get_history("t1") == []
