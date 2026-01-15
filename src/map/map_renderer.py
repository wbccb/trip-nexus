import folium
from folium import PolyLine
from folium.plugins import MarkerCluster
from folium.features import DivIcon
from geopy.geocoders import Nominatim
from geopy.exc import GeocoderTimedOut, GeocoderServiceError
from typing import Dict, List, Tuple, Any
import time
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

logging.getLogger("urllib3").setLevel(logging.ERROR)

AMAP_STREET_TILES = "http://webrd02.is.autonavi.com/appmaptile?lang=zh_cn&size=1&scale=1&style=7&x={x}&y={y}&z={z}"
AMAP_SATELLITE_TILES = "http://webst02.is.autonavi.com/appmaptile?style=6&x={x}&y={y}&z={z}"


class TripMap:
    def __init__(self):
        # Nominatim - OpenStreetMap 提供的免費替代地理編碼服務
        self.geolocator = Nominatim(
            user_agent="trip_nexus_py312_v3",
            timeout=15,
            domain="nominatim.openstreetmap.org",
        )
        self.colors = ["blue", "green", "red", "purple", "orange", "darkblue"]

    def _get_coordinates(self, address: str) -> Tuple[float, float]:
        for attempt in range(3):
            try:
                location = self.geolocator.geocode(address, exactly_one=True)
                if location:
                    return (location.latitude, location.longitude)
                city = address.split(",")[-1].strip()
                location = self.geolocator.geocode(city, exactly_one=True)
                if location:
                    return (location.latitude, location.longitude)
            except (GeocoderTimedOut, GeocoderServiceError, Exception) as e:
                print(f"[MapRenderer] geocode error for '{address}' attempt {attempt + 1}: {e}")
                time.sleep(2**attempt)
        return (30.6570, 104.0650)

    def _create_icon(self, day_idx: int, item: Dict[str, Any]) -> DivIcon:
        color = self.colors[day_idx % len(self.colors)]
        html = (
            f'<div style="width:14px;height:14px;border-radius:50%;'
            f'background-color:{color};border:2px solid white;"></div>'
        )
        return DivIcon(html=html, icon_size=(14, 14), class_name="poi-marker")

    def _build_base_map(self, center_coords: Tuple[float, float]) -> folium.Map:
        m = folium.Map(location=center_coords, zoom_start=12, tiles=None)

        folium.TileLayer(
            tiles=AMAP_STREET_TILES,
            attr="高德地图",
            name="高德街道",
            overlay=False,
            control=True,
        ).add_to(m)

        folium.TileLayer(
            tiles=AMAP_SATELLITE_TILES,
            attr="高德地图",
            name="高德卫星",
            overlay=False,
            control=True,
        ).add_to(m)

        folium.TileLayer(
            tiles="CartoDB positron",
            name="CartoDB Positron",
            overlay=False,
            control=True,
        ).add_to(m)

        folium.LayerControl().add_to(m)
        return m

    def render_map(self, trip_data: Dict[str, Any]) -> folium.Map:
        logger.info("\n\n------------------!!开始渲染地图!!------------------\n\n")
        print(f"[MapRenderer] render_map input trip_data keys: {trip_data.keys()}")
        
        dest = trip_data.get("destination", "成都") # 增加默认值防止key error
        print(f"[MapRenderer] resolving destination: {dest}")
        center_coords = self._get_coordinates(dest)
        print(f"[MapRenderer] center_coords: {center_coords}")
        
        m = self._build_base_map(center_coords)

        daily_plan_raw = trip_data.get("daily_plan")
        if not daily_plan_raw:
             print("[MapRenderer] Warning: daily_plan is empty or None")
             return m

        if isinstance(daily_plan_raw, dict):
            daily_plans_grouped: Dict[str, List[Dict[str, str]]] = daily_plan_raw
        elif isinstance(daily_plan_raw, list):
            daily_plans_grouped = {"1": daily_plan_raw}
        else:
            print(f"[MapRenderer] 警告：daily_plan 数据类型异常，无法渲染。类型: {type(daily_plan_raw)}")
            return m

        marker_cluster = MarkerCluster(name="行程景点").add_to(m)

        for day_str, items in daily_plans_grouped.items():
            try:
                day_idx = int(day_str) - 1
            except ValueError:
                print(f"[MapRenderer] 无法解析日期字符串 '{day_str}' 为数字，跳过。")
                continue

            coords_list: List[Tuple[float, float]] = []

            for idx, item in enumerate(items):
                address = item.get("address")
                attraction = item.get("attraction", "未知景点")
                
                if not address:
                    print(f"[MapRenderer] 第{day_str}天第{idx + 1}项行程({attraction})缺少地址，跳过。")
                    continue

                coords = self._get_coordinates(address)
                print(f"[MapRenderer] Geocoding '{attraction}' ({address}) -> {coords}")

                if not coords or all(c == 0 for c in coords):
                    print(f"[MapRenderer] 无法获取地址 '{address}' 的有效坐标，跳过。")
                    continue

                coords_list.append(coords)

                popup_html = (
                    f"<b>第{day_str}天</b><br>"
                    f"{item.get('time', '')}：{item.get('attraction', '')}<br>"
                    f"交通：{item.get('transport', '')}"
                )

                folium.Marker(
                    location=coords,
                    popup=popup_html,
                    icon=self._create_icon(day_idx, item),
                    tooltip=item.get("attraction", ""),
                ).add_to(marker_cluster)

            if len(coords_list) >= 2:
                PolyLine(
                    locations=coords_list,
                    color=self.colors[day_idx % len(self.colors)],
                    weight=3,
                    opacity=0.7,
                    tooltip=f"第{day_str}天路线",
                ).add_to(m)

        return m
