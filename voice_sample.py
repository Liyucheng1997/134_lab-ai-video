"""一次性子进程：合成一段音色试听音频。

用法：python voice_sample.py --voice 温柔女声 --text "你好" --out path.wav
单独进程跑（加载 torch/F5），避免污染主服务进程。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--voice", default="")
    ap.add_argument("--text", default="你好，这是配音音色试听。")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    from src import tts
    tts.sample(args.text, Path(args.out), voice=args.voice or None)
    return 0


if __name__ == "__main__":
    sys.exit(main())
