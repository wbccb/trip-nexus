import importlib
import sys
import time
import traceback


MODULES = [
    "src.auth.middleware",
    "src.api.dependencies",
    "src.api.routes.auth",
    "src.api.routes.admin",
    "src.api.routes.health",
    "src.api.routes.session",
    "src.api.routes.chat",
    "src.api.routes.flow",
    "src.api.routes.trip",
    "src.api.routes.knowledge",
    "src.api.routes.map",
    "src.api.app",
]


def main() -> int:
    for name in MODULES:
        started = time.perf_counter()
        print(f"[import-profile] importing {name}", flush=True)
        try:
            importlib.import_module(name)
        except Exception:
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            print(f"[import-profile] FAILED {name} cost_ms={elapsed_ms:.2f}", flush=True)
            traceback.print_exc()
            return 1
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        print(f"[import-profile] imported {name} cost_ms={elapsed_ms:.2f}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
