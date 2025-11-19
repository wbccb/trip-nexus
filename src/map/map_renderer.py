import folium
from folium import Marker, PolyLine, Icon
from geopy.geocoders import Nominatim
from geopy.exc import GeocoderTimedOut, GeocoderServiceError
from typing import Dict, List, Tuple, Optional, Any
import time


class TripMap:
    def __init__(self):
        self.geolocator = Nominatim(
            user_agent="trip_nexus_py312_v3",
            timeout=15,
            domain="nominatim.openstreetmap.org"
        )
        self.colors = ["blue", "green", "red", "purple", "orange", "darkblue"]

    def _get_coordinates(self, address: str) -> Tuple[float, float]:
        """地址转经纬度，带多重重试"""
        for attempt in range(3):
            try:
                location = self.geolocator.geocode(address, exactly_one=True)
                if location:
                    return (location.latitude, location.longitude)
                city = address.split(",")[-1].strip()
                location = self.geolocator.geocode(city, exactly_one=True)
                if location:
                    return (location.latitude, location.longitude)
            except (GeocoderTimedOut, GeocoderServiceError):
                time.sleep(2 ** attempt)
        return (30.6570, 104.0650)  # 成都默认坐标

    def render_map(self, trip_data: Dict[str, Any]) -> folium.Map:
        """生成行程地图（folium 0.20.0兼容）"""
        dest = trip_data["destination"]

        # 假设 _get_coordinates 存在于 self 中，并且能够获取城市的坐标
        center_coords = self._get_coordinates(dest)
        m = folium.Map(location=center_coords, zoom_start=12, tiles="CartoDB positron")

        # --- 核心修正区域 ---

        # 1. 适配 LLM 的错误输出结构
        daily_plan_raw = trip_data["daily_plan"]

        # 检查 daily_plan 是否是预期的字典结构
        if isinstance(daily_plan_raw, dict):
            # 如果是正确的字典结构（键为 '1', '2' 等）
            daily_plans_grouped: Dict[str, List[Dict[str, str]]] = daily_plan_raw
        elif isinstance(daily_plan_raw, list):
            # 如果是错误的平铺列表结构（例如 LLM 忘记分组）
            # 将其视为第 1 天的行程
            daily_plans_grouped = {"1": daily_plan_raw}
        else:
            # 如果是 None 或其他意外类型，直接返回基础地图
            print(f"警告：daily_plan 数据类型异常，无法渲染。类型: {type(daily_plan_raw)}")
            return m

        # 2. 迭代修正后的分组
        # 现在 daily_plans_grouped 是 Dict[str, List[Dict[str, str]]] 类型
        for day_str, items in daily_plans_grouped.items():
            # 在这里 day_str 可能是 '1', '2', ... 或者由于修正只可能是 '1'
            try:
                day_idx = int(day_str) - 1
            except ValueError:
                print(f"警告：无法解析日期字符串 '{day_str}' 为数字，跳过。")
                continue

            coords_list: List[Tuple[float, float]] = []

            # 3. 迭代当天的行程项，并检查关键字段
            for idx, item in enumerate(items):
                # 确保 'address' 字段存在且非空，以处理 LLM 输出的残缺项
                if not item.get("address"):
                    print(f"警告：第{day_str}天第{idx + 1}项行程缺少地址，跳过。")
                    continue

                # 假设 self._get_coordinates 存在且返回 (lat, lon)
                coords = self._get_coordinates(item["address"])

                # 检查坐标有效性 (假设无效坐标返回的是 (0, 0) 或 None)
                if not coords or all(c == 0 for c in coords):
                    print(f"警告：无法获取地址 '{item['address']}' 的有效坐标，跳过。")
                    continue

                coords_list.append(coords)

                # 标记点
                folium.Marker(
                    location=coords,
                    popup=f"""
                    <b>第{day_str}天</b><br>
                    {item['time']}：{item['attraction']}<br>
                    交通：{item['transport']}
                    """,
                    # 假设 self.colors 已定义
                    icon=folium.Icon(
                        color=self.colors[day_idx % len(self.colors)],
                        icon="map-marker",
                        prefix="fa"
                    ),
                    tooltip=item["attraction"]
                ).add_to(m)

            # 4. 绘制路线
            if len(coords_list) >= 2:
                folium.PolyLine(
                    locations=coords_list,
                    color=self.colors[day_idx % len(self.colors)],
                    weight=3,
                    opacity=0.7,
                    tooltip=f"第{day_str}天路线"
                ).add_to(m)

        return m