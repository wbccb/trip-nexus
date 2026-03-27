from typing import Dict, Any, Optional

from fastapi import APIRouter, Depends

from src.auth.middleware import AuthenticatedUser, get_optional_user, init_auth_tables

router = APIRouter(prefix="/api", tags=["health"])

@router.get("/health")
def health_check(current_user: Optional[AuthenticatedUser] = Depends(get_optional_user)) -> Dict[str, Any]:
    """健康检查接口"""
    init_auth_tables()
    return {
        "status": "ok",
        "auth": "enabled",
        "authenticated": bool(current_user),
    }
