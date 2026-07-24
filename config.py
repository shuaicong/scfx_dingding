"""项目配置

所有敏感信息支持通过环境变量覆盖，便于 Docker 部署：
    export DINGTALK_APP_KEY=your_key
    export DINGTALK_APP_SECRET=your_secret
    ...

优先级：环境变量 > 代码中的默认值
"""

import os


def _env(key: str, default: str) -> str:
    """获取环境变量，若不存在则返回默认值"""
    return os.environ.get(key, default)


def _env_bool(key: str, default: bool) -> bool:
    """获取布尔型环境变量"""
    val = os.environ.get(key)
    if val is None:
        return default
    return val.lower() in ("1", "true", "yes", "on")


def _env_int(key: str, default: int) -> int:
    """获取整型环境变量"""
    val = os.environ.get(key)
    if val is None:
        return default
    try:
        return int(val)
    except ValueError:
        return default


# ========== 钉钉应用配置 ==========
DINGTALK_APP_KEY = _env("DINGTALK_APP_KEY", "dingn6dciq8ck1gdfode")
DINGTALK_APP_SECRET = _env("DINGTALK_APP_SECRET", "Em649XIuIudeBoPIknSuLrClbDpZLRUWhl1nhUqSF7wOPIE7K8NiZzdXBH6NnoMA")

# ========== 钉钉知识库配置 ==========
# 通过查询用户详情接口获取
DINGTALK_UNION_ID = _env("DINGTALK_UNION_ID", "iPlw6rn8iPGTiP53yETUWVlzwiEiE")
# 知识库 workspaceId
DINGTALK_WORKSPACE_ID = _env("DINGTALK_WORKSPACE_ID", "xPar2SLeQBwGlGaV")
# 粮达网文件夹节点ID（创建文档时作为 parentNodeId）
DINGTALK_PARENT_NODE_ID = _env("DINGTALK_PARENT_NODE_ID", "QBnd5ExVEv6B7zgQFAOg7099JyeZqMmz")

# ========== AI 助理知识库更新触发配置 ==========
# 同步完成后是否自动触发 AI 助理重新学习知识库
RELEARN_ENABLED = _env_bool("RELEARN_ENABLED", True)
# 触发时是否使用无头浏览器（后台静默执行）
RELEARN_HEADLESS = _env_bool("RELEARN_HEADLESS", True)

# ========== 钉钉机器人通知配置 ==========
# 在钉钉群中添加自定义机器人后获取的 Webhook URL
# 留空则不发送通知。格式: https://oapi.dingtalk.com/robot/send?access_token=xxx
DINGTALK_ROBOT_WEBHOOK = _env("DINGTALK_ROBOT_WEBHOOK", "https://oapi.dingtalk.com/robot/send?access_token=c35e29d8f056a9f1c8280192a2f3cf125eb2d2e74e8f2dc931f32efb2f8529c4")

# ========== 粮达网配置 ==========
LIANGDAWANG_BASE_URL = _env("LIANGDAWANG_BASE_URL", "https://www.liangdawang.com")
LIANGDAWANG_LIST_API = _env("LIANGDAWANG_LIST_API", "/ldw-portal-mer/v1/analysisReport/list")
LIANGDAWANG_DETAIL_API = _env("LIANGDAWANG_DETAIL_API", "/ldw-portal-mer/v1/analysisReport/list")

# ========== 价格指数配置 ==========
# 知识库粮达网目录下的子文件夹名
PRICE_INDEX_FOLDER_NAME = _env("PRICE_INDEX_FOLDER_NAME", "价格指数")
# 每次采集近 N 天的数据
PRICE_INDEX_DAYS = _env_int("PRICE_INDEX_DAYS", 14)

# ========== 农粮大数据配置 ==========
# 知识库粮达网目录下的子文件夹名
BIG_DATA_FOLDER_NAME = _env("BIG_DATA_FOLDER_NAME", "农粮大数据")
# 农粮大数据的 columnType 值
BIG_DATA_COLUMN_TYPE = _env("BIG_DATA_COLUMN_TYPE", "1")

# ========== 华南粮网配置 ==========
# 华南粮网栏目ID（2=交易结果公告）
HUANAN_COLUMN_ID = _env_int("HUANAN_COLUMN_ID", 2)
# 华南粮网对应的钉钉知识库 workspace
HUANAN_WORKSPACE_ID = _env("HUANAN_WORKSPACE_ID", "oG8LRSYNK2qbk1v5")
# 华南粮网目标节点（用户指定的文件夹）
HUANAN_TARGET_NODE_ID = _env("HUANAN_TARGET_NODE_ID", "l6Pm2Db8D461n2DyhjOw9A638xLq0Ee4")

# ========== 国家粮食交易中心（海南）配置 ==========
# 海南交易中心市场ID
GRAINMARKET_MARKET_ID = _env("GRAINMARKET_MARKET_ID", "S001031")
# 海南交易中心对应的钉钉知识库 workspace
GRAINMARKET_WORKSPACE_ID = _env("GRAINMARKET_WORKSPACE_ID", "oG8LRSYNK2qbk1v5")
# 海南交易中心目录节点ID
GRAINMARKET_TARGET_NODE_ID = _env("GRAINMARKET_TARGET_NODE_ID", "pLmkKOwY9bpPgm2g")
# 采集的文章类型（11=交易结果）
GRAINMARKET_ARTICLE_TYPES = _env("GRAINMARKET_ARTICLE_TYPES", "11").split(",")

# ========== 调度配置 ==========
# 轮询间隔（分钟），每隔 N 分钟检测一次新文章
SCHEDULE_INTERVAL_MINUTES = _env_int("SCHEDULE_INTERVAL_MINUTES", 30)

# ========== 数据存储 ==========
DB_DIR = os.environ.get("DB_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "data"))
DB_PATH = os.path.join(DB_DIR, "sync_tracker.db")
