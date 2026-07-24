"""粮达网爬虫

封装粮达网分析报告相关的数据采集接口。
"""
import logging
import requests
import html2text

logger = logging.getLogger(__name__)

BASE_URL = "https://www.liangdawang.com"
LIST_URL = f"{BASE_URL}/ldw-portal-mer/v1/analysisReport/list"
DETAIL_URL = f"{BASE_URL}/ldw-portal-mer/v1/analysisReport/list"


class LiangDaWangCrawler:
    """粮达网爬虫"""

    def __init__(self):
        self._converter = html2text.HTML2Text()
        self._converter.body_width = 0          # 不自动换行
        self._converter.ignore_links = False
        self._converter.ignore_images = False
        self._converter.ignore_emphasis = False
        self._converter.protect_links = True
        self._converter.unicode_snob = True

    # ----------------------------------------------------------------
    # 文章列表
    # ----------------------------------------------------------------
    def list_articles(self, page: int = 1, size: int = 50,
                      column_type: str = "",
                      variety_class: str = "",
                      type_id: str = "") -> dict:
        """获取分析报告列表

        返回:
            {
                "records": [ { id, title, createTime, views, ... }, ... ],
                "total": int,
                ...
            }
        """
        resp = requests.post(LIST_URL, json={
            "page": page,
            "size": size,
            "columnType": column_type,
            "varietyClass": variety_class,
            "typeId": type_id,
        })
        data = resp.json()
        if not data.get("success"):
            raise RuntimeError(f"粮达网列表接口失败: {data.get('msg')}")
        return data["data"]

    def list_recent_articles(self, days: int = 2, **kwargs) -> list[dict]:
        """获取最近 N 天发布的文章列表（遍历分页）

        Args:
            days: 往前追溯的天数，默认 2 天（昨天+今天）
        """
        cutoff = _days_ago_str(days)
        all_records = []
        page = 1
        size = 50

        while True:
            result = self.list_articles(page=page, size=size, **kwargs)
            records = result.get("records", [])
            if not records:
                break

            for r in records:
                create_date = r.get("createTime", "")[:10]
                if create_date >= cutoff:
                    all_records.append(r)
                else:
                    # 列表按时间倒序，后面的日期更早，不用继续翻了
                    return all_records

            total = result.get("total", 0)
            if page * size >= total:
                break
            page += 1

        return all_records

    # ----------------------------------------------------------------
    # 文章详情
    # ----------------------------------------------------------------
    def get_article_detail(self, article_id: str, column_type: str = "0") -> dict:
        """获取文章详情"""
        resp = requests.get(f"{DETAIL_URL}/{article_id}", params={
            "columnType": column_type,
        })
        data = resp.json()
        if not data.get("success"):
            raise RuntimeError(f"粮达网详情接口失败: {data.get('msg')}")
        return data["data"]

    def get_article_content(self, article_id: str, column_type: str = "0") -> str:
        """获取文章的完整 Markdown 内容（含标题、AI摘要、正文）"""
        detail = self.get_article_detail(article_id, column_type)

        title = detail.get("title", "")
        create_time = detail.get("createTime", "")
        html_content = detail.get("content", "")
        ai_content = detail.get("aiContent", "")

        # HTML 转 Markdown
        body_md = self._converter.handle(html_content).strip()

        # 组装
        parts = [f"# {title}",
                 "",
                 f"> **文章来源**：粮达网",
                 f"> **发布时间**：{create_time}",
                 "",
                 "---",
                 ""]

        if ai_content:
            parts.extend(["## AI 摘要", "", ai_content, "", "---", ""])

        parts.extend(["## 正文", "", body_md, "",
                      "---",
                      "",
                      "*本文由粮达网数据采集系统自动同步至钉钉知识库*"])

        return "\n".join(parts)


def _days_ago_str(days: int) -> str:
    """返回 N 天前的日期字符串 YYYY-MM-DD"""
    from datetime import datetime, timedelta
    return (datetime.now() - timedelta(days=days - 1)).strftime("%Y-%m-%d")
