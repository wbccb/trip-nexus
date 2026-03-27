from typing import List, Any, Optional

from fastapi import APIRouter, Depends, Request

from src.auth.middleware import (
    AuthenticatedUser,
    get_optional_user,
)
from src.api.schemas.map import (
    MapRenderRequest,
    MapRenderResponse,
    MapGeoJsonRequest,
    MapGeoJsonResponse,
)
from src.api.dependencies import (
    _get_map_renderer,
    _apply_authenticated_audit_context,
    _reset_observability_context,
    _record_audit_log,
)

router = APIRouter(prefix="/api/map", tags=["map"])

@router.post("/render", response_model=MapRenderResponse)
def render_map(
    payload: MapRenderRequest,
    request: Request,
    current_user: Optional[AuthenticatedUser] = Depends(get_optional_user),
) -> MapRenderResponse:
    """渲染行程地图并返回分批次的 HTML 字符串"""
    guard_token = None
    if current_user:
        guard_token = _apply_authenticated_audit_context(
            request=request,
            current_user=current_user,
            request_path="/api/map/render",
        )
    try:
        map_renderer = _get_map_renderer()
        batch_index = int(payload.batch_index or 0)
        batch_size = int(payload.batch_size or 4)
        result = map_renderer.render_trip_map_batch(
            payload.trip_data,
            batch_index=batch_index,
            batch_size=batch_size,
        )
        if current_user:
            _record_audit_log(
                action="map_render",
                status="success",
                user_id=current_user.user_id,
                user_email=current_user.email,
                detail={"batch_index": batch_index, "batch_size": batch_size},
            )
        return MapRenderResponse(
            map_html=str(result.get("map_html") or ""),
            sequence=batch_index,
            day=str(result.get("day") or ""),
            is_final=bool(result.get("is_final")),
        )
    finally:
        if guard_token:
            _reset_observability_context(guard_token)


@router.post("/geojson", response_model=MapGeoJsonResponse)
def get_map_geojson(
    payload: MapGeoJsonRequest,
    request: Request,
    current_user: Optional[AuthenticatedUser] = Depends(get_optional_user),
) -> MapGeoJsonResponse:
    """获取行程的 GeoJSON 数据，供前端地图引擎渲染"""
    guard_token = None
    if current_user:
        guard_token = _apply_authenticated_audit_context(
            request=request,
            current_user=current_user,
            request_path="/api/map/geojson",
        )
    try:
        map_renderer = _get_map_renderer()
        result = map_renderer.get_trip_geojson(payload.trip_data)
        if current_user:
            _record_audit_log(
                action="map_geojson",
                status="success",
                user_id=current_user.user_id,
                user_email=current_user.email,
                detail={"total_points": int(result.get("total_points") or 0)},
            )
        return MapGeoJsonResponse(
            points=dict(result.get("points") or {}),
            routes=dict(result.get("routes") or {}),
            center=dict(result.get("center") or {}),
            bounds=list(result.get("bounds") or []),
            total_points=int(result.get("total_points") or 0),
        )
    finally:
        if guard_token:
            _reset_observability_context(guard_token)
