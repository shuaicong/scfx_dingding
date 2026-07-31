# 钉钉付费 API 调用量优化实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将钉钉知识库写入调用从每月 4 万+ 压入 5000 次以内，通过内容哈希比对、每日冷却、组合精简、文件夹持久化、AI 冷却与月度配额熔断实现。

**Architecture:** 在现有同步引擎（`sync/engine.py`）与追踪器（`storage/tracker.py`）之上做增量改造：所有覆盖写入统一走 `_write_if_changed` 入口（哈希比对 + 冷却 + 熔断），价格指数组合经配置精简，文件夹 nodeId 与 AI 触发时间持久化到本地 `meta` 表。

**Tech Stack:** Python 3.14、SQLite、pytest、requests（现有）、APScheduler（现有）、Playwright（现有）。

**Spec:** `docs/superpowers/specs/2026-07-31-dingtalk-api-quota-optimization-design.md`

---

## 文件结构总览

| 文件 | 职责 | 动作 |
|---|---|---|
| `config.py` | 新增 4 个配置项 | 修改 |
| `storage/tracker.py` | schema 迁移、content_hash、meta 表、月度计数、冷却判断 | 修改 |
| `crawler/price_index.py` | 组合精简（排除品种 + 深加工白名单）、时间戳修正 | 修改 |
| `sync/engine.py` | `_write_if_changed` 写入入口、三数据源接入、文件夹持久化、AI 冷却 | 修改 |
| `tests/test_config.py` | 配置默认值 | 新建 |
| `tests/test_tracker.py` | tracker 数据模型 | 新建 |
| `tests/test_price_index.py` | 组合精简与时间戳 | 新建 |
| `tests/test_engine.py` | 写入入口、文件夹、AI 冷却 | 新建 |

---

## Task 1: 配置新增

**Files:**
- Modify: `config.py`（在"调度配置"之前新增一节）
- Test: `tests/test_config.py`

- [ ] **Step 1: 写失败测试**

新建 `tests/test_config.py`：

```python
"""配置默认值测试"""
from config import (
    DAILY_WRITE_COOLDOWN_HOURS,
    MONTHLY_QUOTA_LIMIT,
    PRICE_INDEX_DEEP_KEEP_AREAS,
    PRICE_INDEX_EXCLUDE_VARIETIES,
)


def test_new_config_defaults():
    assert PRICE_INDEX_EXCLUDE_VARIETIES == ""
    assert PRICE_INDEX_DEEP_KEEP_AREAS == ""
    assert DAILY_WRITE_COOLDOWN_HOURS == 24
    assert MONTHLY_QUOTA_LIMIT == 4800
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python3 -m pytest tests/test_config.py -v`
Expected: FAIL，`ImportError: cannot import name 'PRICE_INDEX_EXCLUDE_VARIETIES'`

- [ ] **Step 3: 实现配置**

在 `config.py` 中，"调度配置"一节（当前第 96-98 行）之前插入：

```python
# ========== 钉钉调用量优化配置 ==========
# 排除的价格指数品种（逗号分隔），如 "小麦"
PRICE_INDEX_EXCLUDE_VARIETIES = _env("PRICE_INDEX_EXCLUDE_VARIETIES", "")
# 深加工企业收购价保留的收购点白名单（逗号分隔）
PRICE_INDEX_DEEP_KEEP_AREAS = _env("PRICE_INDEX_DEEP_KEEP_AREAS", "")
# 同文档每日冷却期（小时），冷却期内不重复写入
DAILY_WRITE_COOLDOWN_HOURS = _env_int("DAILY_WRITE_COOLDOWN_HOURS", 24)
# 月度写入熔断阈值
MONTHLY_QUOTA_LIMIT = _env_int("MONTHLY_QUOTA_LIMIT", 4800)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python3 -m pytest tests/test_config.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add config.py tests/test_config.py
git commit -m "feat: 新增钉钉调用量优化配置项"
```

---

## Task 2: tracker 数据模型扩展

**Files:**
- Modify: `storage/tracker.py`
- Test: `tests/test_tracker.py`

- [ ] **Step 1: 写失败测试**

新建 `tests/test_tracker.py`：

```python
"""追踪器数据模型测试"""
import sqlite3
from datetime import datetime
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
```

注意：文件顶部需要 `import pytest`。

- [ ] **Step 2: 运行测试确认失败**

Run: `python3 -m pytest tests/test_tracker.py -v`
Expected: FAIL（`AttributeError: 'SyncTracker' object has no attribute 'meta_get'` 等）

- [ ] **Step 3: 实现 tracker 扩展**

在 `storage/tracker.py` 中：

**(a) 更新 `CREATE_TABLE_SQL`**（第 12-29 行）为：

```python
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
```

**(b) 修改 `_init_db`**（第 40-43 行）增加列迁移：

```python
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
```

**(c) 修改 `get_record`**（第 49-63 行），SELECT 增加 `content_hash`：

```python
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
```

**(d) 修改 `mark_synced`**（第 65-79 行）增加 `content_hash` 参数：

```python
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
```

**(e) 修改 `get_price_index_doc`**（第 97-110 行）增加 `content_hash`：

```python
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
```

**(f) 修改 `mark_price_index_synced`**（第 112-125 行）增加 `content_hash`：

```python
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
```

**(g) 在类末尾追加新方法**（`count` 方法之后）：

```python
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
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python3 -m pytest tests/test_tracker.py -v`
Expected: PASS（全部 9 个）

- [ ] **Step 5: 提交**

```bash
git add storage/tracker.py tests/test_tracker.py
git commit -m "feat: tracker 支持 content_hash、meta 表、月度计数与每日冷却"
```

---

## Task 3: 价格指数组合精简 + 时间戳修正

**Files:**
- Modify: `crawler/price_index.py`
- Test: `tests/test_price_index.py`

- [ ] **Step 1: 写失败测试**

新建 `tests/test_price_index.py`：

```python
"""价格指数采集器精简与时间戳测试"""
import pytest
from unittest import mock
from crawler.price_index import PriceIndexCollector


def fake_varieties():
    return [{"varietyName": "玉米", "id": "1"}, {"varietyName": "小麦", "id": "2"}]


def fake_tree(vid):
    return [{
        "regionCodeCn": "华北",
        "districtList": [{
            "districtCodeCn": "河北",
            "areaList": [{"area": "石家庄"}, {"area": "保定"}],
        }],
    }]


def fake_rank_type(*args, **kwargs):
    return {
        "success": True,
        "data": {"rankNameList": ["二等"], "priceTypeList": ["主流粮成交价", "深加工企业收购价"]},
    }


@mock.patch.object(PriceIndexCollector, "get_rank_and_type", side_effect=fake_rank_type)
@mock.patch.object(PriceIndexCollector, "get_variety_tree", side_effect=fake_tree)
@mock.patch.object(PriceIndexCollector, "get_variety_list", side_effect=fake_varieties)
def test_exclude_varieties(mock_list, mock_tree, mock_rank, monkeypatch):
    monkeypatch.setattr("crawler.price_index.PRICE_INDEX_EXCLUDE_VARIETIES", "小麦")
    pc = PriceIndexCollector()
    combos = pc.expand_all_combinations()
    names = {c["variety_name"] for c in combos}
    assert names == {"玉米"}


@mock.patch.object(PriceIndexCollector, "get_rank_and_type", side_effect=fake_rank_type)
@mock.patch.object(PriceIndexCollector, "get_variety_tree", side_effect=fake_tree)
@mock.patch.object(PriceIndexCollector, "get_variety_list", side_effect=fake_varieties)
def test_deep_keep_areas(mock_list, mock_tree, mock_rank, monkeypatch):
    monkeypatch.setattr("crawler.price_index.PRICE_INDEX_EXCLUDE_VARIETIES", "")
    monkeypatch.setattr("crawler.price_index.PRICE_INDEX_DEEP_KEEP_AREAS", "石家庄")
    pc = PriceIndexCollector()
    combos = pc.expand_all_combinations()
    deep = [c for c in combos if c["price_type"] == "深加工企业收购价"]
    assert [c["area"] for c in deep] == ["石家庄"]


@mock.patch.object(PriceIndexCollector, "get_price_chart")
def test_build_document_uses_data_date(mock_chart):
    mock_chart.return_value = [{
        "priceDate": "2026-07-30", "price": "2500", "priceDiff": "+10",
        "lastYearPrice": "2400", "remark": "",
    }]
    pc = PriceIndexCollector()
    cfg = {"variety_name": "玉米", "area_type": "华北", "province": "河北",
           "area": "石家庄", "rank": "二等", "price_type": "主流粮成交价"}
    content = pc.build_document(cfg)
    assert "数据截止日期" in content
    assert "2026-07-30" in content
    assert "采集时间" not in content
```

注意：`test_exclude_varieties` 依赖 `config.py` 中 `PRICE_INDEX_EXCLUDE_VARIETIES` 默认空；测试用 `monkeypatch` 覆盖模块绑定名。

- [ ] **Step 2: 运行测试确认失败**

Run: `python3 -m pytest tests/test_price_index.py -v`
Expected: FAIL（`test_exclude_varieties` 断言 `names == {"玉米"}` 失败，因为当前包含小麦）

- [ ] **Step 3: 实现组合精简与时间戳修正**

在 `crawler/price_index.py` 中：

**(a) 更新 import**（第 10 行）：

```python
from config import PRICE_INDEX_DAYS, PRICE_INDEX_EXCLUDE_VARIETIES, PRICE_INDEX_DEEP_KEEP_AREAS
```

**(b) 修改 `expand_all_combinations`**（第 92-154 行），在 `get_variety_list()` 之后与循环体内加入过滤：

```python
    def expand_all_combinations(self) -> list[dict]:
        """展开所有品种×区域×地区×地点×等级×价格类型的组合

        Returns:
            [{
                "variety_name": "玉米",
                "variety_id": "206...",
                "area_type": "港口",
                "province": "南港",
                "area": "海口港",
                "rank": "二等",
                "price_type": "主流粮成交价",
                "district_id": "...",
                "area_id": "...",
            }, ...]
        """
        exclude_varieties = {
            v.strip() for v in PRICE_INDEX_EXCLUDE_VARIETIES.split(",") if v.strip()
        }
        keep_deep_areas = {
            a.strip() for a in PRICE_INDEX_DEEP_KEEP_AREAS.split(",") if a.strip()
        }

        combinations = []
        varieties = self.get_variety_list()

        for variety in varieties:
            vname = variety["varietyName"]
            vid = variety["id"]
            if vname in exclude_varieties:
                logger.info("排除品种: %s", vname)
                continue

            tree = self.get_variety_tree(vid)

            for region in tree:
                area_type = region.get("regionCodeCn", "")
                for district in region.get("districtList", []):
                    province = district.get("districtCodeCn", "")
                    for area_obj in district.get("areaList", []):
                        area_name = area_obj.get("area", "")

                        # 获取该组合的等级和价格类型
                        try:
                            rt = self.get_rank_and_type(
                                vname, area_type, province, area_name
                            )
                            if not rt.get("success"):
                                continue
                            rt_data = rt.get("data", {})
                        except Exception as e:
                            logger.warning(
                                "获取等级类型失败 %s/%s/%s/%s: %s",
                                vname, area_type, province, area_name, e,
                            )
                            continue

                        ranks = rt_data.get("rankNameList", [])
                        price_types = rt_data.get("priceTypeList", [])

                        # 每个等级×价格类型作为一个采集组合
                        for rank in ranks:
                            for pt in price_types:
                                # 深加工企业收购价仅保留白名单收购点
                                if pt == "深加工企业收购价" and keep_deep_areas and area_name not in keep_deep_areas:
                                    continue
                                combinations.append({
                                    "variety_name": vname,
                                    "variety_id": vid,
                                    "area_type": area_type,
                                    "province": province,
                                    "area": area_name,
                                    "rank": rank,
                                    "price_type": pt,
                                })

        return combinations
```

**(c) 修改 `build_document`**（第 191-199 行附近），将采集时间戳替换为数据截止日期：

原代码：

```python
        lines.append(f"**采集时间**: {datetime.now().strftime('%Y-%m-%d %H:%M')}  ")
```

替换为：

```python
        # 数据截止日期取价格数据最后一天，保证内容只在数据更新时变化
        if recent:
            last_date = recent[-1].get("priceDate", "") or ""
            if last_date:
                lines.append(f"**数据截止日期**: {last_date}  ")
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python3 -m pytest tests/test_price_index.py -v`
Expected: PASS（全部 3 个）

- [ ] **Step 5: 提交**

```bash
git add crawler/price_index.py tests/test_price_index.py
git commit -m "feat: 价格指数组合精简（排除品种+深加工白名单）与时间戳修正"
```

---

## Task 4: engine 通用写入入口 `_write_if_changed`

**Files:**
- Modify: `sync/engine.py`
- Test: `tests/test_engine.py`

- [ ] **Step 1: 写失败测试**

新建 `tests/test_engine.py`：

```python
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
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python3 -m pytest tests/test_engine.py -v`
Expected: FAIL（`AttributeError: 'SyncEngine' object has no attribute '_write_if_changed'`）

- [ ] **Step 3: 实现写入入口**

在 `sync/engine.py` 中：

**(a) 更新 import**（第 5-37 行区域），加入 `hashlib` 与新增配置：

在第 5 行 `import logging` / `import time` 附近加入 `import hashlib`：

```python
import hashlib
import logging
import time
```

在 `from config import (...)` 块中加入：

```python
    DAILY_WRITE_COOLDOWN_HOURS,
    MONTHLY_QUOTA_LIMIT,
```

**(b) 在 `__init__` 之后（第 57 行 `self._huanan_folder_id = None` 后）追加三个方法**：

```python
    # ----------------------------------------------------------------
    # 写入控制：内容哈希比对 + 每日冷却 + 月度熔断
    # ----------------------------------------------------------------
    def _get_record_hash(self, record_key: str) -> str | None:
        """读取记录的最近内容哈希"""
        if record_key.startswith("price_index:"):
            rec = self.tracker.get_price_index_doc(record_key)
        else:
            rec = self.tracker.get_record(record_key)
        return rec.get("content_hash") if rec else None

    def _set_record_hash(self, record_key: str, content_hash: str):
        """记录内容哈希（同时刷新最后处理时间）"""
        if record_key.startswith("price_index:"):
            self.tracker.record_price_index_hash(record_key, content_hash)
        else:
            self.tracker.record_content_hash(record_key, content_hash)

    def _within_cooldown(self, record_key: str) -> bool:
        """判断记录是否在每日冷却期内"""
        if record_key.startswith("price_index:"):
            return self.tracker.price_index_within_cooldown(record_key, DAILY_WRITE_COOLDOWN_HOURS)
        return self.tracker.is_within_cooldown(record_key, DAILY_WRITE_COOLDOWN_HOURS)

    def _quota_available(self) -> bool:
        """月度写入配额是否可用"""
        return self.tracker.get_monthly_write_count() < MONTHLY_QUOTA_LIMIT

    def _write_if_changed(self, doc_key: str, content: str, record_key: str,
                          is_new: bool = False) -> bool:
        """内容变化才写入钉钉文档，返回是否实际调用了 overwrite_content。

        规则：
          1. 月度配额熔断：达到上限则不写
          2. 内容哈希相同：跳过写入
          3. 存量空哈希（首次比对）：仅记录哈希不写入
          4. 非新增且冷却期内：跳过写入
        """
        if not self._quota_available():
            logger.warning("月度钉钉写入配额已达上限，跳过写入: %s", record_key)
            return False

        content_hash = hashlib.sha1(content.encode("utf-8")).hexdigest()
        prev_hash = self._get_record_hash(record_key)

        if prev_hash == content_hash:
            logger.info("内容未变化，跳过写入: %s", record_key)
            return False

        if prev_hash is None:
            logger.info("首次比对，仅记录内容哈希: %s", record_key)
            self._set_record_hash(record_key, content_hash)
            return False

        if not is_new and self._within_cooldown(record_key):
            logger.info("每日冷却期内，跳过写入: %s", record_key)
            return False

        self.dingtalk.overwrite_content(doc_key=doc_key, content=content)
        self._set_record_hash(record_key, content_hash)
        self.tracker.bump_monthly_write_count()
        return True
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python3 -m pytest tests/test_engine.py -v`
Expected: PASS（全部 7 个）

- [ ] **Step 5: 提交**

```bash
git add sync/engine.py tests/test_engine.py
git commit -m "feat: 新增统一写入入口 _write_if_changed（哈希比对+冷却+熔断）"
```

---

## Task 5: 三数据源覆盖路径接入写入入口

**Files:**
- Modify: `sync/engine.py`（价格指数 `sync_price_indices`、华南 `_sync_one_huanan`、海南 `_sync_one_grainmarket`）
- Test: 现有 `tests/test_engine.py` 回归

- [ ] **Step 1: 改价格指数路径（第 257-316 行）**

先在 `for config in combinations:` 循环开头（`existing = self.tracker.get_price_index_doc(doc_key_id)` 之后、`build_document` 之前）插入冷却检查，避免冷却期内重复爬取构建：

```python
        for config in combinations:
            doc_key_id = PriceIndexCollector.doc_key(config)
            title = PriceIndexCollector._doc_title(config)

            # 检查是否已同步过（存储 docKey）
            existing = self.tracker.get_price_index_doc(doc_key_id)

            # 冷却期内跳过已有组合的整篇处理（含爬虫构建）
            if existing and self.tracker.price_index_within_cooldown(doc_key_id, DAILY_WRITE_COOLDOWN_HOURS):
                stats["skipped"] += 1
                continue

            # 构建文档内容
            try:
                content = self.price_index.build_document(config)
```

然后替换现有覆盖/新建分支（当前约第 278-312 行）：

```python
            if not content:
                logger.info("跳过无数据: %s", title)
                stats["skipped"] += 1
                continue

            # 写入钉钉
            try:
                if existing:
                    # 覆盖已有文档：内容变化且不在冷却期才写入
                    doc_key = existing["dingtalk_doc_key"]
                    if self._write_if_changed(doc_key, content, doc_key_id):
                        stats["success"] += 1
                    else:
                        stats["skipped"] += 1
                    logger.info("已处理: %s", title)
                else:
                    # 创建新文档
                    doc = self.dingtalk.create_document(
                        workspace_id=DINGTALK_WORKSPACE_ID,
                        parent_node_id=folder_id,
                        name=title,
                    )
                    doc_key = doc.get("docKey", "")
                    doc_url = doc.get("url", "")

                    if self._quota_available():
                        self.dingtalk.overwrite_content(doc_key=doc_key, content=content)
                        self.tracker.bump_monthly_write_count()
                    else:
                        logger.warning("月度配额已满，跳过新增写入: %s", title)

                    # 记录 docKey（含内容哈希）
                    self.tracker.mark_price_index_synced(
                        doc_key_id=doc_key_id,
                        title=title,
                        dingtalk_doc_key=doc_key,
                        dingtalk_url=doc_url,
                        content_hash=hashlib.sha1(content.encode("utf-8")).hexdigest(),
                    )
                    logger.info("已创建: %s", title)

                stats["results"].append({"title": title, "status": "ok"})

            except Exception as e:
                logger.warning("写入失败 %s: %s", title, e)
                stats["failed"] += 1
                stats["results"].append({"title": title, "status": "write_fail", "error": str(e)})
```

注意：`stats["success"] += 1` 移入分支内，避免覆盖时重复累计；`skipped` 语义扩展为"无数据/冷却期/内容未变"。

- [ ] **Step 2: 改华南路径 `_sync_one_huanan`（第 491-515 行）**

当前：

```python
        if existing_record and existing_record.get("dingtalk_doc_key"):
            # 已有文档：覆盖更新内容
            doc_key = existing_record["dingtalk_doc_key"]
            self.dingtalk.overwrite_content(doc_key=doc_key, content=content)
            logger.info("文档已覆盖更新: docKey=%s", doc_key)
        else:
            # 新文章：创建文档并写入
            doc = self.dingtalk.create_document(
                workspace_id=HUANAN_WORKSPACE_ID,
                parent_node_id=HUANAN_TARGET_NODE_ID,
                name=title,
            )
            doc_key = doc.get("docKey", "")
            doc_url = doc.get("url", "")

            self.dingtalk.overwrite_content(doc_key=doc_key, content=content)

            # 标记已同步
            self.tracker.mark_synced(
                article_id=track_key,
                title=title,
                dingtalk_doc_key=doc_key,
                dingtalk_url=doc_url,
                extra=f'{{"source":"huanan","articleId":{article_id}}}',
            )
```

替换为：

```python
        if existing_record and existing_record.get("dingtalk_doc_key"):
            # 已有文档：内容变化且不在冷却期才覆盖更新
            doc_key = existing_record["dingtalk_doc_key"]
            self._write_if_changed(doc_key, content, track_key)
            logger.info("文档已处理: docKey=%s", doc_key)
        else:
            # 新文章：创建文档并写入
            doc = self.dingtalk.create_document(
                workspace_id=HUANAN_WORKSPACE_ID,
                parent_node_id=HUANAN_TARGET_NODE_ID,
                name=title,
            )
            doc_key = doc.get("docKey", "")
            doc_url = doc.get("url", "")

            if self._quota_available():
                self.dingtalk.overwrite_content(doc_key=doc_key, content=content)
                self.tracker.bump_monthly_write_count()
            else:
                logger.warning("月度配额已满，跳过新增写入: %s", title)

            # 标记已同步（含内容哈希）
            self.tracker.mark_synced(
                article_id=track_key,
                title=title,
                dingtalk_doc_key=doc_key,
                dingtalk_url=doc_url,
                extra=f'{{"source":"huanan","articleId":{article_id}}}',
                content_hash=hashlib.sha1(content.encode("utf-8")).hexdigest(),
            )
```

- [ ] **Step 3: 改海南路径 `_sync_one_grainmarket`（第 595-617 行）**

当前：

```python
        if existing_record and existing_record.get("dingtalk_doc_key"):
            # 已有文档：覆盖更新
            doc_key = existing_record["dingtalk_doc_key"]
            self.dingtalk.overwrite_content(doc_key=doc_key, content=content)
            logger.info("文档已覆盖更新: docKey=%s", doc_key)
        else:
            # 新文章：创建文档并写入
            doc = self.dingtalk.create_document(
                workspace_id=GRAINMARKET_WORKSPACE_ID,
                parent_node_id=GRAINMARKET_TARGET_NODE_ID,
                name=title,
            )
            doc_key = doc.get("docKey", "")
            doc_url = doc.get("url", "")
            self.dingtalk.overwrite_content(doc_key=doc_key, content=content)

            self.tracker.mark_synced(
                article_id=track_key,
                title=title,
                dingtalk_doc_key=doc_key,
                dingtalk_url=doc_url,
                extra=f'{{"source":"grainmarket","articleId":"{article_id}","type":"{article_type_name}"}}',
            )
```

替换为：

```python
        if existing_record and existing_record.get("dingtalk_doc_key"):
            # 已有文档：内容变化且不在冷却期才覆盖更新
            doc_key = existing_record["dingtalk_doc_key"]
            self._write_if_changed(doc_key, content, track_key)
            logger.info("文档已处理: docKey=%s", doc_key)
        else:
            # 新文章：创建文档并写入
            doc = self.dingtalk.create_document(
                workspace_id=GRAINMARKET_WORKSPACE_ID,
                parent_node_id=GRAINMARKET_TARGET_NODE_ID,
                name=title,
            )
            doc_key = doc.get("docKey", "")
            doc_url = doc.get("url", "")
            if self._quota_available():
                self.dingtalk.overwrite_content(doc_key=doc_key, content=content)
                self.tracker.bump_monthly_write_count()
            else:
                logger.warning("月度配额已满，跳过新增写入: %s", title)

            self.tracker.mark_synced(
                article_id=track_key,
                title=title,
                dingtalk_doc_key=doc_key,
                dingtalk_url=doc_url,
                extra=f'{{"source":"grainmarket","articleId":"{article_id}","type":"{article_type_name}"}}',
                content_hash=hashlib.sha1(content.encode("utf-8")).hexdigest(),
            )
```

- [ ] **Step 4: 华南/海南循环内增加冷却期整篇跳过**

华南 `sync_huanan` 循环体（约第 440-464 行），在 `existing = self.tracker.get_record(track_key)` 之后、`try` 之前插入：

```python
            # 冷却期内跳过已有文章的整篇处理（含爬虫构建）
            if existing and self.tracker.is_within_cooldown(track_key, DAILY_WRITE_COOLDOWN_HOURS):
                stats["skipped"] += 1
                continue
```

海南 `sync_grainmarket` 循环体（约第 543-568 行），同样在 `existing = self.tracker.get_record(track_key)` 之后、`try` 之前插入：

```python
            # 冷却期内跳过已有文章的整篇处理（含爬虫构建）
            if existing and self.tracker.is_within_cooldown(track_key, DAILY_WRITE_COOLDOWN_HOURS):
                stats["skipped"] += 1
                continue
```

- [ ] **Step 5: 运行回归测试确认通过**

Run: `python3 -m pytest tests/ -v`
Expected: PASS（全部测试；本任务未新增测试，验证既有测试仍通过）

- [ ] **Step 6: 提交**

```bash
git add sync/engine.py
git commit -m "feat: 价格指数/华南/海南覆盖路径接入统一写入入口"
```

---

## Task 6: 文件夹 nodeId 持久化

**Files:**
- Modify: `sync/engine.py`
- Test: `tests/test_engine.py`（追加）

- [ ] **Step 1: 写失败测试**

在 `tests/test_engine.py` 末尾追加：

```python
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
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python3 -m pytest tests/test_engine.py::test_price_index_folder_cached_in_meta tests/test_engine.py::test_big_data_folder_cached_in_meta -v`
Expected: FAIL（调用次数超过 1，meta 未命中）

- [ ] **Step 3: 实现持久化**

修改 `sync/engine.py` 的 `_ensure_price_index_folder`（第 222-232 行）：

```python
    def _ensure_price_index_folder(self) -> str:
        """确保价格指数文件夹存在，返回 nodeId（本地 meta 缓存，避免每轮查询）"""
        if self._price_index_folder_id:
            return self._price_index_folder_id
        cached = self.tracker.meta_get("folder_node_id:price_index")
        if cached:
            self._price_index_folder_id = cached
            return cached
        folder_id = self.dingtalk.find_or_create_folder(
            workspace_id=DINGTALK_WORKSPACE_ID,
            parent_node_id=DINGTALK_PARENT_NODE_ID,
            name=PRICE_INDEX_FOLDER_NAME,
        )
        self._price_index_folder_id = folder_id
        self.tracker.meta_set("folder_node_id:price_index", folder_id)
        return folder_id
```

修改 `_ensure_big_data_folder`（第 321-331 行）：

```python
    def _ensure_big_data_folder(self) -> str:
        """确保农粮大数据文件夹存在，返回 nodeId（本地 meta 缓存）"""
        if self._big_data_folder_id:
            return self._big_data_folder_id
        cached = self.tracker.meta_get("folder_node_id:big_data")
        if cached:
            self._big_data_folder_id = cached
            return cached
        folder_id = self.dingtalk.find_or_create_folder(
            workspace_id=DINGTALK_WORKSPACE_ID,
            parent_node_id=DINGTALK_PARENT_NODE_ID,
            name=BIG_DATA_FOLDER_NAME,
        )
        self._big_data_folder_id = folder_id
        self.tracker.meta_set("folder_node_id:big_data", folder_id)
        return folder_id
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python3 -m pytest tests/test_engine.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add sync/engine.py tests/test_engine.py
git commit -m "feat: 文件夹 nodeId 持久化到 meta，消除每轮 list_nodes 查询"
```

---

## Task 7: AI 触发冷却

**Files:**
- Modify: `sync/engine.py`
- Test: `tests/test_engine.py`（追加）

- [ ] **Step 1: 写失败测试**

在 `tests/test_engine.py` 末尾追加：

```python
def test_relearn_cooldown_skips(engine):
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
```

注意：`test_relearn_cooldown_skips` 依赖 `engine_module.trigger_knowledge_relearn` 在 import 时被替换为 mock。测试中直接 patch 模块属性；为让 `_trigger_relearn` 内的调用被拦截，需要先 patch。请在文件顶部（fixture 之后）追加一个 autouse fixture 或直接在测试内 patch：

```python
def test_relearn_cooldown_skips(engine, monkeypatch):
    monkeypatch.setattr(engine_module, "trigger_knowledge_relearn",
                        mock.MagicMock(return_value={"success": True, "response": "ok"}))
    engine.tracker.meta_set("relearn_last_trigger", str(time.time()))
    engine._trigger_relearn()
    engine_module.trigger_knowledge_relearn.assert_not_called()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python3 -m pytest tests/test_engine.py::test_relearn_cooldown_skips tests/test_engine.py::test_relearn_triggered_after_cooldown -v`
Expected: FAIL（冷却期未生效，`_trigger_relearn` 直接触发）

- [ ] **Step 3: 实现冷却**

修改 `sync/engine.py` 的 `_trigger_relearn`（第 202-217 行）：

```python
    def _trigger_relearn(self):
        """触发 AI 助理重新学习知识库（每日冷却，避免频繁触发）"""
        last = self.tracker.meta_get("relearn_last_trigger")
        if last:
            try:
                if time.time() - float(last) < DAILY_WRITE_COOLDOWN_HOURS * 3600:
                    logger.info("AI 重学冷却期内，跳过触发")
                    return
            except ValueError:
                pass

        logger.info("检测到新增文章，触发 AI 助理知识库更新...")
        try:
            result = trigger_knowledge_relearn(
                headless=RELEARN_HEADLESS,
            )
            if result["success"]:
                response_preview = result.get("response", "")[:200]
                logger.info("AI 助理知识库更新触发成功")
                logger.info("AI 回复: %s", response_preview)
            else:
                logger.warning("AI 助理知识库更新触发失败: %s（不影响同步结果）",
                               result.get("response", "未知错误"))
        except Exception as e:
            logger.warning("AI 助理知识库更新触发异常: %s（不影响同步结果）", e)

        self.tracker.meta_set("relearn_last_trigger", str(time.time()))
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python3 -m pytest tests/test_engine.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add sync/engine.py tests/test_engine.py
git commit -m "feat: AI 知识库重学触发每日冷却"
```

---

## Task 8: 全量回归与冒烟验证

**Files:**
- 无代码改动

- [ ] **Step 1: 运行完整测试套件**

Run: `python3 -m pytest tests/ -v`
Expected: 全部 PASS

- [ ] **Step 2: 验证价格指数精简组合数**

Run:

```bash
python3 -c "
import os
os.environ['PRICE_INDEX_EXCLUDE_VARIETIES'] = '小麦'
os.environ['PRICE_INDEX_DEEP_KEEP_AREAS'] = '中粮榆树,吉林梅花,京粮龙江,寿光金玉米,诸城兴贸,福洋生物,河南汉永,孟州金玉米,宁夏伊品,宝鸡阜丰,蚌埠丰原,骊骅原料'
from crawler.price_index import PriceIndexCollector
pc = PriceIndexCollector()
combos = pc.expand_all_combinations()
deep = [c for c in combos if c['price_type'] == '深加工企业收购价']
print('总组合数:', len(combos))
print('深加工组合:', len(deep))
print('品种:', sorted({c['variety_name'] for c in combos}))
"
```

Expected: 输出组合数约 68（38 港口 + 12 深加工 + 18 大豆），品种不含小麦，深加工组合 = 12

- [ ] **Step 3: 冒烟运行一次完整同步（dry 验证）**

Run: `python3 -c "
from sync.engine import SyncEngine
e = SyncEngine()
print('引擎初始化 OK')
"`

Expected: 引擎正常初始化（不触发任何写入）。**不要**在本地运行完整 `do_sync()`，以免消耗真实钉钉额度；完整验证交给部署环境。

- [ ] **Step 4: 提交（若有遗漏的测试改动）**

```bash
git status
git add -A
git commit -m "test: 全量回归验证" || echo "无改动可提交"
```

---

## 部署说明

上线前需设置以下环境变量（`docker-compose.yml` 或 `.env`）：

```
PRICE_INDEX_EXCLUDE_VARIETIES=小麦
PRICE_INDEX_DEEP_KEEP_AREAS=中粮榆树,吉林梅花,京粮龙江,寿光金玉米,诸城兴贸,福洋生物,河南汉永,孟州金玉米,宁夏伊品,宝鸡阜丰,蚌埠丰原,骊骅原料
DAILY_WRITE_COOLDOWN_HOURS=24
MONTHLY_QUOTA_LIMIT=4800
```

首次上线首轮：华南/海南与价格指数存量文档空哈希，仅记录哈希不写入（省一次全量重写）；次日价格指数开始按"内容变化才写 + 每日冷却"正常同步。

---

## 自查记录

- **Spec 覆盖**：配置(§9)→Task1；数据模型(§1)→Task2；组合精简+时间戳(§2/§3)→Task3；哈希比对(§4)→Task4；分级频率(§5)→Task5；文件夹持久化(§6)→Task6；AI冷却(§7)→Task7；配额熔断(§8)→Task4；测试计划→各 Task。
- **占位符扫描**：所有步骤含完整代码与命令，无 TBD/TODO。
- **类型一致性**：`_write_if_changed(doc_key, content, record_key, is_new)`、`record_content_hash/record_price_index_hash`、`meta_get/meta_set`、`is_within_cooldown/price_index_within_cooldown`、`get_monthly_write_count/bump_monthly_write_count` 在各 Task 中签名一致。
