"""单步执行器：以独立子进程跑流水线的某一步。

用法：python step.py --step tts --job-id job_xxx --config work/job_xxx/cfg/tts.json
进度通过 stdout 的 [stage] 日志上浮给 server.py。
"""
from __future__ import annotations

import argparse
import json
import sys

from src import config
from src.utils import log


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--step", required=True)
    ap.add_argument("--job-id", required=True)
    ap.add_argument("--config", help="该步配置的 JSON 文件路径")
    args = ap.parse_args()

    cfg = {}
    if args.config:
        try:
            cfg = json.loads(open(args.config, encoding="utf-8").read())
        except FileNotFoundError:
            cfg = {}

    from src import steps  # 延迟导入，torch/ctranslate2 只在对应步骤进程里加载

    try:
        result = steps.run_step(args.step, args.job_id, cfg)
        label = "归档保存" if args.step == "publish" else args.step
        log("step", f"{label} 完成 {json.dumps(result, ensure_ascii=False)}")
        return 0
    except Exception as e:  # noqa: BLE001
        label = "归档保存" if args.step == "publish" else args.step
        log("error", f"{label} 失败：{e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
