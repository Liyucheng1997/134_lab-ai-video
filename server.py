"""Web 服务：6 步流水线，每步可单独配置/运行/预览，也可一键全跑。

- 每步以独立子进程（step.py）执行：崩溃隔离 + CUDA 安全（ASR/TTS 天然分进程）。
- 每步的日志/进度单独记录，前端显示在各自卡片上。
- 完成判定由 work/<id>/ 产物文件推断（重启可恢复）。
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, Form, UploadFile, File, HTTPException
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse,
                               StreamingResponse)

from src import config
from src.steps import STEP_DEFS, final_path, work_dir_of
from src.utils import load_json

app = FastAPI(title="AI 视频自动化 · 流水线")

WEB_DIR = config.BASE_DIR / "web"
UPLOAD_DIR = config.WORK_DIR / "_uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

_JOBS: dict[str, dict] = {}
_BILI_LOGINS: dict[str, dict] = {}
_LOCK = threading.Lock()
_RUN_LOCK = threading.Lock()
_STEP_KEYS = [s["key"] for s in STEP_DEFS]
_LOG_RE = re.compile(r"^\[[\d:]+\]\s*\[(\w+)\]\s*(.*)$")
_PCT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%")
_FRAC_RE = re.compile(r"\[?(\d+)\s*/\s*(\d+)\]?")


def _job(job_id: str) -> dict:
    with _LOCK:
        return _JOBS.setdefault(job_id, {
            "current": None, "running": False,
            "run": {k: {"logs": [], "progress": 0, "error": None} for k in _STEP_KEYS},
        })


def _artifact_done(job_id: str, step: str) -> bool:
    if step == "compose":
        return final_path(job_id).exists()
    art = next(s["artifact"] for s in STEP_DEFS if s["key"] == step)
    return (work_dir_of(job_id) / art).exists()


def _dependency_ready(job_id: str, step: str) -> tuple[bool, str]:
    need = next(s["needs"] for s in STEP_DEFS if s["key"] == step)
    if not need:
        return True, ""
    if _artifact_done(job_id, need):
        return True, ""
    need_name = next(s["name"] for s in STEP_DEFS if s["key"] == need)
    return False, f"请先完成上一步：{need_name}"


def _state(job_id: str) -> dict:
    j = _job(job_id)
    wd = work_dir_of(job_id)
    steps = {}
    for s in STEP_DEFS:
        k = s["key"]
        r = j["run"][k]
        if j["current"] == k and j["running"]:
            st = "running"
        elif r["error"]:
            st = "error"
        elif _artifact_done(job_id, k):
            st = "done"
        else:
            st = "pending"
        logs = r["logs"][-200:]
        result = load_json(wd / f"{k}.result.json") or {}
        progress = 100 if st == "done" else r["progress"]
        if not logs and st == "done":
            logs = [{"t": "--:--:--", "stage": "state", "msg": "步骤已完成（从产物文件恢复状态）"}]
        steps[k] = {"status": st, "error": r["error"], "progress": progress,
                    "logs": logs, "result": result}
    return {"job_id": job_id, "current": j["current"], "running": j["running"],
            "steps": steps, "has_video": (wd / "source.mp4").exists()}


def _on_line(job_id: str, step: str, line: str):
    j = _job(job_id)
    m = _LOG_RE.match(line)
    stage, msg = (m.group(1), m.group(2)) if m else ("log", line)
    with _LOCK:
        r = j["run"][step]
        r["logs"].append({"t": time.strftime("%H:%M:%S"), "stage": stage, "msg": msg})
        if len(r["logs"]) > 300:
            r["logs"] = r["logs"][-300:]
        mf = _FRAC_RE.search(msg)
        mp = _PCT_RE.search(msg)
        if mf:
            r["progress"] = round(int(mf.group(1)) / max(int(mf.group(2)), 1) * 100)
        elif mp:
            r["progress"] = min(100, round(float(mp.group(1))))


def _run_one(job_id: str, step: str, cfg: dict) -> int:
    """跑一步（不加锁）。返回退出码。调用方负责 _RUN_LOCK 与 current/running 状态。"""
    j = _job(job_id)
    wd = work_dir_of(job_id)
    (wd / "cfg").mkdir(exist_ok=True)
    cfg_path = wd / "cfg" / f"{step}.json"
    cfg_path.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    with _LOCK:
        j["run"][step] = {"logs": [], "progress": 0, "error": None}
        j["current"] = step
    _on_line(job_id, step, f"[{time.strftime('%H:%M:%S')}] [server] 启动步骤：{step}")

    env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8",
               KMP_DUPLICATE_LIB_OK="TRUE")
    cmd = [sys.executable, "-u", str(config.BASE_DIR / "step.py"),
           "--step", step, "--job-id", job_id, "--config", str(cfg_path)]
    proc = subprocess.Popen(cmd, cwd=str(config.BASE_DIR), env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace", bufsize=1)
    tail = []
    for line in proc.stdout:
        line = line.rstrip("\n")
        if line:
            tail.append(line); tail[:] = tail[-30:]
            _on_line(job_id, step, line)
    code = proc.wait()
    if not (code == 0 and _artifact_done(job_id, step)):
        msg = next((t for t in reversed(tail) if "失败" in t or "Error" in t),
                   f"步骤异常退出（{code}）")
        with _LOCK:
            j["run"][step]["error"] = msg
    else:
        with _LOCK:
            j["run"][step]["progress"] = 100
    return code


def _single_thread(job_id: str, step: str, cfg: dict):
    j = _job(job_id)
    with _RUN_LOCK:
        with _LOCK:
            j["running"] = True
        try:
            _run_one(job_id, step, cfg)
        finally:
            with _LOCK:
                j["running"] = False
                j["current"] = None


def _all_thread(job_id: str, configs: dict):
    j = _job(job_id)
    with _RUN_LOCK:
        with _LOCK:
            j["running"] = True
        try:
            for k in _STEP_KEYS:
                code = _run_one(job_id, k, configs.get(k, {}))
                if code != 0 or j["run"][k]["error"]:
                    break
        finally:
            with _LOCK:
                j["running"] = False
                j["current"] = None


# ------------------------------------------------------------------ 路由
@app.get("/", response_class=HTMLResponse)
def index():
    return (WEB_DIR / "index.html").read_text(encoding="utf-8")


@app.get("/api/config")
def get_config():
    from src.tts import _BUILTIN_VOICES
    return {
        "steps": STEP_DEFS,
        "voices": list(_BUILTIN_VOICES.keys()),
        "voice_default": config.TTS_VOICE,
        "speed_default": config.TTS_SPEED,
        "whisper_models": ["small", "large-v3-turbo"],
        "engines": [{"key": "deepseek", "name": "DeepSeek（大模型，质量高）"},
                    {"key": "google", "name": "Google 翻译（免费，快）"}],
        "sub_presets": config.SUB_PRESETS,
        "sub_preset_default": config.SUB_PRESET,
        "has_deepseek": bool(config.DEEPSEEK_API_KEY),
        "bilibili_logged_in": Path(config.BILIBILI_COOKIE_FILE).exists(),
    }


def _bili_login_worker(sid: str, value: dict):
    try:
        from biliup.plugins.bili_webup import BiliBili, Data

        bili = BiliBili(Data())
        with _LOCK:
            _BILI_LOGINS[sid]["status"] = "waiting"
            _BILI_LOGINS[sid]["message"] = "请用 B 站 App 扫码并确认登录"
        ret = asyncio.run(bili.login_by_qrcode(value))
        data = ret.get("data")
        if not data or not data.get("cookie_info"):
            raise RuntimeError(json.dumps(ret, ensure_ascii=False))
        cookie_file = Path(config.BILIBILI_COOKIE_FILE)
        cookie_file.parent.mkdir(parents=True, exist_ok=True)
        cookie_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        with _LOCK:
            _BILI_LOGINS[sid]["status"] = "done"
            _BILI_LOGINS[sid]["message"] = f"登录成功，已保存到 {cookie_file}"
    except Exception as e:  # noqa: BLE001
        with _LOCK:
            _BILI_LOGINS.setdefault(sid, {})["status"] = "error"
            _BILI_LOGINS[sid]["message"] = str(e)


@app.post("/api/bilibili/login/start")
def bili_login_start():
    try:
        import qrcode
        from biliup.plugins.bili_webup import BiliBili, Data
    except ImportError as e:
        raise HTTPException(500, "缺少 biliup 或 qrcode，请先安装依赖") from e

    sid = uuid.uuid4().hex[:12]
    qr_path = config.WORK_DIR / f"bilibili_login_{sid}.png"
    bili = BiliBili(Data())
    value = bili.get_qrcode()
    url = value.get("data", {}).get("url")
    if not url:
        raise HTTPException(502, f"获取 B 站登录二维码失败：{value}")
    qrcode.make(url).save(qr_path)
    with _LOCK:
        _BILI_LOGINS[sid] = {
            "status": "created",
            "message": "二维码已生成",
            "qr": str(qr_path),
            "created": time.time(),
        }
    threading.Thread(target=_bili_login_worker, args=(sid, value), daemon=True).start()
    return {"sid": sid, "qr": f"/api/bilibili/login/{sid}/qr"}


@app.get("/api/bilibili/login/{sid}/qr")
def bili_login_qr(sid: str):
    state = _BILI_LOGINS.get(sid)
    if not state:
        raise HTTPException(404, "登录会话不存在")
    return _serve(Path(state["qr"]), "image/png")


@app.get("/api/bilibili/login/{sid}/state")
def bili_login_state(sid: str):
    state = _BILI_LOGINS.get(sid)
    if not state:
        raise HTTPException(404, "登录会话不存在")
    return {
        "sid": sid,
        "status": state.get("status"),
        "message": state.get("message", ""),
        "logged_in": Path(config.BILIBILI_COOKIE_FILE).exists(),
    }


@app.get("/api/bilibili/login/status")
def bili_login_status():
    return {
        "logged_in": Path(config.BILIBILI_COOKIE_FILE).exists(),
        "cookie_file": config.BILIBILI_COOKIE_FILE,
    }


@app.post("/api/jobs")
def create_job():
    job_id = "job_" + uuid.uuid4().hex[:10]
    work_dir_of(job_id); _job(job_id)
    return {"job_id": job_id}


@app.get("/api/jobs/{job_id}/state")
def get_state(job_id: str):
    return _state(job_id)


def _save_upload(job_id: str, file: UploadFile) -> str:
    suffix = Path(file.filename).suffix or ".mp4"
    saved = UPLOAD_DIR / f"{job_id}{suffix}"
    with saved.open("wb") as f:
        shutil.copyfileobj(file.file, f)
    return str(saved)


@app.post("/api/jobs/{job_id}/run")
async def run_step(job_id: str, step: str = Form(...), config_json: str = Form("{}"),
                   file: UploadFile | None = File(default=None)):
    if step not in _STEP_KEYS:
        raise HTTPException(400, "未知步骤")
    if _job(job_id)["running"]:
        raise HTTPException(409, "已有步骤在运行")
    ok, reason = _dependency_ready(job_id, step)
    if not ok:
        raise HTTPException(409, reason)
    try:
        cfg = json.loads(config_json or "{}")
    except json.JSONDecodeError:
        cfg = {}
    if file is not None and file.filename:
        cfg["file"] = _save_upload(job_id, file)
    threading.Thread(target=_single_thread, args=(job_id, step, cfg), daemon=True).start()
    return {"ok": True}


@app.post("/api/jobs/{job_id}/run_all")
async def run_all(job_id: str, configs_json: str = Form("{}"),
                  file: UploadFile | None = File(default=None)):
    if _job(job_id)["running"]:
        raise HTTPException(409, "已有步骤在运行")
    try:
        configs = json.loads(configs_json or "{}")
    except json.JSONDecodeError:
        configs = {}
    if file is not None and file.filename:
        configs.setdefault("download", {})["file"] = _save_upload(job_id, file)
    threading.Thread(target=_all_thread, args=(job_id, configs), daemon=True).start()
    return {"ok": True}


@app.get("/api/jobs/{job_id}/stream")
async def stream(job_id: str):
    async def gen():
        last = None
        while True:
            snap = json.dumps(_state(job_id), ensure_ascii=False)
            if snap != last:
                yield f"data: {snap}\n\n"
                last = snap
            await asyncio.sleep(0.4)
    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache"})


# --------- 预览数据 ---------
@app.get("/api/jobs/{job_id}/transcript")
def transcript(job_id: str):
    data = load_json(work_dir_of(job_id) / "segments.json")
    segs = data["segments"] if isinstance(data, dict) else (data or [])
    return {"segments": segs}


@app.get("/api/jobs/{job_id}/translation")
def translation(job_id: str):
    return {"segments": load_json(work_dir_of(job_id) / "translated.json") or []}


@app.get("/api/jobs/{job_id}/cues")
def cues(job_id: str):
    from src.subtitles import cue_list
    segs = load_json(work_dir_of(job_id) / "dub_segments.json") \
        or load_json(work_dir_of(job_id) / "translated.json") or []
    return {"cues": cue_list(segs)}


@app.get("/api/jobs/{job_id}/metadata")
def metadata(job_id: str):
    return load_json(work_dir_of(job_id) / "publish" / "metadata.json") or {}


# --------- 文件 ---------
def _serve(path: Path, media: str, download: bool = False):
    if not path.exists():
        raise HTTPException(404, "文件不存在")
    return FileResponse(path, media_type=media, filename=path.name if download else None)


@app.get("/api/jobs/{job_id}/dub.wav")
def dub(job_id: str):
    return _serve(work_dir_of(job_id) / "dub.wav", "audio/wav")


@app.get("/api/jobs/{job_id}/video.mp4")
def video(job_id: str, download: bool = False):
    return _serve(final_path(job_id), "video/mp4", download)


@app.get("/api/jobs/{job_id}/cover.png")
def cover(job_id: str):
    return _serve(work_dir_of(job_id) / "cover.png", "image/png")


@app.get("/api/jobs/{job_id}/source.mp4")
def source(job_id: str):
    return _serve(work_dir_of(job_id) / "source.mp4", "video/mp4")


@app.get("/api/jobs/{job_id}/sub")
def sub(job_id: str, fmt: str = "srt"):
    ext = {"ass": "subs.ass", "srt": "subs.srt", "vtt": "subs.vtt"}.get(fmt, "subs.srt")
    return _serve(work_dir_of(job_id) / ext, "text/plain", download=True)


_SAMPLE_DIR = config.WORK_DIR / "_samples"
_SAMPLE_DIR.mkdir(parents=True, exist_ok=True)


@app.post("/api/voice-sample")
def voice_sample(voice: str = Form(...), text: str = Form("你好，这是配音音色试听。")):
    # 固定试听文案 → 按音色缓存，生成一次后直接复用（快）
    key = uuid.uuid5(uuid.NAMESPACE_DNS, voice).hex[:10]
    out = _SAMPLE_DIR / f"{key}.wav"
    if not out.exists():
        with _RUN_LOCK:
            if not out.exists():
                cmd = [sys.executable, "-u", str(config.BASE_DIR / "voice_sample.py"),
                       "--voice", voice, "--text", text[:60], "--out", str(out)]
                r = subprocess.run(cmd, cwd=str(config.BASE_DIR), capture_output=True, text=True)
                if r.returncode != 0 or not out.exists():
                    raise HTTPException(500, "试听合成失败")
    return _serve(out, "audio/wav")


if __name__ == "__main__":
    import uvicorn
    print("打开 http://127.0.0.1:8800")
    uvicorn.run(app, host="127.0.0.1", port=8800)
