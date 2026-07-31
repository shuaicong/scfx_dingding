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
    extra           TEXT,                     -- 扩展信息（JSON）
    content_hash    TEXT                      -- 最近写入内容哈希
);

CREATE TABLE IF NOT EXISTS price_index_records (
    doc_key_id      TEXT PRIMARY KEY,        -- 组合唯一标识
    title           TEXT NOT NULL,            -- 文档标题
    dingtalk_doc_key TEXT,                    -- 钉钉文档 docKey
    dingtalk_url    TEXT,                     -- 钉钉文档链接
    synced_at       TEXT NOT NULL,            -- 最后同步时间
    content_hash    TEXT                      -- 最近写入内容哈希
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


class SyncTracker:
    """同步追踪器"""

    def __init__(self, db_path: str):
        self.db_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._init_db()

    def _init_db(self):
        """初始化数据库表，并对旧库补齐新列"""
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript(CREATE_TABLE_SQL)
            self._ensure_column(conn, "sync_records", "content_hash", "TEXT")
            self._ensure_column(conn, "price_index_records", "content_hash", "TEXT")

    @staticmethod
    def _ensure_column(conn, table: str, column: str, coltype: str):
        """为已存在的旧表补齐缺失列"""
        cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
        if column not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")
            conn.commit()

    def is_synced(self, article_id: str) -> bool:
        """检查文章是否已同步"""
        return self.get_record(article_id) is not None

    def get_record(self, article_id: str) -> dict | None:
        """获取已同步记录的完整信息"""
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT dingtalk_doc_key, dingtalk_url, synced_at, extra, content_hash FROM sync_records WHERE article_id = ?",
                (article_id,)
            ).fetchone()
            if row:
                return {
                    "dingtalk_doc_key": row[0],
                    "dingtalk_url": row[1],
                    "synced_at": row[2],
                    "extra": row[3],
                    "content_hash": row[4],
                }
            return None

    def mark_synced(self, article_id: str, title: str,
                    dingtalk_doc_key: str = "",
                    dingtalk_url: str = "",
                    extra: str = "",
                    content_hash: str = ""):
        """标记文章为已同步"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """INSERT OR REPLACE INTO sync_records
                   (article_id, title, dingtalk_doc_key, dingtalk_url, synced_at, extra, content_hash)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (article_id, title, dingtalk_doc_key, dingtalk_url, now, extra, content_hash),
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
                "SELECT dingtalk_doc_key, dingtalk_url, synced_at, content_hash FROM price_index_records WHERE doc_key_id = ?",
                (doc_key_id,)
            ).fetchone()
            if row:
                return {
                    "dingtalk_doc_key": row[0],
                    "dingtalk_url": row[1],
                    "synced_at": row[2],
                    "content_hash": row[3],
                }
            return None

    def mark_price_index_synced(self, doc_key_id: str, title: str,
                                dingtalk_doc_key: str = "",
                                dingtalk_url: str = "",
                                content_hash: str = ""):
        """记录价格指数文档已同步"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """INSERT OR REPLACE INTO price_index_records
                   (doc_key_id, title, dingtalk_doc_key, dingtalk_url, synced_at, content_hash)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (doc_key_id, title, dingtalk_doc_key, dingtalk_url, now, content_hash),
            )
            conn.commit()
        logger.info("已记录价格指数同步: %s | %s", doc_key_id, title)

    # ----------------------------------------------------------------
    # 内容哈希
    # ----------------------------------------------------------------
    def record_content_hash(self, article_id: str, content_hash: str):
        """记录文章内容哈希并刷新最后处理时间（不触发钉钉写入）"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE sync_records SET content_hash = ?, synced_at = ? WHERE article_id = ?",
                (content_hash, now, article_id),
            )
            conn.commit()

    def record_price_index_hash(self, doc_key_id: str, content_hash: str):
        """记录价格指数内容哈希并刷新最后处理时间"""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE price_index_records SET content_hash = ?, synced_at = ? WHERE doc_key_id = ?",
                (content_hash, now, doc_key_id),
            )
            conn.commit()

    # ----------------------------------------------------------------
    # meta KV 存储
    # ----------------------------------------------------------------
    def meta_get(self, key: str) -> str | None:
        """读取 meta 键值"""
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
            return row[0] if row else None

    def meta_set(self, key: str, value: str):
        """写入 meta 键值"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                (key, value),
            )
            conn.commit()

    # ----------------------------------------------------------------
    # 月度写入计数（配额熔断）
    # ----------------------------------------------------------------
    def get_monthly_write_count(self) -> int:
        """获取当月已写入次数"""
        month = datetime.now().strftime("%Y-%m")
        val = self.meta_get(f"monthly_write_count:{month}")
        return int(val) if val else 0

    def bump_monthly_write_count(self):
        """当月写入计数 +1"""
        month = datetime.now().strftime("%Y-%m")
        self.meta_set(f"monthly_write_count:{month}", str(self.get_monthly_write_count() + 1))

    # ----------------------------------------------------------------
    # 每日冷却判断
    # ----------------------------------------------------------------
    @staticmethod
    def _within(rec: dict | None, hours: int) -> bool:
        """判断记录的 synced_at 是否在冷却期内"""
        if not rec or not rec.get("synced_at"):
            return False
        try:
            last = datetime.strptime(rec["synced_at"], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return False
        return (datetime.now() - last).total_seconds() < hours * 3600

    def is_within_cooldown(self, article_id: str, hours: int) -> bool:
        """判断 sync_records 记录是否在冷却期内"""
        return self._within(self.get_record(article_id), hours)

    def price_index_within_cooldown(self, doc_key_id: str, hours: int) -> bool:
        """判断 price_index_records 记录是否在冷却期内"""
        return self._within(self.get_price_index_doc(doc_key_id), hours)
