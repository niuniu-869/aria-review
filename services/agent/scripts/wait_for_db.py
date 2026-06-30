"""Wait until DATABASE_URL accepts connections."""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time

import asyncpg


def _asyncpg_url(raw_url: str) -> str:
    if not raw_url:
        raise ValueError("DATABASE_URL is empty")
    if raw_url.startswith("postgresql+asyncpg://"):
        return "postgresql://" + raw_url[len("postgresql+asyncpg://"):]
    return raw_url


async def _probe(url: str) -> None:
    conn = await asyncpg.connect(_asyncpg_url(url), timeout=5)
    await conn.close()


async def _wait(url: str, timeout: float, interval: float) -> int:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            await _probe(url)
            print("database is ready")
            return 0
        except Exception as exc:  # pragma: no cover - exercised in container smoke tests
            last_error = exc
            await asyncio.sleep(interval)
    print(f"database is not ready after {timeout:.0f}s: {last_error}", file=sys.stderr)
    return 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default=os.environ.get("DATABASE_URL", ""))
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument("--interval", type=float, default=1)
    args = parser.parse_args()
    return asyncio.run(_wait(args.url, args.timeout, args.interval))


if __name__ == "__main__":
    raise SystemExit(main())
