"""agent.memory 长期记忆与会话状态测试：读写/更新/失效/删除规则。"""

from agent.memory import MemoryItem, MemoryStore, SessionStore, SqliteMemoryStore


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


# -- SqliteMemoryStore（持久化，阶段 4）------------------------------------
def test_sqlite_set_get_update_delete(tmp_path):
    m = SqliteMemoryStore(str(tmp_path / "mem.db"))
    m.set("k1", "v1", kind="preference")
    assert m.get("k1") == "v1"
    assert m.update("k1", "v2") is True
    assert m.get("k1") == "v2"
    assert m.update("不存在", "x") is False
    assert m.delete("k1") is True
    assert m.get("k1") is None


def test_sqlite_persists_across_reopen(tmp_path):
    path = str(tmp_path / "mem.db")
    SqliteMemoryStore(path).set("k", {"company": "公牛集团", "year": 2024}, kind="project")
    # 重新打开同一文件，数据仍在（跨会话持久化）
    assert SqliteMemoryStore(path).get("k") == {"company": "公牛集团", "year": 2024}


def test_sqlite_expiry_and_search(tmp_path):
    m = SqliteMemoryStore(str(tmp_path / "mem.db"))
    m.set("gone", "y", ttl_seconds=0)
    assert m.get("gone") is None  # 立即过期自动删除
    m.set("a", 1, kind="preference")
    m.set("b", 2, kind="project")
    assert {i.key for i in m.search(kind="preference")} == {"a"}
