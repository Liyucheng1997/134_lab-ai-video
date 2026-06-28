"""B 站扫码登录：生成自动投稿所需的 biliup cookie。

用法：
    python bili_login.py

扫码成功后会写入 config.BILIBILI_COOKIE_FILE，默认是 work/bilibili.cookie.json。
"""
from __future__ import annotations

import asyncio
import json
import sys
import webbrowser
from pathlib import Path

import qrcode

from src import config
from src.utils import log


def main() -> int:
    try:
        from biliup.plugins.bili_webup import BiliBili, Data
    except ImportError:
        log("bili", "缺少 biliup，请先安装：pip install biliup")
        return 1

    cookie_file = Path(config.BILIBILI_COOKIE_FILE)
    cookie_file.parent.mkdir(parents=True, exist_ok=True)
    qr_file = config.WORK_DIR / "bilibili_login_qr.png"

    bili = BiliBili(Data())
    value = bili.get_qrcode()
    url = value.get("data", {}).get("url")
    if not url:
        log("bili", f"获取二维码失败：{json.dumps(value, ensure_ascii=False)}")
        return 1

    img = qrcode.make(url)
    img.save(qr_file)
    log("bili", f"二维码已生成：{qr_file}")
    log("bili", "请用 B 站 App 扫码并确认登录，脚本会等待最多 120 秒。")
    webbrowser.open(str(qr_file))

    try:
        ret = asyncio.run(bili.login_by_qrcode(value))
    except Exception as e:  # noqa: BLE001
        log("bili", f"扫码登录失败：{e}")
        return 1

    data = ret.get("data")
    if not data or not data.get("cookie_info"):
        log("bili", f"扫码返回异常：{json.dumps(ret, ensure_ascii=False)}")
        return 1

    cookie_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    log("bili", f"登录态已保存：{cookie_file}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
