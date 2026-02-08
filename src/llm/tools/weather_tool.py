from typing import Dict, Any, Optional
import requests

def get_daily_weather(city: str, date: Optional[str] = None, days: int = 7) -> Dict[str, Any]:
    print("\n【请求】get_daily_weather", city, date, days)
    geo_resp = requests.get(
        "https://geocoding-api.open-meteo.com/v1/search",
        params={"name": city, "count": 1, "language": "zh", "format": "json"},
        timeout=10,
    )
    geo_resp.raise_for_status()
    geo_data = geo_resp.json()
    results = geo_data.get("results") or []
    if not results:
        raise RuntimeError(f"weather geocode not found for city: {city}")
    lat = results[0].get("latitude")
    lon = results[0].get("longitude")
    if lat is None or lon is None:
        raise RuntimeError(f"weather geocode missing coordinates for city: {city}")
    
    # 限制预报天数在 1-16 天之间（Open-Meteo 限制）
    forecast_days = max(1, min(int(days), 16))
    
    forecast_resp = requests.get(
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": lat,
            "longitude": lon,
            "daily": "weathercode,temperature_2m_max,temperature_2m_min",
            "timezone": "auto",
            "forecast_days": forecast_days,
        },
        timeout=10,
    )
    forecast_resp.raise_for_status()
    forecast = forecast_resp.json()
    daily = forecast.get("daily") or {}
    times = daily.get("time") or []
    temps_max = daily.get("temperature_2m_max") or []
    temps_min = daily.get("temperature_2m_min") or []
    codes = daily.get("weathercode") or []
    daily_list = []
    for idx, day in enumerate(times):
        if date and day != date:
            continue
        daily_list.append(
            {
                "date": day,
                "temp_max": temps_max[idx] if idx < len(temps_max) else None,
                "temp_min": temps_min[idx] if idx < len(temps_min) else None,
                "weathercode": codes[idx] if idx < len(codes) else None,
            }
        )
    result = {
        "city": city,
        "latitude": lat,
        "longitude": lon,
        "daily": daily_list,
        "source": "open-meteo",
    }
    print("【返回】get_daily_weather\n", result)
    return result
