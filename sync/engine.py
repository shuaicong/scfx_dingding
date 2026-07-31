"""同步引擎

连接粮达网爬虫和钉钉 API，执行文章同步逻辑。
"""
import hashlib
import logging
import time

from config import (
    DAILY_WRITE_COOLDOWN_HOURS,
    DINGTALK_APP_KEY,
    DINGTALK_APP_SECRET,
    DINGTALK_UNION_ID,
    DINGTALK_WORKSPACE_ID,
    DINGTALK_PARENT_NODE_ID,
    DB_PATH,
    RELEARN_ENABLED,
    RELEARN_HEADLESS,
    PRICE_INDEX_FOLDER_NAME,
    PRICE_INDEX_DAYS,
    BIG_DATA_FOLDER_NAME,
    BIG_DATA_COLUMN_TYPE,
    HUANAN_COLUMN_ID,
    HUANAN_WORKSPACE_ID,
    HUANAN_TARGET_NODE_ID,
    GRAINMARKET_MARKET_ID,
    GRAINMARKET_WORKSPACE_ID,
    GRAINMARKET_TARGET_NODE_ID,
    GRAINMARKET_ARTICLE_TYPES,
    MONTHLY_QUOTA_LIMIT,
)
from dingtalk.client import DingTalkClient
from crawler.liangdawang import LiangDaWangCrawler
from crawler.price_index import PriceIndexCollector
from crawler import huanan as huanan_crawler
from crawler import grainmarket as grainmarket_crawler
from storage.tracker import SyncTracker
from trigger_relearn import trigger_knowledge_relearn
from config import DINGTALK_ROBOT_WEBHOOK
from utils.notifier import send_notification, build_new_articles_message

logger = logging.getLogger(__name__)


class SyncEngine:
    """同步引擎"""

    def __init__(self):
        self.dingtalk = DingTalkClient(
            app_key=DINGTALK_APP_KEY,
            app_secret=DINGTALK_APP_SECRET,
            union_id=DINGTALK_UNION_ID,
        )
        self.crawler = LiangDaWangCrawler()
        self.price_index = PriceIndexCollector()
        self.tracker = SyncTracker(DB_PATH)
        self._price_index_folder_id: str | None = None
        self._big_data_folder_id: str | None = None
        self._huanan_folder_id: str | None = None

    # ----------------------------------------------------------------
    # 写入控制：内容哈希比对 + 每日冷却 + 月度熔断
    # ----------------------------------------------------------------
    def _get_record_hash(self, record_key: str) -> str | None:
        """读取记录的最近内容哈希（空哈希视为 None，表示尚未记录）"""
        if record_key.startswith("price_index:"):
            rec = self.tracker.get_price_index_doc(record_key)
        else:
            rec = self.tracker.get_record(record_key)
        if rec:
            return rec.get("content_hash") or None
        return None

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

    # ----------------------------------------------------------------
    # 执行一次同步
    # ----------------------------------------------------------------
    def sync_today(self) -> dict:
        """执行今天文章的同步

        流程:
          1. 从粮达网获取今天发布的文章列表
          2. 过滤已同步的文章
          3. 逐篇获取详情、创建钉钉文档、写入内容
          4. 标记已同步

        返回统计信息。
        """
        logger.info("===== 开始同步今天粮达网文章 =====")
        stats = {"total": 0, "new": 0, "skipped": 0, "failed": 0, "results": []}

        # 1. 获取最近文章（昨天+今天）
        articles = self.crawler.list_recent_articles(days=2)
        stats["total"] = len(articles)
        logger.info("粮达网今天发布 %d 篇文章", len(articles))

        if not articles:
            logger.info("今天没有新文章，跳过同步")
            return stats

        # 2. 过滤已同步的
        for article in articles:
            article_id = str(article["id"])
            title = article.get("title", "未知标题")

            if self.tracker.is_synced(article_id):
                logger.info("跳过已同步文章: [%s] %s", article_id, title)
                stats["skipped"] += 1
                continue

            # 3. 同步单篇文章
            try:
                self._sync_one(article_id, title)
                stats["new"] += 1
                stats["results"].append({"id": article_id, "title": title, "status": "ok"})
                logger.info("✅ 同步成功: [%s] %s", article_id, title)
            except Exception as e:
                stats["failed"] += 1
                stats["results"].append({"id": article_id, "title": title, "status": "fail", "error": str(e)})
                logger.error("❌ 同步失败: [%s] %s - %s", article_id, title, e)

        logger.info("===== 同步完成: 总计%d, 新增%d, 跳过%d, 失败%d =====",
                     stats["total"], stats["new"], stats["skipped"], stats["failed"])

        # 如果有新增文章，触发 AI 助理知识库更新
        if RELEARN_ENABLED and stats["new"] > 0:
            self._trigger_relearn()

        return stats

    # ----------------------------------------------------------------
    # 单篇文章同步
    # ----------------------------------------------------------------
    def _sync_one(self, article_id: str, title: str):
        """同步单篇文章到钉钉知识库"""
        # 1. 获取文章内容（Markdown）
        content = self.crawler.get_article_content(article_id)

        # 2. 在钉钉创建文档
        doc = self.dingtalk.create_document(
            workspace_id=DINGTALK_WORKSPACE_ID,
            parent_node_id=DINGTALK_PARENT_NODE_ID,
            name=title,
        )
        doc_key = doc.get("docKey", "")
        doc_url = doc.get("url", "")
        logger.info("钉钉文档创建成功: docKey=%s", doc_key)

        # 3. 写入 Markdown 内容
        self.dingtalk.overwrite_content(doc_key=doc_key, content=content)
        logger.info("钉钉文档内容写入成功: docKey=%s", doc_key)

        # 4. 标记已同步
        self.tracker.mark_synced(
            article_id=article_id,
            title=title,
            dingtalk_doc_key=doc_key,
            dingtalk_url=doc_url,
        )

    # ----------------------------------------------------------------
    # 查询统计
    # ----------------------------------------------------------------
    def show_status(self) -> dict:
        """显示同步状态"""
        return {
            "total_synced": self.tracker.count(),
            "recent": self.tracker.get_all_synced()[:10],
        }

    # ----------------------------------------------------------------
    # 首次历史数据全量同步
    # ----------------------------------------------------------------
    def sync_history(self, max_pages: int = 10) -> dict:
        """首次运行：同步历史文章

        Args:
            max_pages: 最多同步页数，每页50条
        """
        logger.info("===== 开始同步历史文章 =====")
        stats = {"total": 0, "new": 0, "skipped": 0, "failed": 0}

        for page in range(1, max_pages + 1):
            result = self.crawler.list_articles(page=page, size=50)
            records = result.get("records", [])
            if not records:
                break

            for article in records:
                stats["total"] += 1
                article_id = str(article["id"])
                title = article.get("title", "未知标题")

                if self.tracker.is_synced(article_id):
                    stats["skipped"] += 1
                    continue

                try:
                    self._sync_one(article_id, title)
                    stats["new"] += 1
                except Exception as e:
                    stats["failed"] += 1
                    logger.error("历史同步失败: [%s] %s - %s", article_id, title, e)

            logger.info("历史同步第 %d 页完成", page)

        logger.info("历史同步完成: 总计%d, 新增%d, 跳过%d, 失败%d",
                     stats["total"], stats["new"], stats["skipped"], stats["failed"])

        # 如果有新增文章，触发 AI 助理知识库更新
        if RELEARN_ENABLED and stats["new"] > 0:
            self._trigger_relearn()

        return stats

    # ----------------------------------------------------------------
    # AI 助理知识库更新触发
    # ----------------------------------------------------------------
    def _trigger_relearn(self):
        """触发 AI 助理重新学习知识库"""
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

    # ----------------------------------------------------------------
    # 价格指数同步
    # ----------------------------------------------------------------
    def _ensure_price_index_folder(self) -> str:
        """确保价格指数文件夹存在，返回 nodeId"""
        if self._price_index_folder_id:
            return self._price_index_folder_id
        folder_id = self.dingtalk.find_or_create_folder(
            workspace_id=DINGTALK_WORKSPACE_ID,
            parent_node_id=DINGTALK_PARENT_NODE_ID,
            name=PRICE_INDEX_FOLDER_NAME,
        )
        self._price_index_folder_id = folder_id
        return folder_id

    def sync_price_indices(self) -> dict:
        """同步所有品种的价格指数到知识库

        对每个品种×区域×地点×等级×价格类型的组合：
          1. 采集近 14 天价格数据
          2. 构建 Markdown 文档
          3. 写入钉钉知识库（覆盖已有文档）
        """
        logger.info("===== 开始同步价格指数 =====")
        stats = {"total": 0, "success": 0, "skipped": 0, "failed": 0, "results": []}

        # 确保价格指数文件夹存在
        try:
            folder_id = self._ensure_price_index_folder()
        except Exception as e:
            logger.error("获取价格指数文件夹失败: %s", e)
            return stats

        # 展开所有采集组合
        combinations = self.price_index.expand_all_combinations()
        stats["total"] = len(combinations)
        logger.info("价格指数共 %d 个采集组合", len(combinations))

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
            except Exception as e:
                logger.warning("构建文档失败 %s: %s", title, e)
                stats["failed"] += 1
                stats["results"].append({"title": title, "status": "build_fail", "error": str(e)})
                continue

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

                    # 写入内容；配额满时仅登记 docKey，内容待配额恢复后补齐
                    hash_value = ""
                    if self._quota_available():
                        self.dingtalk.overwrite_content(doc_key=doc_key, content=content)
                        self.tracker.bump_monthly_write_count()
                        hash_value = hashlib.sha1(content.encode("utf-8")).hexdigest()
                        stats["success"] += 1
                    else:
                        logger.warning("月度配额已满，跳过新增写入: %s", title)
                        stats["skipped"] += 1

                    # 记录 docKey（含内容哈希，未实际写入则为空）
                    self.tracker.mark_price_index_synced(
                        doc_key_id=doc_key_id,
                        title=title,
                        dingtalk_doc_key=doc_key,
                        dingtalk_url=doc_url,
                        content_hash=hash_value,
                    )
                    logger.info("已创建: %s", title)

                stats["results"].append({"title": title, "status": "ok"})

            except Exception as e:
                logger.warning("写入失败 %s: %s", title, e)
                stats["failed"] += 1
                stats["results"].append({"title": title, "status": "write_fail", "error": str(e)})

        logger.info("===== 价格指数同步完成: 总计%d, 成功%d, 跳过%d, 失败%d =====",
                     stats["total"], stats["success"], stats["skipped"], stats["failed"])
        return stats

    # ----------------------------------------------------------------
    # 农粮大数据同步
    # ----------------------------------------------------------------
    def _ensure_big_data_folder(self) -> str:
        """确保农粮大数据文件夹存在，返回 nodeId"""
        if self._big_data_folder_id:
            return self._big_data_folder_id
        folder_id = self.dingtalk.find_or_create_folder(
            workspace_id=DINGTALK_WORKSPACE_ID,
            parent_node_id=DINGTALK_PARENT_NODE_ID,
            name=BIG_DATA_FOLDER_NAME,
        )
        self._big_data_folder_id = folder_id
        return folder_id

    def _sync_one_big_data(self, article_id: str, title: str, folder_id: str):
        """同步单篇农粮大数据文章到钉钉知识库"""
        # 1. 获取文章内容（Markdown），columnType=1
        content = self.crawler.get_article_content(
            article_id, column_type=BIG_DATA_COLUMN_TYPE,
        )

        # 2. 在钉钉创建文档
        doc = self.dingtalk.create_document(
            workspace_id=DINGTALK_WORKSPACE_ID,
            parent_node_id=folder_id,
            name=title,
        )
        doc_key = doc.get("docKey", "")
        doc_url = doc.get("url", "")

        # 3. 写入 Markdown 内容
        self.dingtalk.overwrite_content(doc_key=doc_key, content=content)

        # 4. 标记已同步（用 bigdata: 前缀避免与普通文章 ID 冲突）
        self.tracker.mark_synced(
            article_id=f"bigdata:{article_id}",
            title=title,
            dingtalk_doc_key=doc_key,
            dingtalk_url=doc_url,
            extra=f'{{"columnType":"{BIG_DATA_COLUMN_TYPE}"}}',
        )

    def sync_big_data(self, days: int = 2) -> dict:
        """同步农粮大数据文章

        流程与 sync_today 相同，但使用 columnType=1。
        首次同步时传 days=24 覆盖整个 7 月，后续 daemon 用默认 days=2 增量检测。

        Args:
            days: 往前追溯的天数
        """
        logger.info("===== 开始同步农粮大数据 =====")
        stats = {"total": 0, "new": 0, "skipped": 0, "failed": 0, "results": []}

        # 确保文件夹存在
        try:
            folder_id = self._ensure_big_data_folder()
        except Exception as e:
            logger.error("获取农粮大数据文件夹失败: %s", e)
            return stats

        # 获取文章列表
        articles = self.crawler.list_recent_articles(
            days=days, column_type=BIG_DATA_COLUMN_TYPE,
        )
        stats["total"] = len(articles)
        logger.info("农粮大数据获取到 %d 篇文章", len(articles))

        if not articles:
            logger.info("没有新文章，跳过同步")
            return stats

        for article in articles:
            article_id = str(article["id"])
            title = article.get("title", "未知标题")

            if self.tracker.is_synced(f"bigdata:{article_id}"):
                logger.info("跳过已同步: [%s] %s", article_id, title)
                stats["skipped"] += 1
                continue

            try:
                self._sync_one_big_data(article_id, title, folder_id)
                stats["new"] += 1
                stats["results"].append({"id": article_id, "title": title, "status": "ok"})
                logger.info("同步成功: [%s] %s", article_id, title)
            except Exception as e:
                stats["failed"] += 1
                stats["results"].append({"id": article_id, "title": title, "status": "fail", "error": str(e)})
                logger.error("同步失败: [%s] %s - %s", article_id, title, e)

        logger.info("===== 农粮大数据同步完成: 总计%d, 新增%d, 跳过%d, 失败%d =====",
                     stats["total"], stats["new"], stats["skipped"], stats["failed"])
        return stats

    # ----------------------------------------------------------------
    # 华南粮网同步
    # ----------------------------------------------------------------
    def sync_huanan(self, year: int = 2026, month: int = 7) -> dict:
        """同步华南粮网文章到钉钉知识库

        采集指定年月的文章，同步到 HUANAN_TARGET_NODE_ID 节点下。
        已同步的文章会覆盖更新内容（含表格附件），新文章则创建文档。

        Args:
            year: 年份
            month: 月份
        """
        logger.info("===== 开始同步华南粮网 %d年%d月 文章 =====", year, month)
        stats = {"total": 0, "new": 0, "updated": 0, "skipped": 0, "failed": 0, "results": []}

        # 获取文章列表
        articles = huanan_crawler.list_articles_by_month(
            year=year, month=month, column_id=HUANAN_COLUMN_ID,
        )
        stats["total"] = len(articles)
        logger.info("华南粮网 %d年%d月 共 %d 篇文章", year, month, len(articles))

        if not articles:
            return stats

        for article in articles:
            article_id = article["iArticleid"]
            title = article.get("sTitle", "未知标题")
            track_key = f"huanan:{article_id}"

            # 查询已有记录（判断是新文章还是更新）
            existing = self.tracker.get_record(track_key)

            # 冷却期内跳过已有文章的整篇处理（含爬虫构建）
            if existing and self.tracker.is_within_cooldown(track_key, DAILY_WRITE_COOLDOWN_HOURS):
                stats["skipped"] += 1
                continue

            try:
                self._sync_one_huanan(article_id, title, track_key, existing)
                if existing:
                    stats["updated"] += 1
                    stats["results"].append({"id": article_id, "title": title, "status": "updated"})
                    logger.info("更新成功: [%s] %s", article_id, title)
                else:
                    stats["new"] += 1
                    stats["results"].append({"id": article_id, "title": title, "status": "ok"})
                    logger.info("同步成功: [%s] %s", article_id, title)
            except Exception as e:
                stats["failed"] += 1
                stats["results"].append({"id": article_id, "title": title, "status": "fail", "error": str(e)})
                logger.error("同步失败: [%s] %s - %s", article_id, title, e)

            # 避免触发华南粮网接口限流
            time.sleep(1)

        logger.info("===== 华南粮网同步完成: 总计%d, 新增%d, 更新%d, 跳过%d, 失败%d =====",
                     stats["total"], stats["new"], stats["updated"], stats["skipped"], stats["failed"])
        return stats

    def _sync_one_huanan(self, article_id: int, title: str, track_key: str,
                         existing_record: dict | None = None):
        """同步单篇华南粮网文章到钉钉知识库

        Args:
            existing_record: 已有同步记录，如果提供则覆盖更新文档内容
        """
        # 1. 获取详情
        detail = huanan_crawler.get_detail(article_id)

        # 2. 提取并下载附件
        attachments = huanan_crawler.extract_attachments(detail)
        local_files = []
        for at in attachments:
            f = huanan_crawler.download_attachment(at, article_id)
            if f:
                local_files.append(f)

        # 3. 构建 Markdown 文档（含表格内容）
        content = huanan_crawler.build_markdown(detail, local_files)

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

            # 写入内容；配额满时仅登记 docKey，内容待配额恢复后补齐
            hash_value = ""
            if self._quota_available():
                self.dingtalk.overwrite_content(doc_key=doc_key, content=content)
                self.tracker.bump_monthly_write_count()
                hash_value = hashlib.sha1(content.encode("utf-8")).hexdigest()
            else:
                logger.warning("月度配额已满，跳过新增写入: %s", title)

            # 标记已同步（含内容哈希）
            self.tracker.mark_synced(
                article_id=track_key,
                title=title,
                dingtalk_doc_key=doc_key,
                dingtalk_url=doc_url,
                extra=f'{{"source":"huanan","articleId":{article_id}}}',
                content_hash=hash_value,
            )

    # ----------------------------------------------------------------
    # 国家粮食交易中心（海南）同步
    # ----------------------------------------------------------------
    def sync_grainmarket(self, year: int = 2026, month: int = 7) -> dict:
        """同步国家粮食交易中心海南市场文章到钉钉知识库

        采集交易公告、交易清单、交易结果等分类，已同步的文章覆盖更新。

        Args:
            year: 年份
            month: 月份
        """
        logger.info("===== 开始同步海南交易中心 %d年%d月 文章 =====", year, month)
        stats = {"total": 0, "new": 0, "updated": 0, "skipped": 0, "failed": 0, "results": []}

        articles = grainmarket_crawler.list_articles_by_month(
            year=year, month=month,
            market_id=GRAINMARKET_MARKET_ID,
            article_types=GRAINMARKET_ARTICLE_TYPES,
        )
        stats["total"] = len(articles)
        logger.info("海南交易中心 %d年%d月 共 %d 篇文章", year, month, len(articles))

        if not articles:
            return stats

        for article in articles:
            article_id = str(article["articleID"])
            title = article.get("title", "未知标题")
            track_key = f"grainmarket:{article_id}"
            article_type_name = article.get("_articleTypeName", "")

            existing = self.tracker.get_record(track_key)

            # 冷却期内跳过已有文章的整篇处理（含爬虫构建）
            if existing and self.tracker.is_within_cooldown(track_key, DAILY_WRITE_COOLDOWN_HOURS):
                stats["skipped"] += 1
                continue

            try:
                self._sync_one_grainmarket(article_id, title, track_key,
                                           article_type_name, existing)
                if existing:
                    stats["updated"] += 1
                    stats["results"].append({"id": article_id, "title": title, "status": "updated"})
                else:
                    stats["new"] += 1
                    stats["results"].append({"id": article_id, "title": title, "status": "ok"})
                logger.info("%s: [%s] %s", "更新成功" if existing else "同步成功", article_id, title)
            except Exception as e:
                stats["failed"] += 1
                stats["results"].append({"id": article_id, "title": title, "status": "fail", "error": str(e)})
                logger.error("同步失败: [%s] %s - %s", article_id, title, e)

            # 避免触发接口限流
            import time
            time.sleep(0.5)

        logger.info("===== 海南交易中心同步完成: 总计%d, 新增%d, 更新%d, 失败%d =====",
                     stats["total"], stats["new"], stats["updated"], stats["failed"])
        return stats

    def _sync_one_grainmarket(self, article_id: str, title: str, track_key: str,
                              article_type_name: str = "",
                              existing_record: dict | None = None):
        """同步单篇海南交易中心文章到钉钉知识库"""
        # 1. 获取详情
        detail = grainmarket_crawler.get_detail(article_id)

        # 2. 提取并下载附件
        attachments = grainmarket_crawler.extract_attachments(detail)
        local_files = []
        for at in attachments:
            f = grainmarket_crawler.download_attachment(at, article_id)
            if f:
                local_files.append(f)

        # 3. 构建 Markdown 文档（OCR 识别图片中的表格）
        content = grainmarket_crawler.build_markdown(
            detail, local_files, article_type_name=article_type_name,
            article_id=article_id,
        )

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
            # 写入内容；配额满时仅登记 docKey，内容待配额恢复后补齐
            hash_value = ""
            if self._quota_available():
                self.dingtalk.overwrite_content(doc_key=doc_key, content=content)
                self.tracker.bump_monthly_write_count()
                hash_value = hashlib.sha1(content.encode("utf-8")).hexdigest()
            else:
                logger.warning("月度配额已满，跳过新增写入: %s", title)

            self.tracker.mark_synced(
                article_id=track_key,
                title=title,
                dingtalk_doc_key=doc_key,
                dingtalk_url=doc_url,
                extra=f'{{"source":"grainmarket","articleId":"{article_id}","type":"{article_type_name}"}}',
                content_hash=hash_value,
            )

    # ----------------------------------------------------------------
    # 钉钉机器人通知
    # ----------------------------------------------------------------
    def send_new_article_notifications(self, all_stats: dict) -> None:
        """汇总各数据源的新增情况，发送钉钉机器人通知"""
        if not DINGTALK_ROBOT_WEBHOOK:
            return

        sources = [
            ("粮达网", "articles", "", "ok"),
            ("华南粮网", "huanan", "huanan:", "ok"),
            ("海南交易中心", "grainmarket", "grainmarket:", "ok"),
            ("农粮大数据", "big_data", "bigdata:", "new"),
        ]

        for source_name, key, prefix, status_field in sources:
            stats = all_stats.get(key)
            if not stats or stats.get("new", 0) == 0:
                continue

            new_articles = []
            for r in stats.get("results", []):
                if r.get("status") == status_field and r.get("title"):
                    track_key = f"{prefix}{r['id']}"
                    record = self.tracker.get_record(track_key)
                    url = record.get("dingtalk_url", "") if record else ""
                    new_articles.append({"title": r["title"], "url": url})

            if new_articles:
                msg = build_new_articles_message(source_name, new_articles)
                send_notification(
                    webhook_url=DINGTALK_ROBOT_WEBHOOK,
                    title=f"数据采集 - {source_name}",
                    markdown_text=msg,
                )
