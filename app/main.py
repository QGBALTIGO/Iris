from __future__ import annotations

import argparse
import asyncio


def main() -> None:
    parser = argparse.ArgumentParser(description="Iris universal page analyzer")
    parser.add_argument("mode", choices=["api", "bot"], nargs="?", default="api")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    if args.mode == "bot":
        from app.bot import run_bot
        asyncio.run(run_bot())
    else:
        import uvicorn
        uvicorn.run("app.api:app", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
