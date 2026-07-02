#!/usr/bin/env python3
"""检查 FastAPI 路由是否全部进入公共 OpenAPI 契约。

比较粒度是 method + path shape：不同参数命名（pid/projectId）不算漂移，
但新增/删除路由或 method 会 fail。确认为内部路由时写入 allowlist。
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Iterable

import yaml


IGNORED_METHODS = {"HEAD", "OPTIONS"}
PATH_PARAM_RE = re.compile(r"\{([^}:]+)(?::[^}]+)?\}")


def repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def canonical_path(path: str) -> str:
    """去掉 FastAPI converter，并忽略参数名差异，只比较路径形状。"""
    return PATH_PARAM_RE.sub("{}", path.rstrip("/") or "/")


def route_key(method: str, path: str) -> tuple[str, str]:
    return method.upper(), canonical_path(path)


def format_key(key: tuple[str, str]) -> str:
    return f"{key[0]} {key[1]}"


def load_contract(path: Path) -> set[tuple[str, str]]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    routes: set[tuple[str, str]] = set()
    for path_name, item in (raw.get("paths") or {}).items():
        if not isinstance(item, dict):
            continue
        for method in item:
            upper = str(method).upper()
            if upper in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
                routes.add(route_key(upper, path_name))
    return routes


def _iter_terminal_routes(routes: Iterable[object]) -> Iterable[object]:
    """展平路由树：新版 FastAPI (>=0.139) 的 include_router() 把子路由包成不透明的
    `_IncludedRouter`（无自身 .path/.methods），必须递归展开其 .original_router.routes
    /.routes 才能拿到真正的 APIRoute；否则整个子路由器会被静默漏检（无异常/无告警，
    CI 误报"路由缺失"——曾在 fastapi 0.139.0 + starlette 1.3.1 复现）。
    """
    for route in routes:
        methods = getattr(route, "methods", None)
        path = getattr(route, "path", None)
        if methods and path:
            yield route
            continue
        nested = getattr(route, "routes", None)
        if nested is None:
            original_router = getattr(route, "original_router", None)
            nested = getattr(original_router, "routes", None)
        if nested:
            yield from _iter_terminal_routes(nested)


def load_app_routes() -> set[tuple[str, str]]:
    agent_root = repo_root() / "services" / "agent"
    sys.path.insert(0, str(agent_root))
    from app.main import app  # pylint: disable=import-outside-toplevel

    routes: set[tuple[str, str]] = set()
    for route in _iter_terminal_routes(app.routes):
        methods: Iterable[str] = getattr(route, "methods")
        path: str = getattr(route, "path")
        for method in methods:
            upper = method.upper()
            if upper not in IGNORED_METHODS:
                routes.add(route_key(upper, path))
    return routes


def load_allowlist(path: Path) -> set[tuple[str, str]]:
    if not path.exists():
        return set()
    allowed: set[tuple[str, str]] = set()
    for line_no, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) != 2:
            raise SystemExit(f"allowlist 格式错误 {path}:{line_no}: {raw_line}")
        allowed.add(route_key(parts[0], parts[1]))
    return allowed


def main() -> int:
    parser = argparse.ArgumentParser(description="Check FastAPI/OpenAPI route drift.")
    parser.add_argument(
        "--contract",
        type=Path,
        default=repo_root() / "packages" / "contracts" / "openapi.yaml",
    )
    parser.add_argument(
        "--allowlist",
        type=Path,
        default=Path(__file__).with_name("openapi_drift_allowlist.txt"),
    )
    args = parser.parse_args()

    app_routes = load_app_routes()
    contract_routes = load_contract(args.contract)
    allowlist = load_allowlist(args.allowlist)

    missing_in_contract = sorted(app_routes - contract_routes - allowlist)
    stale_in_contract = sorted(contract_routes - app_routes - allowlist)

    if missing_in_contract or stale_in_contract:
        print("OpenAPI drift detected.", file=sys.stderr)
        if missing_in_contract:
            print("\nFastAPI 有但 openapi.yaml 缺失:", file=sys.stderr)
            for key in missing_in_contract:
                print(f"  - {format_key(key)}", file=sys.stderr)
        if stale_in_contract:
            print("\nopenapi.yaml 有但 FastAPI 缺失:", file=sys.stderr)
            for key in stale_in_contract:
                print(f"  - {format_key(key)}", file=sys.stderr)
        print(f"\n如确认为内部路由，请加入 allowlist: {args.allowlist}", file=sys.stderr)
        return 1

    print(
        "OpenAPI drift check passed "
        f"({len(app_routes)} app routes, {len(contract_routes)} contract routes, "
        f"{len(allowlist)} allowlisted)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
