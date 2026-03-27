from typing import Dict


def get_supported_oauth_providers() -> Dict[str, str]:
    """Phase 1 先提供扩展点，后续再接真实 OAuth provider。"""
    return {
        "google": "reserved",
        "github": "reserved",
    }
