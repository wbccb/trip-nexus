import folium  # Folium 地图渲染主库
import math  # 数学函数，用于角度换算与三角函数
from folium import PolyLine  # Folium 折线路径
from folium.plugins import MarkerCluster  # Folium Marker 聚合（当前保留以便扩展）
from folium.features import DivIcon  # HTML 自定义图标
from geopy.geocoders import Nominatim  # 地理编码服务
from geopy.exc import GeocoderTimedOut, GeocoderServiceError  # 地理编码异常
from typing import Dict, List, Tuple, Any, Iterable  # 类型注解
import time  # 失败重试延迟
import logging  # 日志记录
from datetime import datetime  # 时间戳

logging.basicConfig(  # 配置日志级别与格式
    level=logging.INFO,  # 使用 INFO 级别输出
    format="%(asctime)s - %(levelname)s - %(message)s",  # 日志格式
)
logger = logging.getLogger(__name__)  # 模块级日志器

logging.getLogger("urllib3").setLevel(logging.ERROR)  # 降低 urllib3 噪声日志

AMAP_STREET_TILES = (  # 高德街道底图
    "http://webrd02.is.autonavi.com/appmaptile?lang=zh_cn&size=1&scale=1&style=7&x={x}&y={y}&z={z}"
)
AMAP_SATELLITE_TILES = (  # 高德卫星底图
    "http://webst02.is.autonavi.com/appmaptile?style=6&x={x}&y={y}&z={z}"
)


class TripMap:
    """
    行程地图渲染器：负责地理编码、路线绘制、Marker 序号、箭头方向等地图可视化逻辑。
    """

    def __init__(self):
        """
        初始化地理编码器与颜色方案。
        """
        self.geolocator = Nominatim(  # 初始化 Nominatim 地理编码
            user_agent="trip_nexus_py312_v3",  # 自定义 user_agent，避免被限制
            timeout=15,  # 请求超时
            domain="nominatim.openstreetmap.org",  # 解析服务域名
        )
        self.colors = ["blue", "green", "red", "purple", "orange", "darkblue"]  # 每天路线颜色列表

    def _get_coordinates(
        self,
        address: str,
        city_name: str | None = None,
        attraction_name: str | None = None,
        fallback: Tuple[float, float] | None = None,
        max_offset_deg: float | None = None,
    ) -> Tuple[float, float]:
        """
        解析地址为坐标，支持多种 query 组合并提供越界保护。
        """
        search_queries = [address]  # 以原始地址作为首选查询
        # 尝试组合策略：如果提供了城市名和景点名，尝试组合搜索
        if city_name and attraction_name:  # 同时具备城市与景点名时拼接更精准的 query
            if city_name not in address:  # 避免重复城市名
                search_queries.append(f"{city_name}{attraction_name}")  # 直接拼接
            search_queries.append(f"{city_name} {attraction_name}")  # 加空格再次尝试

        # 如果地址本身就是模糊的（如"附近的餐馆"），会在查询列表里覆盖到 city+景点

        for query in search_queries:  # 遍历每一种候选 query
            for attempt in range(2):  # 每个 query 重试 2 次
                try:
                    location = self.geolocator.geocode(query, exactly_one=True)  # 调用地理编码
                    if location:  # 成功解析到位置
                        lat = location.latitude  # 纬度
                        lon = location.longitude  # 经度

                        # 检查是否越界
                        if (
                            fallback  # 有中心点用于对比
                            and max_offset_deg is not None  # 有偏移阈值
                            and (
                                abs(lat - fallback[0]) > max_offset_deg  # 纬度偏移过大
                                or abs(lon - fallback[1]) > max_offset_deg  # 经度偏移过大
                            )
                        ):
                            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")  # 记录时间戳
                            print(
                                f"[{ts}][MapRenderer] geocode result out of bounds for '{query}', "
                                f"lat={lat}, lon={lon}, center={fallback}"
                            )
                            # 如果越界了，继续尝试下一个 query，或者直接视为失败
                            continue
                        else:
                            # 成功且未越界
                            return (lat, lon)  # 返回解析结果

                except (GeocoderTimedOut, GeocoderServiceError, Exception) as e:  # 捕获地理编码异常
                    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")  # 记录时间戳
                    print(f"[{ts}][MapRenderer] geocode error for '{query}' attempt {attempt + 1}: {e}")  # 输出错误
                    time.sleep(1)  # 延迟后重试

        # 所有尝试都失败或越界，尝试降级到城市中心（如果之前没试过）
        try:
            short_addr = address.split(",")[-1].strip()  # 尝试截取地址末尾作为城市名
            if short_addr != address:  # 确保截取后的地址不同
                location = self.geolocator.geocode(short_addr, exactly_one=True)  # 用简化地址再解析
                if location:  # 解析成功
                    lat = location.latitude  # 纬度
                    lon = location.longitude  # 经度
                    if not (  # 再次进行越界保护
                        fallback
                        and max_offset_deg is not None
                        and (
                            abs(lat - fallback[0]) > max_offset_deg
                            or abs(lon - fallback[1]) > max_offset_deg
                        )
                    ):
                        return (lat, lon)  # 返回降级结果
        except Exception:  # 降级解析失败时吞掉异常
            pass

        return fallback or (30.6570, 104.0650)  # 最后回退到中心坐标或默认成都

    def _create_icon(self, day_idx: int, item: Dict[str, Any], order_label: str) -> DivIcon:
        """
        创建带序号的 Marker 图标。
        """
        color = self.colors[day_idx % len(self.colors)]  # 按天选择颜色
        html = (  # 使用 HTML 绘制圆形序号图标
            f'<div style="width:20px;height:20px;border-radius:50%;'
            f'background-color:{color};border:2px solid white;'
            f'display:flex;align-items:center;justify-content:center;'
            f'color:#ffffff;font-size:11px;font-weight:700;">{order_label}</div>'
        )
        return DivIcon(html=html, icon_size=(20, 20), class_name="poi-marker")  # 返回自定义图标

    def _spread_overlapping_coords(
        self,
        coords: Tuple[float, float],
        overlap_counter: Dict[Tuple[float, float], int],
        step: float = 0.0003,
        row_size: int = 3,
    ) -> Tuple[float, float]:
        """
        对重合坐标进行网格偏移，避免多个 Marker 完全重叠。
        """
        key = (round(coords[0], 6), round(coords[1], 6))  # 使用近似坐标作为重合判定键
        count = overlap_counter.get(key, 0)  # 获取当前重合次数
        overlap_counter[key] = count + 1  # 更新计数
        row = count // row_size  # 计算网格行
        col = count % row_size  # 计算网格列
        return (coords[0] + row * step, coords[1] + col * step)  # 应用偏移后的坐标

    def _bearing_degrees(self, start: Tuple[float, float], end: Tuple[float, float]) -> float:
        """
        计算从 start 指向 end 的航向角（0-360 度）。
        """
        lat1, lon1 = start  # 起点纬经度
        lat2, lon2 = end  # 终点纬经度
        phi1 = math.radians(lat1)  # 起点纬度转弧度
        phi2 = math.radians(lat2)  # 终点纬度转弧度
        dlambda = math.radians(lon2 - lon1)  # 经度差转弧度
        y = math.sin(dlambda) * math.cos(phi2)  # 航向分量 y
        x = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlambda)  # 航向分量 x
        bearing = math.degrees(math.atan2(y, x))  # 将 atan2 结果转角度
        return (bearing + 360) % 360  # 规范化到 0-360

    def _add_route_arrow(
        self,
        day_layer: folium.FeatureGroup,
        coords_list: List[Tuple[float, float]],
        color: str,
    ) -> None:
        """
        为每一段相邻坐标添加方向箭头，箭头放在两点的中点。
        """
        if len(coords_list) < 2:  # 至少两点才有路线
            return
        for idx in range(len(coords_list) - 1):  # 逐段处理相邻点
            start = coords_list[idx]  # 段起点
            end = coords_list[idx + 1]  # 段终点
            mid_lat = (start[0] + end[0]) / 2  # 段中点纬度
            mid_lon = (start[1] + end[1]) / 2  # 段中点经度
            bearing = self._bearing_degrees(start, end)  # 计算航向角
            rotation = bearing - 90  # 字符箭头朝向调整（➤ 默认朝右）
            html = (  # 构建箭头 HTML 图标
                f'<div style="width:24px;height:24px;display:flex;align-items:center;'
                f'justify-content:center;transform:rotate({rotation}deg);'
                f'transform-origin:center;color:{color};font-size:18px;'
                f'font-weight:800;text-shadow:0 0 2px #ffffff;">➤</div>'
            )
            icon = DivIcon(html=html, icon_size=(24, 24), class_name="route-arrow")  # 生成箭头图标
            marker = folium.Marker(location=(mid_lat, mid_lon), icon=icon)  # 将箭头放到中点
            marker.add_to(day_layer)  # 添加到当天图层

    def _build_base_map(self, center_coords: Tuple[float, float]) -> folium.Map:
        """
        构建基础地图，并挂载多种底图图层。
        """
        m = folium.Map(location=center_coords, zoom_start=12, tiles=None)  # 初始化地图容器

        folium.TileLayer(
            tiles=AMAP_STREET_TILES,
            attr="高德地图",
            name="高德街道",
            overlay=False,
            control=True,
            show=True,
        ).add_to(m)

        folium.TileLayer(
            tiles=AMAP_SATELLITE_TILES,
            attr="高德地图",
            name="高德卫星",
            overlay=False,
            control=True,
            show=False,
        ).add_to(m)

        folium.TileLayer(
            tiles="CartoDB positron",
            name="CartoDB Positron",
            overlay=False,
            control=True,
            show=False,
        ).add_to(m)

        return m  # 返回基础地图对象

    def render_map(self, trip_data: Dict[str, Any]) -> folium.Map:
        """
        一次性渲染完整地图（包含所有 POI 与路线）。
        """
        logger.info("\n\n------------------!!开始渲染地图!!------------------\n\n")  # 记录渲染开始
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")  # 当前时间戳
        print(f"[{ts}][MapRenderer] render_map input trip_data keys: {trip_data.keys()}")  # 打印输入结构

        dest = trip_data.get("destination", "成都")  # 获取目的地
        print(f"[{ts}][MapRenderer] resolving destination: {dest}")  # 输出目的地
        center_coords = self._get_coordinates(dest)  # 解析目的地坐标作为中心点
        print(f"[{ts}][MapRenderer] center_coords: {center_coords}")  # 输出中心坐标

        m = self._build_base_map(center_coords)  # 创建基础地图

        daily_plan_raw = trip_data.get("daily_plan")  # 读取行程计划
        if not daily_plan_raw:  # 没有行程则返回空地图
            print(f"[{ts}][MapRenderer] Warning: daily_plan is empty or None")  # 输出告警
            return m  # 直接返回

        if isinstance(daily_plan_raw, dict):  # 已按天分组
            daily_plans_grouped: Dict[str, List[Dict[str, str]]] = daily_plan_raw  # 类型显式标注
        elif isinstance(daily_plan_raw, list):  # 单天数组则归为第 1 天
            daily_plans_grouped = {"1": daily_plan_raw}  # 转为字典结构
        else:  # 其他结构不支持
            print(f"[{ts}][MapRenderer] 警告：daily_plan 数据类型异常，无法渲染。类型: {type(daily_plan_raw)}")  # 输出异常
            return m  # 返回空地图

        all_coords: List[Tuple[float, float]] = []  # 收集所有坐标用于 fit_bounds

        day_items = sorted(  # 对天数排序，保证渲染顺序稳定
            daily_plans_grouped.items(),  # 获取天 -> 行程列表
            key=lambda x: int(x[0]) if str(x[0]).isdigit() else str(x[0]),  # 优先按数字排序
        )
        for day_str, items in day_items:
            try:
                day_idx = int(day_str) - 1  # 将天数转为索引
            except ValueError:
                print(f"[{ts}][MapRenderer] 无法解析日期字符串 '{day_str}' 为数字，跳过。")  # 输出提示
                continue  # 跳过非法 day

            coords_list: List[Tuple[float, float]] = []  # 当天坐标列表
            day_layer = folium.FeatureGroup(name=f"第{day_str}天", overlay=True, control=True)  # 当天图层
            overlap_counter: Dict[Tuple[float, float], int] = {}  # 重合坐标计数器

            for idx, item in enumerate(items):  # 遍历当天行程项
                address = item.get("address")  # 地址
                attraction = item.get("attraction", "未知景点")  # 景点名称
                lat = item.get("latitude")  # 纬度
                lon = item.get("longitude")  # 经度

                if isinstance(lat, (int, float)) and isinstance(lon, (int, float)) and not (lat == 0 and lon == 0):
                    coords = (float(lat), float(lon))  # 使用已有坐标
                else:
                    if not address:  # 地址为空无法解析
                        print(f"[{ts}][MapRenderer] 第{day_str}天第{idx + 1}项行程({attraction})缺少地址，跳过。")  # 输出提示
                        continue  # 跳过该点

                    coords = self._get_coordinates(  # 解析地址坐标
                        address,
                        city_name=dest,
                        attraction_name=attraction,
                        fallback=center_coords,
                        max_offset_deg=1.0,
                    )
                print(  # 打印原始坐标信息
                    f"[{ts}][MapRenderer] Marker raw -> day={day_str}, idx={idx}, "
                    f"attraction='{attraction}', address='{address}', coords={coords}"
                )

                if not coords or all(c == 0 for c in coords):  # 无效坐标直接跳过
                    print(f"[{ts}][MapRenderer] 无法获取地址 '{address}' 的有效坐标，跳过。")  # 输出警告
                    continue  # 跳过该点

                adjusted_coords = self._spread_overlapping_coords(coords, overlap_counter)  # 处理重合坐标
                coords_list.append(adjusted_coords)  # 记录当天坐标
                all_coords.append(adjusted_coords)  # 记录全局坐标

                popup_html = (  # Marker 弹窗内容
                    f"<b>第{day_str}天</b><br>"
                    f"{item.get('time', '')}：{item.get('attraction', '')}<br>"
                    f"交通：{item.get('transport', '')}"
                )

                marker = folium.Marker(  # 构建 Marker
                    location=adjusted_coords,  # 使用偏移后的坐标
                    popup=popup_html,  # 弹窗信息
                    icon=self._create_icon(day_idx, item, str(idx + 1)),  # 序号图标
                    tooltip=item.get("attraction", ""),  # 悬浮提示
                )
                marker.add_to(day_layer)  # 添加到当天图层
                print(  # 输出添加日志
                    f"[{ts}][MapRenderer] Marker added -> day={day_str}, idx={idx}, "
                    f"lat={adjusted_coords[0]}, lon={adjusted_coords[1]}"
                )

            if len(coords_list) >= 2:  # 至少两点才绘制路线
                route_line = PolyLine(  # 创建当天路线
                    locations=coords_list,
                    color=self.colors[day_idx % len(self.colors)],  # 使用当天颜色
                    weight=3,  # 线宽
                    opacity=0.7,  # 透明度
                    tooltip=f"第{day_str}天路线",  # 路线提示
                )
                route_line.add_to(day_layer)  # 添加到当天图层
                self._add_route_arrow(day_layer, coords_list, self.colors[day_idx % len(self.colors)])  # 添加方向箭头
            day_layer.add_to(m)  # 将当天图层挂载到地图

        if all_coords:  # 有坐标时适配视角
            try:
                m.fit_bounds(all_coords, padding=(20, 20))  # 自适应视角
            except Exception as e:
                print(f"[{ts}][MapRenderer] fit_bounds failed: {e}")  # 输出错误

        folium.LayerControl().add_to(m)  # 添加图层控制器
        logger.info("\n\n------------------!!渲染地图结束!!------------------\n\n")  # 记录结束
        return m  # 返回最终地图

    def render_map_batches(self, trip_data: Dict[str, Any], batch_size: int = 4) -> Iterable[Dict[str, Any]]:
        """
        按批次渲染地图 POI，并逐步输出可用于前端刷新地图的 HTML 片段。

        Args:
            trip_data: 行程结构化数据，包含 destination 与 daily_plan。
            batch_size: 每批 POI 数量，控制地图更新节奏。

        Yields:
            按序输出 poi_batch 事件，包含当前 HTML 与是否完成标记。
        """
        logger.info("\n\n------------------!!开始分批渲染地图!!------------------\n\n")  # 记录渲染开始
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")  # 当前时间戳
        print(f"[{ts}][MapRenderer] render_map_batches input trip_data keys: {trip_data.keys()}")  # 输出输入结构

        dest = trip_data.get("destination", "成都")  # 读取目的地
        # 解析目的地中心点作为地图初始视角
        center_coords = self._get_coordinates(dest)  # 解析中心坐标
        m = self._build_base_map(center_coords)  # 构建基础地图

        daily_plan_raw = trip_data.get("daily_plan")  # 读取行程结构
        if not daily_plan_raw:  # 行程为空时返回空地图
            # 无行程数据时直接返回空地图事件
            yield {
                "event": "poi_batch",
                "sequence": 0,
                "day": None,
                "html": m.get_root().render(),
                "is_final": True,
                "map_obj": m,
            }
            return  # 终止生成

        if isinstance(daily_plan_raw, dict):  # 按天分组
            daily_plans_grouped: Dict[str, List[Dict[str, str]]] = daily_plan_raw  # 类型显式标注
        elif isinstance(daily_plan_raw, list):  # 单天数组
            daily_plans_grouped = {"1": daily_plan_raw}  # 转为统一结构
        else:
            # 非法数据结构时降级为返回空地图事件
            yield {
                "event": "poi_batch",
                "sequence": 0,
                "day": None,
                "html": m.get_root().render(),
                "is_final": True,
                "map_obj": m,
            }
            return  # 终止生成

        # 按天排序，保证 POI 渲染顺序稳定
        day_items = sorted(  # 对天数排序，保证渲染顺序稳定
            daily_plans_grouped.items(),  # 获取天 -> 行程列表
            key=lambda x: int(x[0]) if str(x[0]).isdigit() else str(x[0]),  # 按数字优先
        )
        all_coords: List[Tuple[float, float]] = []  # 收集所有坐标
        sequence = 0  # 批次序号

        for day_str, items in day_items:  # 遍历每一天
            try:
                day_idx = int(day_str) - 1  # 转为索引
            except ValueError:
                continue  # 非法 day 直接跳过

            # 每天一个图层，支持前端按天开关显示
            day_layer = folium.FeatureGroup(name=f"第{day_str}天", overlay=True, control=True)  # 当天图层
            coords_list: List[Tuple[float, float]] = []  # 当天坐标
            pending_flush = False  # 是否需要补发一次渲染
            overlap_counter: Dict[Tuple[float, float], int] = {}  # 重合计数器

            for idx, item in enumerate(items):  # 遍历当天行程
                address = item.get("address")  # 地址
                attraction = item.get("attraction", "未知景点")  # 景点名称
                lat = item.get("latitude")  # 纬度
                lon = item.get("longitude")  # 经度

                if isinstance(lat, (int, float)) and isinstance(lon, (int, float)) and not (lat == 0 and lon == 0):
                    coords = (float(lat), float(lon))  # 使用已有坐标
                else:
                    # 缺少经纬度时尝试地址解析
                    if not address:
                        continue  # 缺少地址则跳过
                    coords = self._get_coordinates(  # 地址解析坐标
                        address,
                        city_name=dest,
                        attraction_name=attraction,
                        fallback=center_coords,
                        max_offset_deg=1.0,
                    )

                if not coords or all(c == 0 for c in coords):  # 解析失败则跳过
                    continue

                adjusted_coords = self._spread_overlapping_coords(coords, overlap_counter)  # 处理重合
                coords_list.append(adjusted_coords)  # 记录当天坐标
                all_coords.append(adjusted_coords)  # 记录全局坐标

                popup_html = (  # 弹窗内容
                    f"<b>第{day_str}天</b><br>"
                    f"{item.get('time', '')}：{item.get('attraction', '')}<br>"
                    f"交通：{item.get('transport', '')}"
                )

                marker = folium.Marker(  # 创建 Marker
                    location=adjusted_coords,  # 使用偏移坐标
                    popup=popup_html,  # 弹窗信息
                    icon=self._create_icon(day_idx, item, str(idx + 1)),  # 序号图标
                    tooltip=item.get("attraction", ""),  # 悬浮提示
                )
                marker.add_to(day_layer)  # 添加到当天图层
                pending_flush = True  # 标记需要刷新

                # 达到批次大小时输出一次中间渲染结果
                if batch_size > 0 and len(coords_list) % batch_size == 0:  # 达到批次大小时输出
                    day_layer.add_to(m)  # 临时加入图层
                    if all_coords:
                        m.fit_bounds(all_coords, padding=(20, 20))  # 视角适配
                    sequence += 1  # 序号递增
                    yield {
                        "event": "poi_batch",
                        "sequence": sequence,
                        "day": day_str,
                        "html": m.get_root().render(),  # 生成 HTML
                        "is_final": False,
                        "map_obj": None,
                    }
                    pending_flush = False  # 已输出则重置

            if len(coords_list) >= 2:  # 至少两点才能画线
                # 同一天内绘制路线连线，增强可视化指引
                route_line = PolyLine(  # 创建路线
                    locations=coords_list,
                    color=self.colors[day_idx % len(self.colors)],  # 使用当天颜色
                    weight=3,  # 线宽
                    opacity=0.7,  # 透明度
                    tooltip=f"第{day_str}天路线",  # 提示文案
                )
                route_line.add_to(day_layer)  # 添加到当天图层
                self._add_route_arrow(day_layer, coords_list, self.colors[day_idx % len(self.colors)])  # 添加箭头
                pending_flush = True  # 路线绘制后需要刷新

            day_layer.add_to(m)  # 当天图层加入地图
            if pending_flush:  # 当日剩余点位未触发批次时，补发一次渲染更新
                if all_coords:
                    m.fit_bounds(all_coords, padding=(20, 20))  # 视角适配
                sequence += 1  # 序号递增
                yield {
                    "event": "poi_batch",
                    "sequence": sequence,
                    "day": day_str,
                    "html": m.get_root().render(),  # 生成 HTML
                    "is_final": False,
                    "map_obj": None,
                }

        if all_coords:
            try:
                # 最终视角适配所有坐标点
                m.fit_bounds(all_coords, padding=(20, 20))  # 最终 fit_bounds
            except Exception:
                pass  # 忽略 fit_bounds 失败

        folium.LayerControl().add_to(m)  # 添加图层控制器
        sequence += 1  # 最终批次序号
        # 最终事件输出完整地图对象，供后续复用
        yield {
            "event": "poi_batch",
            "sequence": sequence,
            "day": None,
            "html": m.get_root().render(),  # 输出最终 HTML
            "is_final": True,
            "map_obj": m,  # 返回地图对象
        }
