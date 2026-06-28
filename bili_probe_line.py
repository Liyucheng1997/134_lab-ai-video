"""Probe a single BiliBili upload line without submitting an archive."""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from src import config
from src.utils import log


def _force_system_dns() -> None:
    """Avoid aiohttp's aiodns/pycares resolver, which can time out while system DNS works."""
    try:
        import aiohttp.resolver
        import aiohttp.connector

        aiohttp.resolver.DefaultResolver = aiohttp.resolver.ThreadedResolver
        aiohttp.connector.DefaultResolver = aiohttp.resolver.ThreadedResolver
    except Exception:
        pass


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--line", required=True)
    ap.add_argument("--video", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    try:
        _force_system_dns()
        from biliup.plugins.bili_webup import BiliBili, Data

        cookie_file = Path(config.BILIBILI_COOKIE_FILE)
        cookie = json.loads(cookie_file.read_text(encoding="utf-8"))
        started = time.time()

        with BiliBili(Data(title="line-probe")) as bili:
            bili.login_by_cookies(cookie)
            part = bili.upload_file(
                str(Path(args.video)),
                lines=args.line,
                tasks=config.BILIBILI_UPLOAD_THREADS,
            )

        result = {
            "ok": True,
            "line": args.line,
            "seconds": round(time.time() - started, 3),
            "part": part,
        }
        Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        log("probe", json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as e:  # noqa: BLE001
        result = {"ok": False, "line": args.line, "error": str(e)}
        Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        log("error", json.dumps(result, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    sys.exit(main())
