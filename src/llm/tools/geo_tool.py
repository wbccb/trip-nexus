from typing import Dict, Any, Optional
from geopy.geocoders import Nominatim
from geopy.exc import GeocoderTimedOut, GeocoderServiceError
import logging
from src.observability import log_event

logger = logging.getLogger(__name__)


def geocode_address(
    address: str,
    geolocator: Nominatim,
    city: Optional[str] = None,
    attraction: Optional[str] = None,
) -> Dict[str, Any]:
    search_queries = [address]
    if city and attraction:
        if city not in address:
            search_queries.append(f"{city}{attraction}")
        search_queries.append(f"{city} {attraction}")
    for query in search_queries:
        for _ in range(2):
            try:
                log_event(logger, logging.INFO, "地理编码请求开始", {"查询": query})
                location = geolocator.geocode(query, exactly_one=True)
                log_event(logger, logging.INFO, "地理编码请求完成", {"查询": query, "命中": bool(location)})
                if location:
                    return {
                        "address": address,
                        "latitude": location.latitude,
                        "longitude": location.longitude,
                        "display_name": location.address,
                        "query": query,
                    }
            except (GeocoderTimedOut, GeocoderServiceError, Exception) as e:
                log_event(logger, logging.WARNING, "地理编码请求失败", {"查询": query, "原因": str(e)})
                continue
    raise RuntimeError(f"geocode failed for address: {address}")
