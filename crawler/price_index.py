"""粮达网价格指数采集器

采集各品种、区域、地点的价格指数数据，生成 Markdown 文档。
"""
import logging
from typing import Any
from datetime import datetime, timedelta

import requests
from config import PRICE_INDEX_DAYS, PRICE_INDEX_EXCLUDE_VARIETIES, PRICE_INDEX_DEEP_KEEP_AREAS

logger = logging.getLogger(__name__)

BASE_URL = "https://www.liangdawang.com"
MER_API = f"{BASE_URL}/ldw-portal-mer/v1"


class PriceIndexCollector:
    """价格指数采集器"""

    HEADERS = {"User-Agent": "Mozilla/5.0"}

    # ----------------------------------------------------------------
    # 获取所有品种配置
    # ----------------------------------------------------------------
    def get_variety_list(self) -> list[dict]:
        """获取所有品种"""
        resp = requests.get(
            f"{MER_API}/informationPriceVariety/getVarietyNameByMap",
            headers=self.HEADERS,
        )
        return resp.json().get("data", [])

    def get_variety_tree(self, variety_id: str) -> list[dict]:
        """获取品种的区域/地区/地点树"""
        resp = requests.get(
            f"{MER_API}/informationPriceVariety/getVarietyTree",
            params={"varietyId": variety_id},
            headers=self.HEADERS,
        )
        return resp.json().get("data", [])

    def get_rank_and_type(self, variety_name: str,
                          region_code_cn: str,
                          district_code_cn: str,
                          area: str) -> dict:
        """获取等级和价格类型选项"""
        resp = requests.post(
            f"{MER_API}/informationPriceVariety/getRankAndType",
            json={
                "varietyName": variety_name,
                "regionCodeCn": region_code_cn,
                "districtCodeCn": district_code_cn,
                "area": area,
            },
            headers=self.HEADERS,
        )
        return resp.json()

    # ----------------------------------------------------------------
    # 获取价格数据
    # ----------------------------------------------------------------
    def get_price_chart(self, variety_name: str, area_type: str,
                        province: str, area: str,
                        rank: str, price_type: str) -> list[dict]:
        """获取价格走势数据

        Returns:
            [{price, priceDate, priceDiff, ...}, ...]
        """
        resp = requests.get(
            f"{MER_API}/infoCenter/getPriceChart",
            params={
                "areaType": area_type,
                "province": province,
                "area": area,
                "varietyName": variety_name,
                "rank": rank,
                "priceType": price_type,
            },
            headers=self.HEADERS,
        )
        data = resp.json()
        if not data.get("success"):
            logger.warning("获取价格数据失败: %s", data.get("msg", ""))
            return []
        return data["data"].get("priceByArea", {}).get("priceIndexBOs", [])

    # ----------------------------------------------------------------
    # 展开所有采集组合
    # ----------------------------------------------------------------
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

                        # 等级精简：每个地点只保留主等级（列表第一个），
                        # 避免多等级展开放大钉钉覆盖写入调用量
                        primary_rank = ranks[0] if ranks else None
                        if primary_rank is None:
                            continue

                        # 每个价格类型作为一个采集组合
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
                                "rank": primary_rank,
                                "price_type": pt,
                            })

        return combinations

    # ----------------------------------------------------------------
    # 构建 Markdown 文档
    # ----------------------------------------------------------------
    def build_document(self, config: dict) -> str:
        """采集价格数据并构建 Markdown 文档"""
        # 获取价格数据（近 N 天）
        all_prices = self.get_price_chart(
            variety_name=config["variety_name"],
            area_type=config["area_type"],
            province=config["province"],
            area=config["area"],
            rank=config["rank"],
            price_type=config["price_type"],
        )

        if not all_prices:
            logger.info("无价格数据: %s", self._doc_title(config))
            return ""

        # 筛选近 N 天
        cutoff = (datetime.now() - timedelta(days=PRICE_INDEX_DAYS)).strftime("%Y-%m-%d")
        recent = [p for p in all_prices if p.get("priceDate", "") >= cutoff]
        recent.sort(key=lambda p: p.get("priceDate", ""))

        if not recent:
            return ""

        # 计算统计
        prices_num = []
        for p in recent:
            try:
                prices_num.append(float(p["price"]))
            except (ValueError, KeyError):
                pass

        title = self._doc_title(config)
        lines = [f"# {title}", ""]
        lines.append(f"**品种**: {config['variety_name']}  ")
        lines.append(f"**区域**: {config['area_type']} / {config['province']} / {config['area']}  ")
        lines.append(f"**等级**: {config['rank']}  ")
        lines.append(f"**价格类型**: {config['price_type']}  ")
        # 数据截止日期取价格数据最后一天，保证内容只在数据更新时变化
        if recent:
            last_date = recent[-1].get("priceDate", "") or ""
            if last_date:
                lines.append(f"**数据截止日期**: {last_date}  ")
        lines.append("")
        lines.append("---")
        lines.append("")

        # 价格表
        lines.append(f"## 近{PRICE_INDEX_DAYS}日价格")
        lines.append("")
        lines.append("| 日期 | 价格(元/吨) | 涨跌 | 去年同期 | 备注 |")
        lines.append("|------|------------|------|---------|------|")
        for p in recent:
            date = p.get("priceDate", "")[5:]  # MM-DD
            price = p.get("price", "")
            diff = p.get("priceDiff", "")
            last_year = p.get("lastYearPrice", "-")
            remark = p.get("remark", "")
            lines.append(f"| {date} | {price} | {diff} | {last_year} | {remark} |")

        lines.append("")

        # 统计
        if prices_num:
            lines.append("## 统计")
            lines.append("")
            lines.append(f"- 近{PRICE_INDEX_DAYS}日最高价：{max(prices_num):.0f} 元/吨")
            lines.append(f"- 近{PRICE_INDEX_DAYS}日最低价：{min(prices_num):.0f} 元/吨")
            if len(prices_num) > 1:
                avg = sum(prices_num) / len(prices_num)
                lines.append(f"- 近{PRICE_INDEX_DAYS}日均价：{avg:.0f} 元/吨")
                trend = prices_num[-1] - prices_num[0]
                if trend > 0:
                    lines.append(f"- 近期趋势：上涨 {trend:.0f} 元/吨")
                elif trend < 0:
                    lines.append(f"- 近期趋势：下跌 {abs(trend):.0f} 元/吨")
                else:
                    lines.append("- 近期趋势：持平")

        lines.append("")
        lines.append("---")
        lines.append("")
        lines.append("*数据来源：粮达网 | 由数据采集系统自动同步*")

        return "\n".join(lines)

    @staticmethod
    def _doc_title(config: dict) -> str:
        """生成文档标题"""
        return (f"{config['variety_name']}价格指数"
                f"_{config['area']}"
                f"（{config['rank']}/{config['price_type']}）")

    @staticmethod
    def doc_key(config: dict) -> str:
        """生成组合的唯一标识 key（用于跟踪 docKey）"""
        return (f"price_index:{config['variety_id']}:{config['area_type']}"
                f":{config['province']}:{config['area']}"
                f":{config['rank']}:{config['price_type']}")
