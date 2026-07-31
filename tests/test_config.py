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
    assert MONTHLY_QUOTA_LIMIT == 4000
