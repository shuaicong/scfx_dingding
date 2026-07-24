"""已同步文章追踪器

使用 SQLite 记录已同步到钉钉的文章 ID，防止重复同步。
"""
import os
import sqlite3
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS sync_records (
    article_id      TEXT PRIMARY KEY,        -- 粮达网文章ID
    title           TEXT NOT NULL,            -- 文章标题
    dingtalk_doc_key TEXT,                    -- 钉钉文档 docKey
    dingtalk_url    TEXT,                     -- 钉钉文档链接
    synced_at       TEXT NOT NULL,            -- 同步时间
    extra           TEXT                      -- 扩展信息（JSON）
);

CREATE TABLE IF NOT EXISTS price_index_records (
    doc_key_id      TEXT PRIMARY KEY,        -- 组合唯一标识
    title           TEXT NOT NULL,            -- 文档标题
    dingtalk_doc_key TEXT,                    -- 钉钉文档 docKey
    dingtalk_url    TEXT,                     -- 钉钉文档链接
    synced_at       TEXT NOT NULL             -- 最后同步时间
);
"""


class SyncTracker:
    """同步追踪器"""

    def __init__(self, db_path: str):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._init_db()

    def _init_db(self):
        """初始化数据库表"""
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript(CREATE_TABLE_SQL)

    def is_synced(self, article_id: str) -> bool:
        """检查文章是否已同步"""
        return self.get_record(article_id) is not None

    def get_record(self, article_id: str) -> dict | None:
        """获取已同步记录的完整信息"""
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT dingtalk_doc_key, dingtalk_url, synced_at, extra FROM sync_records WHERE article_id = ?",
                (article_id,)
            ).fetchone()
            if row:
                return {
                    "dingtalk_doc_key": row[0],
                    "dingtalk_url": row[1],
                    "synced_at": row[2],
                    "extra": row[3],
                }
            return None

    def mark_synced(self, article_id: str, title: str,
                    dingtalk_doc_key: str = "",
                    dingtalk_url: str = "",
                    extra: str = ""):
        """标记文章为已同步"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """INSERT OR REPLACE INTO sync_records
                   (article_id, title, dingtalk_doc_key, dingtalk_url, synced_at, extra)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (article_id, title, dingtalk_doc_key, dingtalk_url, now, extra),
            )
            conn.commit()
        logger.info("已标记文章同步: %s | %s", article_id, title)

    def get_all_synced(self) -> list[tuple]:
        """获取所有已同步记录"""
        with sqlite3.connect(self.db_path) as conn:
            return conn.execute(
                "SELECT article_id, title, synced_at FROM sync_records ORDER BY synced_at DESC"
            ).fetchall()

    def count(self) -> int:
        """获取已同步总数"""
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("SELECT COUNT(*) FROM sync_records").fetchone()
            return row[0] if row else 0

    # ----------------------------------------------------------------
    # 价格指数记录
    # ----------------------------------------------------------------
    def get_price_index_doc(self, doc_key_id: str) -> dict | None:
        """查询价格指数文档的 docKey 等信息"""
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT dingtalk_doc_key, dingtalk_url, synced_at FROM price_index_records WHERE doc_key_id = ?",
                (doc_key_id,)
            ).fetchone()
            if row:
                return {
                    "dingtalk_doc_key": row[0],
                    "dingtalk_url": row[1],
                    "synced_at": row[2],
                }
            return None

    def mark_price_index_synced(self, doc_key_id: str, title: str,
                                 dingtalk_doc_key: str = "",
                                 dingtalk_url: str = ""):
        """记录价格指数文档已同步"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """INSERT OR REPLACE INTO price_index_records
                   (doc_key_id, title, dingtalk_doc_key, dingtalk_url, synced_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (doc_key_id, title, dingtalk_doc_key, dingtalk_url, now),
            )
            conn.commit()
        logger.info("已记录价格指数同步: %s | %s", doc_key_id, title)
