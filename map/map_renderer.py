import folium
from folium import Marker, PolyLine, Icon
from geopy.geocoders import Nominatim
from geopy.exc import GeocoderTimedOut, GeocoderServiceError
from typing import Dict, List, Tuple, Optional
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
        center_coords = self._get_coordinates(dest)
        m = folium.Map(location=center_coords, zoom_start=12, tiles="CartoDB positron")

        daily_plan: Dict[str, List[Dict[str, str]]] = trip_data["daily_plan"]
        for day_str, items in daily_plan.items():
            day_idx = int(day_str) - 1
            coords_list: List[Tuple[float, float]] = []

            for idx, item in enumerate(items):
                coords = self._get_coordinates(item["address"])
                coords_list.append(coords)

                Marker(
                    location=coords,
                    popup=f"""
                    <b>第{day_str}天</b><br>
                    {item['time']}：{item['attraction']}<br>
                    交通：{item['transport']}
                    """,
                    icon=Icon(
                        color=self.colors[day_idx % len(self.colors)],
                        icon="map-marker",
                        prefix="fa"
                    ),
                    tooltip=item["attraction"]
                ).add_to(m)

            if len(coords_list) >= 2:
                PolyLine(
                    locations=coords_list,
                    color=self.colors[day_idx % len(self.colors)],
                    weight=3,
                    opacity=0.7,
                    tooltip=f"第{day_str}天路线"
                ).add_to(m)

        return m