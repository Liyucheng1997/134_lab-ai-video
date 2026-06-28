"""Web 服务：上传视频 / 填链接 → 中文配音+硬字幕 mp4，实时显示每一步进度。

用 114 听书软件的 venv 运行（见 web.ps1）：
    uvicorn 由本文件内启动，浏览器打开 http://127.0.0.1:8800
"""
from __future__ import annotations

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
from fastapi.responses import (FileResponse, HTMLResponse, StreamingResponse,
                               JSONResponse)

from src import config, orchestrator

app = FastAPI(title="AI 视频自动化")

# ---------------------------------------------------------------- 任务状态
_JOBS: dict[str, dict] = {}
_JOBS_LOCK = threading.Lock()
_GPU_LOCK = threading.Lock()          # 单卡：同一时刻只跑一个任务
WEB_DIR = config.BASE_DIR / "web"
UPLOAD_DIR = config.WORK_DIR / "_uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

_STEP_KEYS = [k for k, _ in orchestrator.STEPS]
# 日志 stage → 步骤 key 的映射（其余 stage 归到“当前步骤”的明细）
_STAGE_TO_STEP = {
    "download": "download", "asr": "asr", "translate": "translate",
    "tts": "tts", "subs": "subs", "mux": "mux",
}


def _new_job() -> dict:
    return {
        "status": "queued",                 # queued|running|done|error
        "steps": {k: "pending" for k in _STEP_KEYS},  # pending|running|done
        "current": None,
        "percent": 0,
        "logs": [],
        "error": None,
        "result": None,                     # 下载/预览用相对 URL
        "created": time.time(),
    }


def _set(job_id: str, **kw):
    with _JOBS_LOCK:
        _JOBS[job_id].update(kw)


def _on_log(job_id: str, stage: str, msg: str):
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if not job:
            return
        job["logs"].append({"t": time.strftime("%H:%M:%S"), "stage": stage, "msg": msg})
        if len(job["logs"]) > 500:
            job["logs"] = job["logs"][-500:]

        step = _STAGE_TO_STEP.get(stage)
        if step:
            idx = _STEP_KEYS.index(step)
            for k in _STEP_KEYS[:idx]:
                job["steps"][k] = "done"
            job["steps"][step] = "running"
            job["current"] = step
            # 估算总进度：已完成步骤 + 当前步骤内的细分（tts 的 [i/n]）
            frac = 0.5
            m = re.search(r"\[(\d+)/(\d+)\]", msg)
            if m:
                frac = int(m.group(1)) / max(int(m.group(2)), 1)
            job["percent"] = round((idx + frac) / len(_STEP_KEYS) * 100)


_LOG_RE = re.compile(r"^\[[\d:]+\]\s*\[(\w+)\]\s*(.*)$")


def _worker(job_id: str, cmd: list[str]):
    """在独立子进程里跑流水线，解析其 stdout 驱动进度。

    子进程隔离了 torch/ctranslate2/CUDA 的原生崩溃——即使它挂了，Web 服务也不受影响。
    """
    _set(job_id, status="queued")
    with _GPU_LOCK:                         # 排队，单卡串行
        _set(job_id, status="running")
        env = dict(os.environ)
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        env["KMP_DUPLICATE_LIB_OK"] = "TRUE"

        proc = subprocess.Popen(
            cmd, cwd=str(config.BASE_DIR), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
        )
        tail: list[str] = []
        for line in proc.stdout:                       # 实时逐行读取
            line = line.rstrip("\n")
            if not line:
                continue
            tail.append(line)
            if len(tail) > 40:
                tail.pop(0)
            m = _LOG_RE.match(line)
            if m:
                _on_log(job_id, m.group(1), m.group(2))
            else:                                       # 非标准行（如 torch 警告）也存进日志
                _on_log(job_id, "log", line)
        code = proc.wait()

        out_file = config.OUTPUT_DIR / f"{job_id}_zh.mp4"
        if code == 0 and out_file.exists():
            with _JOBS_LOCK:
                job = _JOBS[job_id]
                for k in _STEP_KEYS:
                    job["steps"][k] = "done"
                job["status"] = "done"
                job["percent"] = 100
                job["current"] = None
                job["result"] = f"/api/jobs/{job_id}/file"
        else:
            reason = "进程异常退出" if code != 0 else "未生成成片"
            detail = f"{reason}（退出码 {code}）\n" + "\n".join(tail[-12:])
            _set(job_id, status="error", error=detail)
            _on_log(job_id, "error", reason)


# ---------------------------------------------------------------- 路由
@app.get("/", response_class=HTMLResponse)
def index():
    return (WEB_DIR / "index.html").read_text(encoding="utf-8")


@app.post("/api/jobs")
async def create_job(
    url: str = Form(default=""),
    voice: str = Form(default=""),
    whisper: str = Form(default=""),
    language: str = Form(default=""),
    file: UploadFile | None = File(default=None),
):
    url = (url or "").strip()
    if not url and file is None:
        raise HTTPException(400, "请提供 YouTube 链接或上传视频文件")

    job_id = "job_" + uuid.uuid4().hex[:10]
    out_file = config.OUTPUT_DIR / f"{job_id}_zh.mp4"

    cmd = [sys.executable, "-u", str(config.BASE_DIR / "pipeline.py"),
           "--job-id", job_id, "--out", str(out_file)]

    if file is not None and file.filename:
        suffix = Path(file.filename).suffix or ".mp4"
        saved = UPLOAD_DIR / f"{job_id}{suffix}"
        with saved.open("wb") as f:
            shutil.copyfileobj(file.file, f)
        cmd += ["--file", str(saved)]
    else:
        cmd += [url]

    if voice:
        cmd += ["--voice", voice]
    if whisper:
        cmd += ["--whisper", whisper]
    if language:
        cmd += ["--lang", language]

    with _JOBS_LOCK:
        _JOBS[job_id] = _new_job()
    threading.Thread(target=_worker, args=(job_id, cmd), daemon=True).start()
    return {"job_id": job_id}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if not job:
            raise HTTPException(404, "任务不存在")
        return JSONResponse(dict(job))


@app.get("/api/jobs/{job_id}/stream")
async def stream_job(job_id: str):
    if job_id not in _JOBS:
        raise HTTPException(404, "任务不存在")

    async def gen():
        import asyncio
        last = None
        while True:
            with _JOBS_LOCK:
                job = _JOBS.get(job_id)
                snapshot = json.dumps(job, ensure_ascii=False) if job else None
            if snapshot and snapshot != last:
                yield f"data: {snapshot}\n\n"
                last = snapshot
            if job and job["status"] in ("done", "error"):
                break
            await asyncio.sleep(0.4)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache"})


@app.get("/api/jobs/{job_id}/file")
def get_file(job_id: str, download: bool = False):
    out = config.OUTPUT_DIR / f"{job_id}_zh.mp4"
    if not out.exists():
        raise HTTPException(404, "成片尚未生成")
    return FileResponse(
        out, media_type="video/mp4",
        filename=f"{job_id}_zh.mp4" if download else None,
    )


@app.get("/api/voices")
def voices():
    from src.tts import _BUILTIN_VOICES
    return {"voices": list(_BUILTIN_VOICES.keys()), "default": config.TTS_VOICE}


if __name__ == "__main__":
    import uvicorn
    print("打开 http://127.0.0.1:8800")
    uvicorn.run(app, host="127.0.0.1", port=8800)
