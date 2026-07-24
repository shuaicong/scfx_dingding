"""粮达网 → 钉钉知识库 数据采集同步系统

使用方式:
    # 立即执行一次同步
    python main.py

    # 同步历史文章（前 N 页）
    python main.py --history --pages 5

    # 查看同步状态
    python main.py --status

    # 以调度模式运行（每30分钟自动检测新文章）
    python main.py --daemon
"""
import argparse
import logging
import sys

from apscheduler.schedulers.blocking import BlockingScheduler

from config import SCHEDULE_INTERVAL_MINUTES
from sync.engine import SyncEngine

# 日志配置
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("main")


def do_sync():
    """执行一次同步任务（文章 + 价格指数 + 农粮大数据 + 华南粮网）"""
    from datetime import datetime
    engine = SyncEngine()
    article_stats = engine.sync_today()
    logger.info("文章同步完成: 总计%d, 新增%d, 跳过%d, 失败%d",
                article_stats["total"], article_stats["new"],
                article_stats["skipped"], article_stats["failed"])
    price_stats = engine.sync_price_indices()
    logger.info("价格指数同步完成: 总计%d, 成功%d, 跳过%d, 失败%d",
                price_stats["total"], price_stats["success"],
                price_stats["skipped"], price_stats["failed"])
    big_stats = engine.sync_big_data()
    logger.info("农粮大数据同步完成: 总计%d, 新增%d, 跳过%d, 失败%d",
                big_stats["total"], big_stats["new"],
                big_stats["skipped"], big_stats["failed"])
    now = datetime.now()
    huanan_stats = engine.sync_huanan(year=now.year, month=now.month)
    logger.info("华南粮网同步完成: 总计%d, 新增%d, 更新%d, 失败%d",
                huanan_stats["total"], huanan_stats["new"],
                huanan_stats["updated"], huanan_stats["failed"])
    grain_stats = engine.sync_grainmarket(year=now.year, month=now.month)
    logger.info("海南交易中心同步完成: 总计%d, 新增%d, 更新%d, 失败%d",
                grain_stats["total"], grain_stats["new"],
                grain_stats["updated"], grain_stats["failed"])

    all_stats = {
        "articles": article_stats,
        "price_indices": price_stats,
        "big_data": big_stats,
        "huanan": huanan_stats,
        "grainmarket": grain_stats,
    }

    # 有新增数据时发送钉钉机器人通知
    engine.send_new_article_notifications(all_stats)

    return all_stats


def show_status():
    """显示同步状态"""
    engine = SyncEngine()
    status = engine.show_status()
    print(f"\n已同步文章总数: {status['total_synced']}")
    print("\n最近同步记录:")
    for row in status["recent"]:
        print(f"  [{row[0]}] {row[1]}  |  {row[2]}")
    print()


def run_daemon():
    """以守护模式运行，定时执行同步"""
    scheduler = BlockingScheduler()

    scheduler.add_job(
        do_sync,
        trigger="interval",
        minutes=SCHEDULE_INTERVAL_MINUTES,
        id="sync_liangdawang",
        name="粮达网定时同步",
        misfire_grace_time=120,
    )

    logger.info("调度器已启动，每隔 %d 分钟检测一次新文章", SCHEDULE_INTERVAL_MINUTES)
    logger.info("按 Ctrl+C 停止")

    try:
        scheduler.start()
    except KeyboardInterrupt:
        logger.info("调度器已停止")


def main():
    parser = argparse.ArgumentParser(
        description="粮达网 → 钉钉知识库 数据采集同步系统"
    )
    parser.add_argument("--status", action="store_true", help="查看同步状态")
    parser.add_argument("--daemon", action="store_true", help="以守护模式运行（定时调度）")
    parser.add_argument("--history", action="store_true", help="同步历史文章")
    parser.add_argument("--pages", type=int, default=10, help="历史同步页数（每页50条）")
    parser.add_argument("--big-data", action="store_true", help="同步农粮大数据文章")
    parser.add_argument("--all-july", action="store_true", help="首次同步整个7月农粮大数据")
    parser.add_argument("--huanan", action="store_true", help="同步华南粮网文章")
    parser.add_argument("--grainmarket", action="store_true", help="同步国家粮食交易中心海南市场文章")
    args = parser.parse_args()

    if args.status:
        show_status()
    elif args.daemon:
        run_daemon()
    elif args.history:
        engine = SyncEngine()
        stats = engine.sync_history(max_pages=args.pages)
        print(f"历史同步完成: 总计{stats['total']}, 新增{stats['new']}, 跳过{stats['skipped']}, 失败{stats['failed']}")
    elif args.big_data or args.all_july:
        engine = SyncEngine()
        days = 24 if args.all_july else 2
        stats = engine.sync_big_data(days=days)
        print(f"农粮大数据同步完成: 总计{stats['total']}, 新增{stats['new']}, 跳过{stats['skipped']}, 失败{stats['failed']}")
    elif args.huanan:
        from datetime import datetime
        engine = SyncEngine()
        now = datetime.now()
        stats = engine.sync_huanan(year=now.year, month=now.month)
        print(f"华南粮网同步完成: 总计{stats['total']}, 新增{stats['new']}, 更新{stats['updated']}, 失败{stats['failed']}")
        if stats.get("new", 0) > 0:
            engine.send_new_article_notifications({"huanan": stats})
    elif args.grainmarket:
        from datetime import datetime
        engine = SyncEngine()
        now = datetime.now()
        stats = engine.sync_grainmarket(year=now.year, month=now.month)
        print(f"海南交易中心同步完成: 总计{stats['total']}, 新增{stats['new']}, 更新{stats['updated']}, 失败{stats['failed']}")
        if stats.get("new", 0) > 0:
            engine.send_new_article_notifications({"grainmarket": stats})
    else:
        # 默认：立即执行一次同步
        stats = do_sync()
        print(f"\n同步完成: 总计{stats['total']}, 新增{stats['new']}, 跳过{stats['skipped']}, 失败{stats['failed']}")


if __name__ == "__main__":
    main()
