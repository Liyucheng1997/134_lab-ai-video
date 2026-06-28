"""通用小工具：日志、子进程、ffprobe 时长、JSON 缓存。"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

from . import config

_NO_WINDOW = 0x08000000  # Windows: 不弹黑框

# 可选日志钩子：Web 服务用它把每一步进度推给前端。
_log_hook = None


def set_log_hook(fn) -> None:
    """注册回调 fn(stage:str, msg:str)；传 None 取消。"""
    global _log_hook
    _log_hook = fn


def log(stage: str, msg: str) -> None:
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}] [{stage}] {msg}", flush=True)
    if _log_hook is not None:
        try:
            _log_hook(stage, msg)
        except Exception:
            pass


def run(cmd: list[str], desc: str = "", quiet: bool = True) -> None:
    """运行外部命令；失败抛异常并带上 stderr。"""
    if desc:
        log("run", desc)
    proc = subprocess.run(
        cmd,
        stdout=subprocess.PIPE if quiet else None,
        stderr=subprocess.PIPE if quiet else None,
        text=True,
        creationflags=_NO_WINDOW,
    )
    if proc.returncode != 0:
        tail = (proc.stderr or "")[-2000:] if quiet else ""
        raise RuntimeError(f"命令失败 ({proc.returncode}): {' '.join(cmd[:3])}...\n{tail}")


def media_duration(path: str | Path) -> float:
    """用 ffprobe 取媒体时长（秒）。"""
    out = subprocess.run(
        [config.FFPROBE, "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True, creationflags=_NO_WINDOW,
    )
    try:
        return float(out.stdout.strip())
    except ValueError:
        return 0.0


def save_json(path: str | Path, data) -> None:
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_json(path: str | Path):
    p = Path(path)
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return None


def srt_timestamp(seconds: float) -> str:
    if seconds < 0:
        seconds = 0
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def ass_timestamp(seconds: float) -> str:
    if seconds < 0:
        seconds = 0
    cs = int(round(seconds * 100))
    h, cs = divmod(cs, 360_000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h:d}:{m:02d}:{s:02d}.{cs:02d}"
