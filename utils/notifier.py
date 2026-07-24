"""钉钉机器人通知

通过群自定义机器人 Webhook 发送采集结果通知。
"""
import json
import logging
from datetime import datetime

import requests

logger = logging.getLogger(__name__)


def send_notification(webhook_url: str, title: str, markdown_text: str) -> bool:
    """发送钉钉群机器人消息（markdown 类型）

    Args:
        webhook_url: 机器人 Webhook URL
        title: 消息标题
        markdown_text: Markdown 正文

    Returns:
        是否发送成功
    """
    if not webhook_url:
        logger.debug("未配置机器人 Webhook，跳过通知")
        return False

    payload = {
        "msgtype": "markdown",
        "markdown": {
            "title": title,
            "text": markdown_text,
        },
    }

    try:
        resp = requests.post(
            webhook_url,
            headers={"Content-Type": "application/json"},
            json=payload,
            timeout=10,
        )
        data = resp.json()
        if data.get("errcode") == 0:
            logger.info("钉钉机器人通知发送成功: %s", title[:50])
            return True
        else:
            logger.warning("钉钉机器人通知发送失败: %s", data.get("errmsg", "未知错误"))
            return False
    except Exception as e:
        logger.warning("钉钉机器人通知发送异常: %s", e)
        return False


def build_new_articles_message(
    source_name: str,
    articles: list[dict],
    stats: dict | None = None,
) -> str:
    """构建新增文章通知的 Markdown 文本

    Args:
        source_name: 数据源名称（如"海南交易中心"、"华南粮网"）
        articles: 新增文章列表，每项含 title, url（钉钉链接）等
        stats: 可选的完整统计信息

    Returns:
        Markdown 文本
    """
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    # 首行包含关键词 scfx（钉钉机器人自定义关键词校验）
    lines = [
        f"## scfx 数据采集通知",
        "",
        f"**来源**：{source_name}",
        f"**新增**：{len(articles)} 篇",
        "",
    ]

    if articles:
        lines.append("---")
        lines.append("")
        for i, a in enumerate(articles, 1):
            title = a.get("title", "未知标题")
            url = a.get("url", "")
            if url:
                lines.append(f"{i}. [{title}]({url})")
            else:
                lines.append(f"{i}. {title}")
        lines.append("")

    lines.append(f"---")
    lines.append(f"*scfx 采集时间：{now}*")
    return "\n".join(lines)
