import sys
import os

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.llm.llm_manager import LlmManager


def main() -> None:
    manager = LlmManager()

    print("=== Tool Schemas ===")
    for schema in manager.list_tools():
        print(schema)

    print("\n=== Weather Tool ===")
    print(manager.call_tool("weather.get_daily", {"city": "成都"}))

    print("\n=== Geocode Tool ===")
    print(manager.call_tool("geo.geocode", {"address": "天府广场", "city": "成都"}))

    print("\n=== POI Search Tool ===")
    print(manager.call_tool("poi.search", {"query": "火锅", "city": "成都", "top_k": 3}))


if __name__ == "__main__":
    main()
