"""第 6 步：准备发布 / 自动投稿。

默认根据中文译文生成 B 站标题/简介/标签/分区建议，连同成片、封面一起放进
work/<id>/publish/。当 mode=upload 时，通过本地 Biliup cookie 自动投稿到 B 站。
"""
from __future__ import annotations

import json
import subprocess
import shutil
import sys
from pathlib import Path

import requests

from . import config
from .utils import load_json, log, save_json

_SYS = (
    "你是B站(哔哩哔哩)运营。根据给定的中文视频文案，生成适合B站的投稿信息。"
    "只输出 JSON：{\"title\":\"不超过80字的吸引人标题\","
    "\"desc\":\"简介，2-4句，可含简单换行\","
    "\"tags\":[\"标签1\",\"标签2\",\"...最多10个...\"],"
    "\"partition\":\"建议分区，如 知识/科技/生活/影视 等\"}。不要多余文字。"
)

_PARTITION_TID = {
    "知识": 36,
    "科技": 188,
    "生活": 160,
    "影视": 181,
    "人文历史": 124,
    "社科": 228,
    "法律": 228,
    "心理": 228,
    "科学科普": 201,
    "财经": 207,
    "商业": 207,
}


def _gen_metadata(text: str) -> dict:
    if not config.DEEPSEEK_API_KEY:
        raise RuntimeError("缺少 DEEPSEEK_API_KEY，无法生成投稿信息。")
    resp = requests.post(
        f"{config.DEEPSEEK_BASE_URL}/chat/completions",
        headers={"Authorization": f"Bearer {config.DEEPSEEK_API_KEY}",
                 "Content-Type": "application/json"},
        json={
            "model": config.DEEPSEEK_MODEL,
            "messages": [{"role": "system", "content": _SYS},
                         {"role": "user", "content": text[:4000]}],
            "temperature": 1.1,
            "response_format": {"type": "json_object"},
            "stream": False,
        },
        timeout=120,
    )
    resp.raise_for_status()
    return json.loads(resp.json()["choices"][0]["message"]["content"])


def gen_title(text: str) -> str:
    """用 DeepSeek 生成一句醒目的中文封面标题；无 key 时回退到首句。"""
    fallback = (text.strip().replace("\n", " ").split("。")[0] or "心灵之旅")[:18]
    if not config.DEEPSEEK_API_KEY:
        return fallback
    try:
        resp = requests.post(
            f"{config.DEEPSEEK_BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {config.DEEPSEEK_API_KEY}",
                     "Content-Type": "application/json"},
            json={"model": config.DEEPSEEK_MODEL,
                  "messages": [
                      {"role": "system", "content": "你是情感/心灵成长类视频的标题策划。"
                       "根据文案给一个吸引人、有共鸣的中文封面标题，12-18字，不要书名号引号，只输出标题。"},
                      {"role": "user", "content": text[:2000]}],
                  "temperature": 1.1, "stream": False},
            timeout=60,
        )
        resp.raise_for_status()
        t = resp.json()["choices"][0]["message"]["content"].strip().strip("《》\"'").splitlines()[0]
        return (t or fallback)[:20]
    except Exception:
        return fallback


def _tid_from_partition(partition: str, fallback: int | None = None) -> int:
    text = partition or ""
    for key, tid in _PARTITION_TID.items():
        if key in text:
            return tid
    return int(fallback or config.BILIBILI_TID)


def _upload_bilibili(meta: dict, *, final_video: Path, cover: Path | None,
                     tid: int, copyright: int) -> dict:
    cookie_file = Path(config.BILIBILI_COOKIE_FILE)
    if not cookie_file.exists():
        raise RuntimeError(
            f"缺少 B 站登录态：{cookie_file}。请先运行 python bili_login.py 扫码登录。"
        )
    if not final_video.exists():
        raise RuntimeError(f"找不到成片：{final_video}")

    log("publish", f"B站自动投稿：{(meta.get('title') or final_video.stem)[:80]}")
    meta = {**meta, "tid": int(tid), "copyright": int(copyright)}
    lines = list(dict.fromkeys([
        config.BILIBILI_UPLOAD_LINE,
        *getattr(config, "BILIBILI_UPLOAD_FALLBACK_LINES", []),
    ]))
    errors: list[str] = []
    temp_meta = final_video.parent / "upload_meta.json"
    temp_meta.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    for line in lines:
        out_json = final_video.parent / f"upload_result_{line.lower()}.json"
        out_json.unlink(missing_ok=True)
        cmd = [
            sys.executable, "-u", str(config.BASE_DIR / "bili_upload_once.py"),
            "--meta", str(temp_meta),
            "--video", str(final_video),
            "--line", line,
            "--out", str(out_json),
        ]
        if cover and cover.exists():
            cmd += ["--cover", str(cover)]
        log("publish", f"尝试 B站上传线路：{line}（最多等待 {config.BILIBILI_UPLOAD_TIMEOUT}s）")
        proc = subprocess.Popen(
            cmd,
            cwd=str(config.BASE_DIR),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        try:
            stdout, _ = proc.communicate(timeout=config.BILIBILI_UPLOAD_TIMEOUT)
        except subprocess.TimeoutExpired:
            try:
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception:
                proc.kill()
            msg = f"{line}: 上传超时"
            errors.append(msg)
            log("publish", f"线路超时，换下一条：{line}")
            continue

        for row in (stdout or "").splitlines():
            if row.strip():
                log("publish", row[-500:])
        if proc.returncode == 0 and out_json.exists():
            ret = json.loads(out_json.read_text(encoding="utf-8"))
            log("publish", f"B站投稿提交成功，线路：{line}")
            return ret
        msg = f"{line}: 退出码 {proc.returncode}"
        errors.append(msg)
        log("publish", f"线路失败，换下一条：{msg}")

    raise RuntimeError("B站视频上传失败，已尝试所有线路：" + " | ".join(errors))


def prepare(*, work_dir: Path, final_video: Path, platform: str = "bilibili",
            mode: str = "prepare", tid: int | None = None,
            copyright: int | None = None) -> dict:
    """生成投稿信息包，返回 metadata dict（同时写入 publish/metadata.json）。"""
    translated = load_json(work_dir / "translated.json") or []
    full_text = "".join(s.get("zh", "") for s in translated)[:4000]
    if not full_text.strip():
        raise RuntimeError("缺少翻译文案，请先完成翻译步骤。")

    log("publish", f"为 {platform} 生成投稿信息…")
    meta = _gen_metadata(full_text)

    pub_dir = work_dir / "publish"
    pub_dir.mkdir(exist_ok=True)
    # 拷贝成片
    if final_video.exists():
        shutil.copy(final_video, pub_dir / "video.mp4")
    # 封面：优先用 compose 生成的 cover.png
    cover = work_dir / "cover.png"
    if cover.exists():
        shutil.copy(cover, pub_dir / "cover.png")

    selected_tid = int(tid or _tid_from_partition(meta.get("partition", "")))
    selected_copyright = int(copyright or config.BILIBILI_COPYRIGHT)

    meta = {
        "platform": platform,
        "mode": mode,
        "title": meta.get("title", "")[:80],
        "desc": meta.get("desc", ""),
        "tags": meta.get("tags", [])[:10],
        "partition": meta.get("partition", ""),
        "tid": selected_tid,
        "copyright": selected_copyright,
        "video": str(pub_dir / "video.mp4"),
        "cover": str(pub_dir / "cover.png") if (pub_dir / "cover.png").exists() else "",
    }

    if platform == "bilibili" and mode == "upload":
        ret = _upload_bilibili(
            meta,
            final_video=pub_dir / "video.mp4",
            cover=(pub_dir / "cover.png" if (pub_dir / "cover.png").exists() else None),
            tid=selected_tid,
            copyright=selected_copyright,
        )
        meta["uploaded"] = True
        meta["upload_result"] = ret
        save_json(pub_dir / "upload_result.json", ret)
    else:
        meta["uploaded"] = False

    save_json(pub_dir / "metadata.json", meta)
    log("publish", f"投稿信息已生成：{meta['title']}")
    return meta
