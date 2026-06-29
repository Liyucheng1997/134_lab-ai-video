"""一次性子进程：合成音色试听音频（单个或批量）。

单个：python voice_sample.py --voice 人工老龙凤 --speed 1.0 --out path.wav
批量：python voice_sample.py --speed 1.0 --spec spec.json
      spec.json = [{"voice": "...", "out": "..."}, ...]，进度通过 stdout 上浮。
单独进程跑（加载 torch/F5），避免污染主服务进程；同一进程内复用已加载模型。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", default="")
    ap.add_argument("--voice", default="")
    ap.add_argument("--text", default="你好，这是配音音色试听。")
    ap.add_argument("--speed", type=float, default=None)
    ap.add_argument("--out", default="")
    ap.add_argument("--spec", default="", help="批量任务 JSON 文件：[{voice,out},...]")
    args = ap.parse_args()

    from src import config, tts
    from src.utils import log

    if args.speed is not None:
        config.TTS_SPEED = max(0.5, min(1.6, float(args.speed)))

    if args.spec:
        jobs = json.loads(Path(args.spec).read_text(encoding="utf-8"))
        total = len(jobs)
        ok = 0
        for i, job in enumerate(jobs, 1):
            voice, out = job.get("voice", ""), job.get("out", "")
            log("prepare", f"[{i}/{total}] {voice}")
            try:
                tts.sample(args.text, Path(out), voice=voice or None,
                           engine=args.engine or None)
                ok += 1
            except Exception as e:  # noqa: BLE001
                log("prepare", f"  跳过 {voice}：{str(e)[:120]}")
        log("prepare", f"完成 {ok}/{total}")
        return 0 if ok else 1

    tts.sample(args.text, Path(args.out), voice=args.voice or None,
               engine=args.engine or None)
    return 0


if __name__ == "__main__":
    sys.exit(main())
