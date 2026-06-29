"""YouTube / 本地视频 → 中文配音 + 中文硬字幕 MP4 一键流水线。

用法（必须用 114 听书软件的 venv 运行，已封装在 run.ps1 里）：
    python pipeline.py "https://www.youtube.com/watch?v=XXXX"
    python pipeline.py URL --voice 沉稳男声 --whisper large-v3-turbo --lang en
    python pipeline.py --file 本地视频.mp4

实现说明：faster-whisper(ctranslate2) 与 F5-TTS(torch) 同进程加载 CUDA 会崩溃
（0xC0000005），所以默认（--stage all）会把流程拆成 prep / render 两个独立子进程依次执行。
中间产物缓存在 work/<id>/，可断点续跑；删除对应文件即可强制重算。
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

from src import config
from src.utils import log


def _run_stage_subprocess(stage: str, args, job_id: str) -> int:
    """以独立子进程运行某个阶段，stdout/stderr 直接继承（日志上浮到父进程）。"""
    cmd = [sys.executable, "-u", str(Path(__file__).resolve()),
           "--stage", stage, "--job-id", job_id]
    if args.url:
        cmd.append(args.url)
    if args.file:
        cmd += ["--file", args.file]
    if args.voice:
        cmd += ["--voice", args.voice]
    if args.whisper:
        cmd += ["--whisper", args.whisper]
    if args.lang:
        cmd += ["--lang", args.lang]
    if args.out:
        cmd += ["--out", args.out]
    return subprocess.run(cmd, cwd=str(config.BASE_DIR)).returncode


def main() -> int:
    ap = argparse.ArgumentParser(description="YouTube/本地视频 → 中文配音 + 中文硬字幕 MP4")
    ap.add_argument("url", nargs="?", help="YouTube 视频链接")
    ap.add_argument("--file", help="本地视频文件路径（与 url 二选一）")
    ap.add_argument("--job-id", help="任务/工作目录 id")
    ap.add_argument("--stage", choices=["all", "prep", "render"], default="all",
                    help="all=完整(默认，拆两进程)；prep=下载+识别+翻译；render=配音+字幕+合成")
    ap.add_argument("--voice", help="配音音色（沉稳男声/温柔女声/浑厚男声）")
    ap.add_argument("--whisper", help="ASR 模型（small / large-v3-turbo …）")
    ap.add_argument("--lang", help="源语言代码，留空自动检测（如 en/ja/ko）")
    ap.add_argument("--out", help="输出文件路径（默认 work/<id>/final.mp4）")
    args = ap.parse_args()

    from src import orchestrator

    # 单阶段：直接在本进程执行（被 all 模式或外部按阶段调用）
    if args.stage == "prep":
        orchestrator.run_prep(
            url=args.url,
            local_file=Path(args.file) if args.file else None,
            job_id=args.job_id,
            whisper_model=args.whisper,
            language=args.lang,
        )
        return 0
    if args.stage == "render":
        if not args.job_id:
            ap.error("render 阶段需要 --job-id")
        orchestrator.run_render(job_id=args.job_id, voice=args.voice, out_path=args.out)
        return 0

    # all：解析出统一 job_id，再依次跑两个独立子进程
    if not args.url and not args.file:
        ap.error("必须提供 url 或 --file 其一")
    job_id = args.job_id or orchestrator.make_job_id(
        args.url, Path(args.file).name if args.file else None)

    t0 = time.time()
    rc = _run_stage_subprocess("prep", args, job_id)
    if rc != 0:
        log("pipeline", f"准备阶段失败（退出码 {rc}）")
        return rc
    rc = _run_stage_subprocess("render", args, job_id)
    if rc != 0:
        log("pipeline", f"合成阶段失败（退出码 {rc}）")
        return rc

    out_path = args.out or str(config.WORK_DIR / job_id / "final.mp4")
    log("pipeline", f"全部完成，用时 {time.time() - t0:.0f}s → {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
