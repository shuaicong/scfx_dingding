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
    # 玉米、小麦两个品种均产生石家庄的深加工组合，故用集合断言白名单生效
    assert {c["area"] for c in deep} == {"石家庄"}


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
