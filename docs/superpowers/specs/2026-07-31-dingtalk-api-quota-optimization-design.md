# 钉钉付费 API 调用量优化设计（压入 5000 次/月）

日期：2026-07-31
状态：已批准

## 背景与目标

粮达网 → 钉钉知识库数据采集同步系统以 daemon 模式每 30 分钟运行一轮完整同步（`main.py:89`）。钉钉**所有 API 调用均消耗付费额度**，每月配额 5000 次，当前一个月实际调用 4 万多次（约 3 天即可打满）。

**目标**：通过优化将每月钉钉 API 调用压入 5000 次以内，并保证硬性不超量。

## 现状分析（调用量放大根因）

| 接口 | 每轮调用 | 每月估算 | 放大原因 |
|---|---|---|---|
| `overwriteContent` 价格指数 | ≈171 | ≈246k | 每轮无条件覆盖全部组合（`engine.py:283`），无内容比对 |
| `overwriteContent` 华南 | ≈115 | ≈166k | 每轮覆盖当月全部文章（`engine.py:494`），内容从未变化也重写 |
| `overwriteContent` 海南 | ≈14 | ≈20k | 同上（`engine.py:598`） |
| `list_nodes` 查文件夹 | 2 | ≈2880 | 每轮新建 SyncEngine，文件夹 nodeId 进程内缓存失效，重复查询 |
| `gettoken` | 2 小时/次 | ≈360 | 固定开销，无法避免 |
| `create_document` 新增 | 仅新增 | 小 | — |
| AI 触发"调用技能更新" | 每次新增 | 看新增量 | `trigger_relearn.py` 无冷却 |

**根因**：写入前从不比较新旧内容（无内容哈希），无按数据新鲜度节流，无钉钉调用限流，无 AI 触发冷却，文件夹 nodeId 不持久化。

**价格指数组合冗余**：实际展开 340 组合，写入 171 组合。用户确认砍掉小麦（48 组合），保留玉米（105）+ 国产大豆（18）= 123 组合。

## 优化设计

### 1. 数据模型变更（`storage/tracker.py`）

- `sync_records` 增加 `content_hash TEXT` 列
- `price_index_records` 增加 `content_hash TEXT` 列
- 新增 `meta` KV 表：

```sql
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
```

存储的键：
- `folder_node_id:price_index` / `folder_node_id:big_data`：文件夹 nodeId 缓存
- `relearn_last_trigger`：AI 触发冷却时间戳
- `monthly_write_month` / `monthly_write_count`：当月写入计数

**旧库迁移**：`_init_db` 检测列是否存在，缺失则执行 `ALTER TABLE ... ADD COLUMN`；新库直接建全列。`meta` 表用 `CREATE TABLE IF NOT EXISTS` 幂等创建。

### 2. 价格指数时间戳修正（`crawler/price_index.py:197`）

文档中的 `**采集时间**: {datetime.now()}` 每轮必变，导致内容哈希比对失效。改为：

`**数据截止日期**: <价格数据最后一天的日期>`（无价格数据则不渲染该行）。

目的：内容仅在价格数据真正更新时变化，哈希比对才有意义。

### 3. 价格指数品种精简（`crawler/price_index.py` + `config.py`）

- `expand_all_combinations` 过滤掉配置指定的排除品种。
- 新增配置 `PRICE_INDEX_EXCLUDE_VARIETIES`（逗号分隔，默认空），部署时设为 `小麦`。
- 已同步的小麦文档**保留不动**，仅停止采集与更新。

### 4. 内容哈希比对（`sync/engine.py` + `storage/tracker.py`）

统一写入入口 `_write_if_changed(doc_key, content, record_key)`：

1. 计算 `sha1(content).hexdigest()`
2. 查询库中 `content_hash`：
   - 相同 → 跳过 `overwriteContent`，仅记日志（节省调用）
   - 不同 → 调用 `overwriteContent` 并更新 `content_hash`
   - **存量行为空** → 不写入，仅记录当前哈希（避免上线首轮全量重写浪费额度）

价格指数、华南、海南三条覆盖路径全部改走该入口；新增文章路径照常创建+写入。

### 5. 分级频率：同文档每天最多写入一次（`sync/engine.py`）

- 新增配置 `DAILY_WRITE_COOLDOWN_HOURS = 24`。
- 每条文档距上次写入（`synced_at`）不足冷却期则本轮跳过写入（内容变化也等次日）。
- 粮达网文章 / 农粮大数据保持每轮检测新文章（爬虫抓取不消耗钉钉额度，只有写入才消耗）。

### 6. 文件夹 nodeId 持久化（`sync/engine.py`）

`_ensure_price_index_folder` / `_ensure_big_data_folder` 先查 `meta` 表，命中直接返回；未命中才 `list_nodes` 查找/创建并写入 `meta`。消掉每轮 2 次 `list_nodes`。

### 7. AI 触发冷却（`sync/engine.py`）

`_trigger_relearn` 检查 `meta.relearn_last_trigger`，距上次触发不足 24 小时则跳过；触发成功后更新时间戳。

### 8. 月度配额熔断（`sync/engine.py` + `storage/tracker.py` + `config.py`）

- 新增配置 `MONTHLY_QUOTA_LIMIT = 4800`。
- 每次实际 `overwriteContent` 调用前检查 `meta.monthly_write_count`（按 `monthly_write_month` 判断当前月份）：
  - 达到阈值 → 跳过本次写入并记日志警告，保证绝不超量
  - 未达到 → 调用成功后计数 +1
- 月份变化时自动重置计数。

### 9. 新增配置项（`config.py`）

| 配置 | 默认值 | 说明 |
|---|---|---|
| `PRICE_INDEX_EXCLUDE_VARIETIES` | `""` | 排除的价格指数品种，逗号分隔（部署设 `小麦`） |
| `DAILY_WRITE_COOLDOWN_HOURS` | `24` | 同文档每日冷却期（小时） |
| `MONTHLY_QUOTA_LIMIT` | `4800` | 月度写入熔断阈值 |

## 预算估算（优化后，每月）

| 来源 | 每月调用 |
|---|---|
| 价格指数（123 组合，内容变化才写，每日冷却） | ≤3690 |
| 华南粮网 / 海南（内容比对，文章不变≈0） | ≈0–30 |
| 粮达网文章 / 大数据新增（新增量×2） | ≈100 |
| 文件夹查询（持久化后） | 0 |
| `gettoken` | ≈360 |
| **合计** | **≈4150 < 5000** ✓ |

熔断阈值 4800 作为硬性兜底，即使价格指数每天 123 组合全变（3690）加上其他调用，也保证不超配额。

## 测试计划

新增 `tests/` 目录（pytest，加入 `requirements.txt` 或测试依赖清单），覆盖：

1. **schema 迁移**：旧库（无 `content_hash` 列）升级后列存在，`meta` 表可用。
2. **内容哈希跳过**：相同内容不调用 `overwrite_content`（mock 客户端验证），不同内容才调用。
3. **存量空哈希**：首次比对只记录哈希不写入。
4. **每日冷却**：距上次写入不足 24h 的组合跳过写入。
5. **月度熔断**：达到 `MONTHLY_QUOTA_LIMIT` 后拒绝写入，跨月自动重置。
6. **AI 冷却**：24h 内不重复触发。
7. **文件夹持久化**：首次查找后再次调用直接读 meta，不重复 `list_nodes`。
8. **品种排除**：`PRICE_INDEX_EXCLUDE_VARIETIES` 生效，小麦组合被过滤。

按 CLAUDE.md 规范，所有代码改动完成后运行完整测试套件，无回归方可交付。

## 风险与回滚

- **价格指数时效性**：每日冷却意味着同一天内源站价格变化不会立即同步，次日更新。已确认可接受。
- **存量空哈希首轮不写入**：华南/海南内容稳定，风险低；价格指数次日自动正常比对。
- **熔断触发**：若某月调用逼近 4800，部分更新推迟到次日/次月，通过日志告警可见，不会超量计费。
- **回滚**：全部改动集中在 `tracker.py`、`engine.py`、`price_index.py`、`config.py`，`meta` 与新增列为 additive 变更，回滚无需改动数据库。

## 决策记录

- 计费对象：钉钉知识库文档写入接口（所有钉钉 API 调用均消耗额度）。
- 价格指数精简：排除小麦（48 组合），保留玉米 + 大豆 = 123 组合。
- 已有小麦文档：保留不动，停止更新。
- 方案组合：A（稳健）——内容比对 + 分级频率 + 文件夹持久化 + AI 冷却 + 配额熔断。
