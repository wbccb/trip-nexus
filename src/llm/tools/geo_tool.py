from typing import Dict, Any, Optional
from geopy.geocoders import Nominatim
from geopy.exc import GeocoderTimedOut, GeocoderServiceError


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
                # 打印请求前的参数
                print(f"\n [Geocode Request] query={query}")
                location = geolocator.geocode(query, exactly_one=True)
                # 打印请求后的返回结果
                print(f"[Geocode Response] query={query}, location={location} \n")
                if location:
                    return {
                        "address": address,
                        "latitude": location.latitude,
                        "longitude": location.longitude,
                        "display_name": location.address,
                        "query": query,
                    }
            except (GeocoderTimedOut, GeocoderServiceError, Exception) as e:
                # 打印异常信息
                print(f"[Geocode Error] query={query}, error={e} \n")
                continue
    raise RuntimeError(f"geocode failed for address: {address}")
