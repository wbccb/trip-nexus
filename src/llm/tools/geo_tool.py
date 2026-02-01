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
                location = geolocator.geocode(query, exactly_one=True)
                if location:
                    return {
                        "address": address,
                        "latitude": location.latitude,
                        "longitude": location.longitude,
                        "display_name": location.address,
                        "query": query,
                    }
            except (GeocoderTimedOut, GeocoderServiceError, Exception):
                continue
    raise RuntimeError(f"geocode failed for address: {address}")
