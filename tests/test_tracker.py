"""追踪器数据模型测试"""
import sqlite3
from datetime import datetime

import pytest

from storage.tracker import SyncTracker


@pytest.fixture
def tracker(tmp_path):
    return SyncTracker(str(tmp_path / "test.db"))


def test_meta_get_set(tracker):
    assert tracker.meta_get("k") is None
    tracker.meta_set("k", "v")
    assert tracker.meta_get("k") == "v"


def test_content_hash_column_exists(tracker):
    with sqlite3.connect(tracker.db_path) as conn:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(sync_records)")]
    assert "content_hash" in cols
    with sqlite3.connect(tracker.db_path) as conn:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(price_index_records)")]
    assert "content_hash" in cols


def test_old_db_migrates(tmp_path):
    db = tmp_path / "old.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE sync_records (article_id TEXT PRIMARY KEY, title TEXT NOT NULL, dingtalk_doc_key TEXT, dingtalk_url TEXT, synced_at TEXT NOT NULL, extra TEXT)")
    conn.execute("CREATE TABLE price_index_records (doc_key_id TEXT PRIMARY KEY, title TEXT NOT NULL, dingtalk_doc_key TEXT, dingtalk_url TEXT, synced_at TEXT NOT NULL)")
    conn.commit()
    conn.close()
    t = SyncTracker(str(db))
    with sqlite3.connect(str(db)) as conn:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(sync_records)")]
    assert "content_hash" in cols


def test_record_and_read_content_hash(tracker):
    tracker.mark_synced("1", "标题", "docKey", "url")
    tracker.record_content_hash("1", "abc123")
    rec = tracker.get_record("1")
    assert rec["content_hash"] == "abc123"


def test_price_index_hash(tracker):
    tracker.mark_price_index_synced("price_index:1", "标题", "docKey", "url")
    tracker.record_price_index_hash("price_index:1", "xyz")
    rec = tracker.get_price_index_doc("price_index:1")
    assert rec["content_hash"] == "xyz"


def test_mark_synced_with_hash(tracker):
    tracker.mark_synced("1", "标题", "docKey", "url", content_hash="h1")
    assert tracker.get_record("1")["content_hash"] == "h1"


def test_monthly_count(tracker):
    assert tracker.get_monthly_write_count() == 0
    tracker.bump_monthly_write_count()
    tracker.bump_monthly_write_count()
    assert tracker.get_monthly_write_count() == 2


def test_within_cooldown(tracker):
    tracker.mark_synced("1", "标题", "docKey")
    assert tracker.is_within_cooldown("1", 24) is True
    assert tracker.price_index_within_cooldown("nope", 24) is False
