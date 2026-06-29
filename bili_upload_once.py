"""单线路 B 站投稿 helper。

由 src.publish 以子进程调用。隔离 biliup 的网络阻塞，便于主流程超时切换线路。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from src import config
from src.utils import log


def _force_system_dns() -> None:
    """Avoid aiohttp's aiodns/pycares resolver, which may fail on local DNS setups."""
    try:
        import aiohttp.resolver
        import aiohttp.connector

        aiohttp.resolver.DefaultResolver = aiohttp.resolver.ThreadedResolver
        aiohttp.connector.DefaultResolver = aiohttp.resolver.ThreadedResolver
    except Exception:
        pass


def _preupload_status(bili, video_path: Path, line: str) -> str:
    """Return Bilibili's preupload error when biliup masks it as a missing endpoint."""
    line_map = {
        "bda": ("upcdn=bda&probe_version=20221109", "//upos-cs-upcdnbda.bilivideo.com/OK"),
        "bda2": ("upcdn=bda2&probe_version=20221109", "//upos-cs-upcdnbda2.bilivideo.com/OK"),
        "cs-bda2": ("upcdn=bda2&probe_version=20221109", "//upos-cs-upcdnbda2.bilivideo.com/OK"),
        "tx": ("upcdn=tx&probe_version=20221109", "//upos-cs-upcdntx.bilivideo.com/OK"),
        "txa": ("upcdn=txa&probe_version=20221109", "//upos-cs-upcdntxa.bilivideo.com/OK"),
        "ws": ("upcdn=ws&probe_version=20221109", "//upos-cs-upcdnws.bilivideo.com/OK"),
        "qn": ("upcdn=qn&probe_version=20221109", "//upos-cs-upcdnqn.bilivideo.com/OK"),
        "cs-qn": ("upcdn=qn&probe_version=20221109", "//upos-cs-upcdnqn.bilivideo.com/OK"),
    }
    query_text = line_map.get(line, line_map["bda2"])[0]
    query = {
        "r": "upos",
        "profile": "ugcupos/bup",
        "ssl": 0,
        "version": "2.8.12",
        "build": 2081200,
        "name": str(video_path),
        "size": os.path.getsize(video_path),
    }
    session = getattr(bili, "_BiliBili__session")
    resp = session.get(f"https://member.bilibili.com/preupload?{query_text}", params=query, timeout=10)
    try:
        ret = resp.json()
    except Exception:
        return f"B站预上传失败：HTTP {resp.status_code} {resp.text[:300]}"
    if "endpoint" in ret:
        return ""
    msg = ret.get("message") or ret.get("info") or json.dumps(ret, ensure_ascii=False)
    code = ret.get("code")
    suffix = f"（code={code}）" if code is not None else ""
    return f"B站预上传失败：{msg}{suffix}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--meta", required=True)
    ap.add_argument("--video", required=True)
    ap.add_argument("--cover", default="")
    ap.add_argument("--line", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    try:
        _force_system_dns()
        from biliup.plugins.bili_webup import BiliBili, Data

        meta = json.loads(Path(args.meta).read_text(encoding="utf-8"))
        cookie = json.loads(Path(config.BILIBILI_COOKIE_FILE).read_text(encoding="utf-8"))
        tags = [str(t).strip() for t in (meta.get("tags") or []) if str(t).strip()][:10]
        video_path = Path(args.video)
        cover_path = Path(args.cover) if args.cover else None

        video = Data(
            copyright=int(meta.get("copyright") or config.BILIBILI_COPYRIGHT),
            source=meta.get("source", ""),
            tid=int(meta.get("tid") or config.BILIBILI_TID),
            title=(meta.get("title") or video_path.stem)[:80],
            desc=meta.get("desc", ""),
            dynamic=(meta.get("title") or "")[:233],
            tag=tags,
        )

        log("publish", f"helper 登录并上传，线路：{args.line}")
        with BiliBili(video) as bili:
            bili.login_by_cookies(cookie)
            try:
                part = bili.upload_file(
                    str(video_path),
                    lines=args.line,
                    tasks=config.BILIBILI_UPLOAD_THREADS,
                )
            except KeyError as e:
                if str(e).strip("'\"") in {"endpoint", "chunk_size"}:
                    detail = _preupload_status(bili, video_path, args.line)
                    if detail:
                        raise RuntimeError(detail) from e
                raise
            part["title"] = video.title
            video.append(part)
            if cover_path and cover_path.exists():
                video.cover = bili.cover_up(str(cover_path)).replace("http:", "")
                log("publish", "封面已上传")
            ret = bili.submit("web")

        Path(args.out).write_text(json.dumps(ret, ensure_ascii=False, indent=2), encoding="utf-8")
        log("publish", f"helper 投稿成功：{json.dumps(ret, ensure_ascii=False)}")
        return 0
    except Exception as e:  # noqa: BLE001
        log("error", f"helper 上传失败：{e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
