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
        self.geolocator = Nominatim(
            user_agent="trip_nexus_py312_v3",
            timeout=15,
            domain="nominatim.openstreetmap.org",
        )
        self.colors = ["blue", "green", "red", "purple", "orange", "darkblue"]

    def _get_coordinates(
        self,
        address: str,
        city_name: str | None = None,
        attraction_name: str | None = None,
        fallback: Tuple[float, float] | None = None,
        max_offset_deg: float | None = None,
    ) -> Tuple[float, float]:
        search_queries = [address]
        # 尝试组合策略：如果提供了城市名和景点名，尝试组合搜索
        if city_name and attraction_name:
            if city_name not in address:
                search_queries.append(f"{city_name}{attraction_name}")
            search_queries.append(f"{city_name} {attraction_name}") # 加空格尝试
        
        # 如果地址本身就是模糊的（如"附近的餐馆"），直接尝试城市+景点（如果有）
        # 或者仅仅依赖 fallback
        
        for query in search_queries:
            for attempt in range(2): # 每个 query 重试 2 次
                try:
                    location = self.geolocator.geocode(query, exactly_one=True)
                    if location:
                        lat = location.latitude
                        lon = location.longitude
                        
                        # 检查是否越界
                        if (
                            fallback
                            and max_offset_deg is not None
                            and (
                                abs(lat - fallback[0]) > max_offset_deg
                                or abs(lon - fallback[1]) > max_offset_deg
                            )
                        ):
                            print(
                                f"[MapRenderer] geocode result out of bounds for '{query}', "
                                f"lat={lat}, lon={lon}, center={fallback}"
                            )
                            # 如果越界了，继续尝试下一个 query，或者直接视为失败
                            continue
                        else:
                            # 成功且未越界
                            return (lat, lon)
                            
                except (GeocoderTimedOut, GeocoderServiceError, Exception) as e:
                    print(f"[MapRenderer] geocode error for '{query}' attempt {attempt + 1}: {e}")
                    time.sleep(1)
        
        # 所有尝试都失败或越界，尝试降级到城市中心（如果之前没试过）
        # 这里逻辑是：如果 address 失败了，我们已经在上面尝试了 city 降级了吗？
        # 原有代码里有 city = address.split(",")[-1].strip() 的逻辑，保留一下
        try:
             short_addr = address.split(",")[-1].strip()
             if short_addr != address:
                 location = self.geolocator.geocode(short_addr, exactly_one=True)
                 if location:
                    lat = location.latitude
                    lon = location.longitude
                    if not (
                        fallback
                        and max_offset_deg is not None
                        and (
                            abs(lat - fallback[0]) > max_offset_deg
                            or abs(lon - fallback[1]) > max_offset_deg
                        )
                    ):
                        return (lat, lon)
        except Exception:
            pass

        return fallback or (30.6570, 104.0650)

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

        dest = trip_data.get("destination", "成都")
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

        all_coords: List[Tuple[float, float]] = []

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
                lat = item.get("latitude")
                lon = item.get("longitude")

                if isinstance(lat, (int, float)) and isinstance(lon, (int, float)) and not (lat == 0 and lon == 0):
                    coords = (float(lat), float(lon))
                else:
                    if not address:
                        print(f"[MapRenderer] 第{day_str}天第{idx + 1}项行程({attraction})缺少地址，跳过。")
                        continue

                    coords = self._get_coordinates(
                        address,
                        city_name=dest,
                        attraction_name=attraction,
                        fallback=center_coords,
                        max_offset_deg=1.0
                    )
                print(
                    f"[MapRenderer] Marker raw -> day={day_str}, idx={idx}, "
                    f"attraction='{attraction}', address='{address}', coords={coords}"
                )

                if not coords or all(c == 0 for c in coords):
                    print(f"[MapRenderer] 无法获取地址 '{address}' 的有效坐标，跳过。")
                    continue

                coords_list.append(coords)
                all_coords.append(coords)

                popup_html = (
                    f"<b>第{day_str}天</b><br>"
                    f"{item.get('time', '')}：{item.get('attraction', '')}<br>"
                    f"交通：{item.get('transport', '')}"
                )

                marker = folium.Marker(
                    location=coords,
                    popup=popup_html,
                    icon=self._create_icon(day_idx, item),
                    tooltip=item.get("attraction", ""),
                )
                marker.add_to(m)
                print(
                    f"[MapRenderer] Marker added -> day={day_str}, idx={idx}, "
                    f"lat={coords[0]}, lon={coords[1]}"
                )

            if len(coords_list) >= 2:
                PolyLine(
                    locations=coords_list,
                    color=self.colors[day_idx % len(self.colors)],
                    weight=3,
                    opacity=0.7,
                    tooltip=f"第{day_str}天路线",
                ).add_to(m)

        if all_coords:
            try:
                m.fit_bounds(all_coords, padding=(20, 20))
            except Exception as e:
                print(f"[MapRenderer] fit_bounds failed: {e}")

        return m
