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
