from typing import Optional, Dict, List, Any
from pydantic import BaseModel, Field

class MapRenderRequest(BaseModel):
    trip_data: Dict[str, Any] = Field(..., description="结构化行程数据")
    batch_index: Optional[int] = Field(0, description="批次序号，从 0 开始")
    batch_size: Optional[int] = Field(4, description="每批 POI 数量")


class MapRenderResponse(BaseModel):
    map_html: str = Field(..., description="地图 HTML 字符串")
    sequence: int = Field(..., description="当前批次序号")
    day: Optional[str] = Field(None, description="当前批次所属天数")
    is_final: bool = Field(False, description="是否最终批次")


class MapGeoJsonRequest(BaseModel):
    trip_data: Dict[str, Any] = Field(..., description="结构化行程数据")


class MapGeoJsonResponse(BaseModel):
    points: Dict[str, Any] = Field(..., description="POI 点位 GeoJSON")
    routes: Dict[str, Any] = Field(..., description="路线 GeoJSON")
    center: Dict[str, float] = Field(..., description="地图中心点")
    bounds: List[float] = Field(..., description="地图边界")
    total_points: int = Field(..., description="POI 数量")
