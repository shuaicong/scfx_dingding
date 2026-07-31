"""同步引擎写入入口测试"""
import hashlib
import time
from datetime import datetime
from unittest import mock

import pytest

import sync.engine as engine_module
from sync.engine import SyncEngine
from storage.tracker import SyncTracker


@pytest.fixture
def engine(tmp_path):
    """构造注入 mock 的 SyncEngine，绕过真实网络与数据库"""
    tracker = SyncTracker(str(tmp_path / "t.db"))
    eng = SyncEngine.__new__(SyncEngine)
    eng.tracker = tracker
    eng.dingtalk = mock.MagicMock()
    eng.price_index = mock.MagicMock()
    eng.crawler = mock.MagicMock()
    eng._price_index_folder_id = None
    eng._big_data_folder_id = None
    eng._huanan_folder_id = None
    return eng


def _set_monthly_count(engine, n: int):
    month = datetime.now().strftime("%Y-%m")
    engine.tracker.meta_set(f"monthly_write_count:{month}", str(n))


def test_skips_when_content_unchanged(engine):
    engine.tracker.mark_synced("a", "标题", "docKey1")
    content = "同一内容"
    engine.tracker.record_content_hash(
        "a", hashlib.sha1(content.encode("utf-8")).hexdigest())
    written = engine._write_if_changed("docKey1", content, "a")
    assert written is False
    engine.dingtalk.overwrite_content.assert_not_called()


def test_writes_when_content_changed(engine, monkeypatch):
    monkeypatch.setattr(engine_module, "DAILY_WRITE_COOLDOWN_HOURS", 0)
    engine.tracker.mark_synced("a", "标题", "docKey1")
    engine.tracker.record_content_hash("a", "old_hash")
    written = engine._write_if_changed("docKey1", "新内容", "a")
    assert written is True
    engine.dingtalk.overwrite_content.assert_called_once_with(doc_key="docKey1", content="新内容")
    assert engine.tracker.get_record("a")["content_hash"] is not None


def test_first_compare_records_hash_without_write(engine):
    engine.tracker.mark_synced("a", "标题", "docKey1")
    written = engine._write_if_changed("docKey1", "内容", "a")
    assert written is False
    engine.dingtalk.overwrite_content.assert_not_called()
    assert engine.tracker.get_record("a")["content_hash"] is not None


def test_cooldown_blocks_write(engine):
    engine.tracker.mark_synced("a", "标题", "docKey1")
    engine.tracker.record_content_hash("a", "old_hash")  # 刚刷新 synced_at
    written = engine._write_if_changed("docKey1", "新内容", "a")
    assert written is False


def test_new_article_ignores_cooldown(engine):
    engine.tracker.mark_synced("a", "标题", "docKey1")
    engine.tracker.record_content_hash("a", "old_hash")
    written = engine._write_if_changed("docKey1", "新内容", "a", is_new=True)
    assert written is True


def test_quota_blocks_write(engine, monkeypatch):
    monkeypatch.setattr(engine_module, "MONTHLY_QUOTA_LIMIT", 4800)
    _set_monthly_count(engine, 4800)
    engine.tracker.mark_synced("a", "标题", "docKey1")
    engine.tracker.record_content_hash("a", "old_hash")
    written = engine._write_if_changed("docKey1", "新内容", "a")
    assert written is False
    engine.dingtalk.overwrite_content.assert_not_called()


def test_quota_bumps_on_write(engine, monkeypatch):
    monkeypatch.setattr(engine_module, "DAILY_WRITE_COOLDOWN_HOURS", 0)
    engine.tracker.mark_synced("a", "标题", "docKey1")
    engine.tracker.record_content_hash("a", "old_hash")
    engine._write_if_changed("docKey1", "新内容", "a")
    assert engine.tracker.get_monthly_write_count() == 1


def test_write_if_changed_routes_price_index_branch(engine, monkeypatch):
    """price_index: 前缀的记录走 price_index_records 表，哈希比对独立生效"""
    engine.tracker.mark_price_index_synced("price_index:1", "标题", "docKey_pi")
    content = "价格内容"
    engine.tracker.record_price_index_hash(
        "price_index:1", hashlib.sha1(content.encode("utf-8")).hexdigest())
    # 内容未变化 → 跳过，且不会调用普通 sync_records 的方法
    assert engine._write_if_changed("docKey_pi", content, "price_index:1") is False
    engine.dingtalk.overwrite_content.assert_not_called()
    # 内容变化且关闭冷却 → 写入 price_index 记录
    engine.tracker.record_price_index_hash("price_index:1", "old_pi_hash")
    monkeypatch.setattr(engine_module, "DAILY_WRITE_COOLDOWN_HOURS", 0)
    assert engine._write_if_changed("docKey_pi", "新价格内容", "price_index:1") is True
    engine.dingtalk.overwrite_content.assert_called_once_with(doc_key="docKey_pi", content="新价格内容")
    assert engine.tracker.get_price_index_doc("price_index:1")["content_hash"] is not None


def test_pending_hash_triggers_backfill_write(engine, monkeypatch):
    """创建时配额满的记录以 PENDING_HASH 占位，配额恢复后应补写内容"""
    monkeypatch.setattr(engine_module, "DAILY_WRITE_COOLDOWN_HOURS", 24)
    engine.tracker.mark_price_index_synced(
        "price_index:1", "标题", "docKey_pi",
        content_hash=engine_module.PENDING_HASH)
    written = engine._write_if_changed("docKey_pi", "新内容", "price_index:1")
    assert written is True
    engine.dingtalk.overwrite_content.assert_called_once_with(doc_key="docKey_pi", content="新内容")
    assert engine.tracker.get_price_index_doc("price_index:1")["content_hash"] != engine_module.PENDING_HASH


def test_price_index_folder_cached_in_meta(engine):
    engine.dingtalk.find_or_create_folder.return_value = "folderId"
    fid = engine._ensure_price_index_folder()
    assert fid == "folderId"
    engine.dingtalk.find_or_create_folder.assert_called_once()
    engine.dingtalk.find_or_create_folder.reset_mock()
    assert engine._ensure_price_index_folder() == "folderId"
    engine.dingtalk.find_or_create_folder.assert_not_called()
    assert engine.tracker.meta_get("folder_node_id:price_index") == "folderId"


def test_big_data_folder_cached_in_meta(engine):
    engine.dingtalk.find_or_create_folder.return_value = "bigFolder"
    assert engine._ensure_big_data_folder() == "bigFolder"
    engine.dingtalk.find_or_create_folder.reset_mock()
    assert engine._ensure_big_data_folder() == "bigFolder"
    engine.dingtalk.find_or_create_folder.assert_not_called()


def test_price_index_folder_read_from_meta_on_fresh_instance(engine):
    """新 SyncEngine 实例（内存缓存为空）应命中 meta，不再触发网络查询"""
    engine.dingtalk.find_or_create_folder.return_value = "folderId"
    engine._ensure_price_index_folder()
    fresh = SyncEngine.__new__(SyncEngine)
    fresh.tracker = engine.tracker
    fresh.dingtalk = mock.MagicMock()
    fresh._price_index_folder_id = None
    fresh._big_data_folder_id = None
    fresh._huanan_folder_id = None
    assert fresh._ensure_price_index_folder() == "folderId"
    fresh.dingtalk.find_or_create_folder.assert_not_called()


def test_relearn_cooldown_skips(engine, monkeypatch):
    monkeypatch.setattr(engine_module, "trigger_knowledge_relearn",
                        mock.MagicMock(return_value={"success": True, "response": "ok"}))
    engine.tracker.meta_set("relearn_last_trigger", str(time.time()))
    engine._trigger_relearn()
    engine_module.trigger_knowledge_relearn.assert_not_called()


def test_relearn_triggered_after_cooldown(engine, monkeypatch):
    monkeypatch.setattr(engine_module, "trigger_knowledge_relearn",
                        mock.MagicMock(return_value={"success": True, "response": "ok"}))
    engine.tracker.meta_set("relearn_last_trigger", str(time.time() - 25 * 3600))
    engine._trigger_relearn()
    engine_module.trigger_knowledge_relearn.assert_called_once()
    assert engine.tracker.meta_get("relearn_last_trigger") is not None


def test_sync_one_skips_when_quota_full(engine):
    _set_monthly_count(engine, 4000)
    written = engine._sync_one("1", "标题")
    assert written is False
    engine.dingtalk.create_document.assert_not_called()
    engine.dingtalk.overwrite_content.assert_not_called()


def test_sync_one_writes_when_quota_available(engine):
    engine.crawler.get_article_content.return_value = "内容"
    engine.dingtalk.create_document.return_value = {"docKey": "k1", "url": "u1"}
    written = engine._sync_one("1", "标题")
    assert written is True
    engine.dingtalk.overwrite_content.assert_called_once()
    assert engine.tracker.get_monthly_write_count() == 1
    assert engine.tracker.get_record("1")["content_hash"] is not None
