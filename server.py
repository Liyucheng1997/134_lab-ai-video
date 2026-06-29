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

from fastapi import FastAPI, Form, UploadFile, File, HTTPException, Body
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse,
                               StreamingResponse)

from src import config
from src.steps import STEP_DEFS, final_path, work_dir_of
from src.utils import load_json, media_duration

app = FastAPI(title="AI 视频自动化 · 流水线")

WEB_DIR = config.BASE_DIR / "web"
UPLOAD_DIR = config.WORK_DIR / "_uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
QUEUE_STATE_FILE = config.OUTPUT_DIR / "_queue_state.json"
JOB_DIRS_FILE = config.OUTPUT_DIR / "_job_dirs.json"
ESTIMATE_CALIBRATION_FILE = config.OUTPUT_DIR / "_estimate_calibration.json"

_JOBS: dict[str, dict] = {}
_BILI_LOGINS: dict[str, dict] = {}
_QUEUE: list[dict] = []
_QUEUE_RUNNING = False
_QUEUE_STOP = False
_LOCK = threading.Lock()
_RUN_LOCK = threading.Lock()
_JOB_DIRS_WRITE_LOCK = threading.Lock()
_STEP_KEYS = [s["key"] for s in STEP_DEFS]
_LOG_RE = re.compile(r"^\[[\d:]+\]\s*\[(\w+)\]\s*(.*)$")
_PCT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%")
_FRAC_RE = re.compile(r"\[?(\d+)\s*/\s*(\d+)\]?")
_QUEUE_ITEM_KEYS = {
    "id", "job_id", "url", "title", "status", "error", "order",
    "duration_sec", "output_dir", "project_dir", "created_at",
    "started_at", "ended_at",
}

_DEFAULT_SECONDS_PER_SENTENCE = {
    "download": 0.35,
    "asr": 0.55,
    "translate": 0.35,
    "tts": 4.0,
    "compose": 0.35,
    "publish": 0.25,
}
_STEP_MIN_ESTIMATE = {
    "download": 5,
    "asr": 5,
    "translate": 4,
    "tts": 20,
    "compose": 5,
    "publish": 4,
}


def _job(job_id: str) -> dict:
    with _LOCK:
        return _JOBS.setdefault(job_id, _default_job_state())


def _work_path(job_id: str) -> Path:
    data = load_json(JOB_DIRS_FILE) or {}
    entry = data.get(job_id) if isinstance(data, dict) else None
    if isinstance(entry, dict) and entry.get("cache_dir"):
        return Path(entry["cache_dir"])
    return config.WORK_DIR / job_id


def _safe_work_path(job_id: str) -> Path:
    path = _work_path(job_id).resolve()
    roots = [config.WORK_DIR.resolve(), config.OUTPUT_DIR.resolve()]
    if path in roots or not any(root in path.parents for root in roots):
        raise RuntimeError(f"拒绝清理异常 work 路径：{path}")
    if path.name not in {job_id, "cache"}:
        raise RuntimeError(f"拒绝清理异常 job 目录：{path}")
    return path


def _default_job_state() -> dict:
    return {
        "current": None, "running": False,
        "archive_meta": {}, "cleaned": False,
        "run": {k: {"logs": [], "progress": 0, "error": None,
                    "started_at": None, "ended_at": None} for k in _STEP_KEYS},
    }


def _safe_dir_part(text: str, fallback: str = "待处理视频") -> str:
    text = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", str(text or ""))
    text = re.sub(r"\s+", "", text).strip(". ")
    return (text or fallback)[:20]


def _next_project_dir(theme: str = "待处理视频") -> Path:
    stamp = time.strftime("%Y%m%d")
    base = config.OUTPUT_DIR / f"{stamp}_{_safe_dir_part(theme)}"
    project_dir = base
    suffix = 2
    while project_dir.exists():
        project_dir = config.OUTPUT_DIR / f"{base.name}_{suffix:02d}"
        suffix += 1
    project_dir.mkdir(parents=True)
    (project_dir / "cache").mkdir(exist_ok=True)
    return project_dir


def _theme_from_text(text: str) -> str:
    chars = "".join(re.findall(r"[\u4e00-\u9fff]", str(text or "")))
    if len(chars) >= 4:
        return chars[:8]
    return "待处理视频"


def _rename_pending_project(item: dict, title: str) -> None:
    return
    if item.get("status") != "pending":
        return
    theme = _theme_from_text(title)
    if theme == "待处理视频":
        return
    project_value = item.get("project_dir") or item.get("output_dir")
    if not project_value:
        return
    project = Path(project_value)
    if not project.exists() or (project / "metadata.json").exists():
        return
    m = re.match(r"^(\d+)[_-].*_(\d{8})(?:_\d+)?$", project.name)
    if not m:
        return
    base = project.parent / f"{m.group(1)}_{_safe_dir_part(theme)}_{m.group(2)}"
    target = base
    suffix = 2
    while target.exists() and target.resolve() != project.resolve():
        target = project.parent / f"{base.name}_{suffix:02d}"
        suffix += 1
    if target.resolve() != project.resolve():
        project.rename(target)
    item["project_dir"] = str(target)
    item["output_dir"] = str(target)
    _register_job_project(item["job_id"], target)


def _read_job_dirs() -> dict:
    data = load_json(JOB_DIRS_FILE) or {}
    return data if isinstance(data, dict) else {}


def _write_job_dirs(data: dict) -> None:
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    last_error: Exception | None = None
    with _JOB_DIRS_WRITE_LOCK:
        for attempt in range(8):
            tmp = JOB_DIRS_FILE.with_name(
                f"{JOB_DIRS_FILE.name}.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex}.tmp"
            )
            try:
                tmp.write_text(payload, encoding="utf-8")
                os.replace(tmp, JOB_DIRS_FILE)
                return
            except PermissionError as e:
                last_error = e
                time.sleep(0.05 * (attempt + 1))
            finally:
                tmp.unlink(missing_ok=True)
    if last_error:
        raise last_error


def _register_job_project(job_id: str, project_dir: str | Path) -> Path:
    project = Path(project_dir)
    cache = project / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    data = _read_job_dirs()
    data[job_id] = {"project_dir": str(project), "cache_dir": str(cache)}
    _write_job_dirs(data)
    return project


def _job_project_dir(job_id: str) -> Path | None:
    data = _read_job_dirs()
    entry = data.get(job_id)
    if isinstance(entry, dict) and entry.get("project_dir"):
        return Path(entry["project_dir"])
    return None


def _remove_job_mapping(job_id: str) -> None:
    data = _read_job_dirs()
    if job_id in data:
        data.pop(job_id, None)
        _write_job_dirs(data)


def _safe_remove_path(path: Path, root: Path) -> None:
    if not path:
        return
    try:
        resolved = path.resolve()
    except FileNotFoundError:
        resolved = path.absolute()
    root = root.resolve()
    if resolved == root or root not in resolved.parents:
        return
    if resolved.exists():
        if resolved.is_dir():
            shutil.rmtree(resolved)
        else:
            resolved.unlink(missing_ok=True)


def _delete_job_files(item: dict) -> None:
    job_id = str(item.get("job_id") or "")
    if not job_id:
        return
    paths: set[Path] = set()
    for key in ("project_dir", "output_dir"):
        value = item.get(key)
        if value:
            paths.add(Path(value))
    project = _job_project_dir(job_id)
    if project:
        paths.add(project)
    legacy_work = config.WORK_DIR / job_id

    for path in sorted(paths, key=lambda p: len(str(p)), reverse=True):
        _safe_remove_path(path, config.OUTPUT_DIR)
    _safe_remove_path(legacy_work, config.WORK_DIR)
    for uploaded in UPLOAD_DIR.glob(f"{job_id}.*"):
        if uploaded.is_file():
            uploaded.unlink(missing_ok=True)
    _remove_job_mapping(job_id)
    with _LOCK:
        _JOBS.pop(job_id, None)


def _cfg_for_step(job_id: str, step: str, cfg: dict) -> dict:
    cfg = dict(cfg or {})
    if step == "publish":
        project = _job_project_dir(job_id)
        if project:
            cfg.setdefault("archive_dir", str(project))
    return cfg


_STEP_OUTPUTS = {
    "download": ["source.mp4", "source.wav", "source.m4a"],
    "asr": ["segments.json"],
    "translate": ["translated.json"],
    "tts": ["dub.wav", "dub_segments.json"],
    "compose": ["subs.srt", "subs.vtt", "subs.ass", "cover_title.txt", "cover.png", "final.mp4"],
    "publish": ["publish"],
}
_ARCHIVE_OUTPUTS = [
    "video.mp4", "cover.png", "title.txt", "description.txt",
    "tags.txt", "publish_info.md", "metadata.json",
]


def _clear_from_step(job_id: str, start_step: str) -> None:
    if start_step not in _STEP_KEYS:
        raise HTTPException(400, "未知步骤")
    wd = _safe_work_path(job_id)
    start = _STEP_KEYS.index(start_step)
    for step in _STEP_KEYS[start:]:
        for name in _STEP_OUTPUTS.get(step, []):
            path = wd / name
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
            else:
                path.unlink(missing_ok=True)
        (wd / f"{step}.result.json").unlink(missing_ok=True)
    if start_step != "publish":
        project = _job_project_dir(job_id)
        if project:
            for name in _ARCHIVE_OUTPUTS:
                path = project / name
                if path.is_dir():
                    shutil.rmtree(path, ignore_errors=True)
                else:
                    path.unlink(missing_ok=True)
    with _LOCK:
        j = _JOBS.setdefault(job_id, _default_job_state())
        j["cleaned"] = False
        if start_step != "publish":
            j["archive_meta"] = {}
        for step in _STEP_KEYS[start:]:
            j["run"][step] = {"logs": [], "progress": 0, "error": None,
                              "started_at": None, "ended_at": None}


def _update_queue_item_for_job(job_id: str, **changes) -> None:
    with _LOCK:
        item = next((x for x in _QUEUE if x.get("job_id") == job_id), None)
        if not item:
            return
        item.update(changes)
        if "output_dir" in changes and changes["output_dir"]:
            item["project_dir"] = changes["output_dir"]
        _save_queue_state_unlocked()


def _remember_archive_meta(job_id: str, meta: dict | None = None) -> dict:
    if meta is None:
        with _LOCK:
            cached = dict((_JOBS.get(job_id) or {}).get("archive_meta") or {})
        if cached:
            return cached
        meta = load_json(_work_path(job_id) / "publish" / "metadata.json") or {}
    if meta:
        with _LOCK:
            j = _JOBS.setdefault(job_id, _default_job_state())
            j["archive_meta"] = dict(meta)
    return meta or {}


def _archive_file(meta: dict, key: str, fallback_name: str) -> Path | None:
    cand = str(meta.get(key) or "").strip()
    if cand:
        path = Path(cand)
        if path.exists():
            return path
    archive_dir = str(meta.get("archive_dir") or "").strip()
    if archive_dir:
        path = Path(archive_dir) / fallback_name
        if path.exists():
            return path
    return None


def _cleanup_success_cache(job_id: str, meta: dict | None = None) -> dict:
    meta = _remember_archive_meta(job_id, meta)
    archive_dir_value = str(meta.get("archive_dir") or "").strip()
    if not meta or not archive_dir_value:
        return meta
    archive_dir = Path(archive_dir_value)
    if not archive_dir.exists():
        return meta

    wd = _safe_work_path(job_id)
    output_root = config.OUTPUT_DIR.resolve()
    wd_in_output = output_root in wd.resolve().parents
    if wd.exists() and not wd_in_output:
        shutil.rmtree(wd)
    for uploaded in UPLOAD_DIR.glob(f"{job_id}.*"):
        if uploaded.is_file():
            uploaded.unlink(missing_ok=True)
    with _LOCK:
        j = _JOBS.setdefault(job_id, _default_job_state())
        j["cleaned"] = not wd_in_output
        j["archive_meta"] = dict(meta)
    _register_job_project(job_id, archive_dir)
    return meta


def _queue_item_for_archive(archive_dir: Path, meta: dict) -> dict:
    archive_key = str(archive_dir.resolve()).lower()
    return {
        "id": "qh_" + uuid.uuid5(uuid.NAMESPACE_URL, archive_key).hex[:10],
        "job_id": "job_" + uuid.uuid5(uuid.NAMESPACE_DNS, archive_key).hex[:10],
        "url": "",
        "title": str(meta.get("title") or archive_dir.name),
        "status": "done",
        "error": None,
        "order": 0,
        "duration_sec": None,
        "output_dir": str(archive_dir),
        "project_dir": str(archive_dir),
        "created_at": archive_dir.stat().st_mtime,
        "started_at": None,
        "ended_at": archive_dir.stat().st_mtime,
    }


def _normalize_queue_item(item: dict, order: int) -> dict:
    out = {k: item.get(k) for k in _QUEUE_ITEM_KEYS}
    out["id"] = str(out.get("id") or ("qi_" + uuid.uuid4().hex[:10]))
    out["job_id"] = str(out.get("job_id") or ("job_" + uuid.uuid4().hex[:10]))
    out["url"] = str(out.get("url") or "")
    out["title"] = str(out.get("title") or "")
    out["status"] = str(out.get("status") or "pending")
    out["error"] = out.get("error")
    out["order"] = order
    out["duration_sec"] = out.get("duration_sec")
    out["output_dir"] = str(out.get("output_dir") or "")
    out["project_dir"] = str(out.get("project_dir") or out["output_dir"] or "")
    out["created_at"] = float(out.get("created_at") or time.time())
    out["started_at"] = out.get("started_at")
    out["ended_at"] = out.get("ended_at")
    if out["status"] == "running":
        out["status"] = "pending"
        out["error"] = "服务重启中断，已恢复为待执行"
        out["started_at"] = None
        out["ended_at"] = None
    if out["project_dir"]:
        _register_job_project(out["job_id"], out["project_dir"])
    if out["status"] == "done" and out["output_dir"]:
        meta = load_json(Path(out["output_dir"]) / "metadata.json") or {}
        if meta:
            _remember_archive_meta(out["job_id"], meta)
            with _LOCK:
                j = _JOBS.setdefault(out["job_id"], _default_job_state())
                j["cleaned"] = True
                j["archive_meta"] = dict(meta)
    return out


def _save_queue_state_unlocked() -> None:
    payload = {
        "version": 1,
        "updated_at": time.time(),
        "items": [{k: item.get(k) for k in _QUEUE_ITEM_KEYS} for item in _QUEUE],
    }
    data = json.dumps(payload, ensure_ascii=False, indent=2)
    last_error: Exception | None = None
    for attempt in range(8):
        tmp = QUEUE_STATE_FILE.with_name(
            f"{QUEUE_STATE_FILE.name}.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex}.tmp"
        )
        try:
            tmp.write_text(data, encoding="utf-8")
            os.replace(tmp, QUEUE_STATE_FILE)
            return
        except PermissionError as e:
            last_error = e
            time.sleep(0.05 * (attempt + 1))
        finally:
            tmp.unlink(missing_ok=True)
    if last_error:
        raise last_error


def _save_queue_state() -> None:
    with _LOCK:
        _save_queue_state_unlocked()


def _load_queue_state() -> None:
    restored: list[dict] = []
    legacy_queue_file = config.WORK_DIR / "queue_state.json"
    seen_outputs: set[str] = set()
    state_file = QUEUE_STATE_FILE if QUEUE_STATE_FILE.exists() else legacy_queue_file
    data = load_json(state_file) or {}
    raw_items = data.get("items") if isinstance(data, dict) else []
    if isinstance(raw_items, list):
        restored = [_normalize_queue_item(x, i) for i, x in enumerate(raw_items) if isinstance(x, dict)]

    if not QUEUE_STATE_FILE.exists():
        seen_outputs = {str(x.get("output_dir") or "").lower() for x in restored if x.get("output_dir")}
        for meta_path in sorted(config.OUTPUT_DIR.glob("*/metadata.json")):
            meta = load_json(meta_path) or {}
            archive_dir = meta_path.parent
            archive_key = str(archive_dir.resolve()).lower()
            if archive_key in seen_outputs:
                continue
            item = _queue_item_for_archive(archive_dir, meta)
            item["order"] = len(restored)
            restored.append(item)
            seen_outputs.add(archive_key)
            _register_job_project(item["job_id"], archive_dir)
            _remember_archive_meta(item["job_id"], meta)
            with _LOCK:
                j = _JOBS.setdefault(item["job_id"], _default_job_state())
                j["cleaned"] = True
                j["archive_meta"] = dict(meta)

    with _LOCK:
        _QUEUE[:] = restored
        for i, item in enumerate(_QUEUE):
            item["order"] = i
        _save_queue_state_unlocked()


def _artifact_done(job_id: str, step: str) -> bool:
    with _LOCK:
        if (_JOBS.get(job_id) or {}).get("cleaned"):
            return True
    if step == "compose":
        return (_work_path(job_id) / "final.mp4").exists()
    art = next(s["artifact"] for s in STEP_DEFS if s["key"] == step)
    return (_work_path(job_id) / art).exists()


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
    wd = _work_path(job_id)
    steps = {}
    estimates = _estimate_job(job_id)
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
        started = r.get("started_at")
        ended = r.get("ended_at")
        elapsed = (ended or time.time()) - started if started else 0
        steps[k] = {"status": st, "error": r["error"], "progress": progress,
                    "logs": logs, "result": result, "elapsed_sec": round(elapsed),
                    "estimate_sec": estimates["steps"].get(k, 0)}
    return {"job_id": job_id, "current": j["current"], "running": j["running"],
            "steps": steps, "has_video": (wd / "source.mp4").exists(),
            "estimate": estimates}


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
    cfg = _cfg_for_step(job_id, step, cfg)
    wd = work_dir_of(job_id)
    (wd / "cfg").mkdir(exist_ok=True)
    cfg_path = wd / "cfg" / f"{step}.json"
    cfg_path.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    with _LOCK:
        j["run"][step] = {"logs": [], "progress": 0, "error": None,
                          "started_at": time.time(), "ended_at": None}
        j["current"] = step
    step_name = next((s["name"] for s in STEP_DEFS if s["key"] == step), step)
    _on_line(job_id, step, f"[{time.strftime('%H:%M:%S')}] [server] 启动步骤：{step_name}")

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
    with _LOCK:
        j["run"][step]["ended_at"] = time.time()
    if code == 0 and not j["run"][step]["error"] and _artifact_done(job_id, step):
        _update_estimate_calibration(job_id, step)
    return code


def _single_thread(job_id: str, step: str, cfg: dict):
    j = _job(job_id)
    with _RUN_LOCK:
        with _LOCK:
            j["running"] = True
        _update_queue_item_for_job(job_id, status="running", error=None,
                                   started_at=time.time(), ended_at=None)
        try:
            code = _run_one(job_id, step, cfg)
            if step == "publish" and code == 0 and not j["run"][step]["error"]:
                meta = _cleanup_success_cache(job_id)
                _update_queue_item_for_job(job_id, status="done",
                                           ended_at=time.time(),
                                           output_dir=meta.get("archive_dir", ""),
                                           title=meta.get("project_title") or meta.get("title", ""))
            elif code != 0 or j["run"][step]["error"]:
                _update_queue_item_for_job(job_id, status="error",
                                           ended_at=time.time(),
                                           error=j["run"][step]["error"])
            else:
                _update_queue_item_for_job(job_id, status="pending",
                                           ended_at=time.time(), error=None)
        finally:
            with _LOCK:
                j["running"] = False
                j["current"] = None


def _all_thread(job_id: str, configs: dict):
    j = _job(job_id)
    with _RUN_LOCK:
        with _LOCK:
            j["running"] = True
        _update_queue_item_for_job(job_id, status="running", error=None,
                                   started_at=time.time(), ended_at=None)
        try:
            success = False
            for k in _STEP_KEYS:
                code = _run_one(job_id, k, configs.get(k, {}))
                if code != 0 or j["run"][k]["error"]:
                    break
            else:
                success = True
            if success and _artifact_done(job_id, "publish"):
                meta = _cleanup_success_cache(job_id)
                _update_queue_item_for_job(job_id, status="done",
                                           ended_at=time.time(),
                                           output_dir=meta.get("archive_dir", ""),
                                           title=meta.get("project_title") or meta.get("title", ""))
            else:
                failed = next((k for k in _STEP_KEYS if j["run"][k]["error"]), None)
                if failed:
                    _update_queue_item_for_job(job_id, status="error",
                                               ended_at=time.time(),
                                               error=j["run"][failed]["error"])
        finally:
            with _LOCK:
                j["running"] = False
                j["current"] = None


def _rerun_from_thread(job_id: str, start_step: str, configs: dict):
    j = _job(job_id)
    with _RUN_LOCK:
        with _LOCK:
            j["running"] = True
        _update_queue_item_for_job(job_id, status="running", error=None,
                                   started_at=time.time(), ended_at=None)
        try:
            _clear_from_step(job_id, start_step)
            start = _STEP_KEYS.index(start_step)
            success = False
            for k in _STEP_KEYS[start:]:
                code = _run_one(job_id, k, configs.get(k, {}))
                if code != 0 or j["run"][k]["error"]:
                    break
            else:
                success = True
            if success and _artifact_done(job_id, "publish"):
                meta = _cleanup_success_cache(job_id)
                _update_queue_item_for_job(job_id, status="done",
                                           ended_at=time.time(),
                                           output_dir=meta.get("archive_dir", ""),
                                           title=meta.get("project_title") or meta.get("title", ""))
            else:
                failed = next((k for k in _STEP_KEYS[start:] if j["run"][k]["error"]), None)
                if failed:
                    _update_queue_item_for_job(job_id, status="error",
                                               ended_at=time.time(),
                                               error=j["run"][failed]["error"])
        except Exception as e:  # noqa: BLE001
            _update_queue_item_for_job(job_id, status="error",
                                       ended_at=time.time(), error=str(e))
            with _LOCK:
                j["run"][start_step]["error"] = str(e)
        finally:
            with _LOCK:
                j["running"] = False
                j["current"] = None


# ------------------------------------------------------------------ 队列与时间估算
_URL_RE = re.compile(r"https?://[^\s，,]+")


def _parse_links(text: str) -> list[str]:
    found = _URL_RE.findall(text or "")
    if found:
        return list(dict.fromkeys(u.strip() for u in found if u.strip()))
    links = []
    for line in (text or "").splitlines():
        line = line.strip()
        if line:
            links.append(line)
    return list(dict.fromkeys(links))


def _probe_url_meta(url: str) -> dict:
    """快速探测标题/时长；失败不影响入队。"""
    try:
        proc = subprocess.run(
            [config.YT_DLP, "--dump-single-json", "--skip-download", "--no-warnings", url],
            cwd=str(config.BASE_DIR),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=45,
            creationflags=0 if os.name != "nt" else 0x08000000,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            data = json.loads(proc.stdout)
            return {
                "title": (data.get("title") or "").strip(),
                "duration_sec": float(data.get("duration") or 0) or None,
            }
    except Exception:
        pass
    return {"title": "", "duration_sec": None}


def _known_duration(job_id: str, fallback: float | None = None) -> float:
    wd = _work_path(job_id)
    for name in ("source.wav", "dub.wav"):
        path = wd / name
        if path.exists():
            dur = media_duration(path)
            if dur > 0:
                return dur
    return float(fallback or 600)


def _translated_stats(job_id: str) -> tuple[int, int]:
    data = load_json(_work_path(job_id) / "translated.json") or []
    if isinstance(data, dict):
        data = data.get("segments", [])
    chars = sum(len((s.get("zh") or "").strip()) for s in data if isinstance(s, dict))
    return chars, len(data)


def _sentence_count(job_id: str) -> int:
    wd = _work_path(job_id)
    for name in ("translated.json", "segments.json"):
        data = load_json(wd / name) or []
        if isinstance(data, dict):
            data = data.get("segments", [])
        if isinstance(data, list) and data:
            return len(data)
    return 0


def _actual_step_elapsed(job_id: str, key: str) -> int | None:
    run = _job(job_id)["run"].get(key, {})
    if run.get("started_at") and run.get("ended_at"):
        return round(run["ended_at"] - run["started_at"])
    return None


def _read_estimate_calibration() -> dict:
    data = load_json(ESTIMATE_CALIBRATION_FILE) or {}
    return data if isinstance(data, dict) else {}


def _write_estimate_calibration(data: dict) -> None:
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    for attempt in range(8):
        tmp = ESTIMATE_CALIBRATION_FILE.with_name(
            f"{ESTIMATE_CALIBRATION_FILE.name}.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex}.tmp"
        )
        try:
            tmp.write_text(payload, encoding="utf-8")
            os.replace(tmp, ESTIMATE_CALIBRATION_FILE)
            return
        except PermissionError:
            time.sleep(0.05 * (attempt + 1))
        finally:
            tmp.unlink(missing_ok=True)


def _calibrated_seconds_per_sentence() -> dict[str, float]:
    data = _read_estimate_calibration()
    steps = data.get("steps") if isinstance(data, dict) else {}
    rates = dict(_DEFAULT_SECONDS_PER_SENTENCE)
    if isinstance(steps, dict):
        for key, value in steps.items():
            try:
                rate = float((value or {}).get("seconds_per_sentence"))
            except (TypeError, ValueError):
                continue
            if rate > 0:
                rates[key] = rate
    return rates


def _update_estimate_calibration(job_id: str, completed_step: str) -> None:
    sentences = _sentence_count(job_id)
    if sentences <= 0:
        return
    data = _read_estimate_calibration()
    steps = data.get("steps") if isinstance(data.get("steps"), dict) else {}
    now = time.time()
    to_update = [completed_step]
    if completed_step == "asr":
        to_update.insert(0, "download")
    for key in to_update:
        elapsed = _actual_step_elapsed(job_id, key)
        if not elapsed or elapsed <= 0:
            continue
        steps[key] = {
            "seconds_per_sentence": round(elapsed / max(sentences, 1), 4),
            "sentences": sentences,
            "elapsed_sec": elapsed,
            "job_id": job_id,
            "updated_at": now,
        }
    data = {"version": 1, "updated_at": now, "steps": steps}
    _write_estimate_calibration(data)


def _step_estimate_seconds(duration: float, job_id: str | None = None) -> dict[str, int]:
    duration = max(float(duration or 0), 30.0)
    sentence_count = _sentence_count(job_id) if job_id else 0
    if sentence_count > 0:
        rates = _calibrated_seconds_per_sentence()
        return {
            key: round(max(_STEP_MIN_ESTIMATE[key], sentence_count * rates.get(key, _DEFAULT_SECONDS_PER_SENTENCE[key])))
            for key in _STEP_KEYS
        }
    # 第 2 步完成前还没有句子数，只能用视频时长粗估。
    return {
        "download": round(max(5, duration * 0.05)),
        "asr": round(max(5, duration * 0.08)),
        "translate": round(max(4, duration / 5 * _DEFAULT_SECONDS_PER_SENTENCE["translate"])),
        "tts": round(max(70, duration * 1.45)),
        "compose": round(max(5, duration * 0.05)),
        "publish": 4,
    }


def _estimate_job(job_id: str, fallback_duration: float | None = None) -> dict:
    duration = _known_duration(job_id, fallback_duration)
    steps = _step_estimate_seconds(duration, job_id)
    for key in list(steps):
        actual = _actual_step_elapsed(job_id, key)
        if actual is not None:
            steps[key] = actual
    remaining = 0
    state = _job(job_id)
    for key, eta in steps.items():
        if _artifact_done(job_id, key):
            continue
        run = state["run"].get(key, {})
        if state.get("current") == key and state.get("running"):
            progress = max(0, min(100, float(run.get("progress") or 0)))
            elapsed = time.time() - run["started_at"] if run.get("started_at") else 0
            remaining += round(max(eta * (1 - progress / 100), eta - elapsed, 10))
        else:
            remaining += eta
    return {"duration_sec": round(duration), "steps": steps,
            "total_sec": sum(steps.values()), "remaining_sec": remaining}


def _queue_item_status(item: dict) -> str:
    job_id = item["job_id"]
    j = _job(job_id)
    if item.get("status") in {"running", "error", "done"}:
        return item["status"]
    if any(_artifact_done(job_id, k) for k in _STEP_KEYS):
        if _artifact_done(job_id, "publish"):
            return "done"
        if j["running"]:
            return "running"
    return item.get("status", "pending")


def _queue_snapshot() -> dict:
    with _LOCK:
        raw = [dict(item) for item in _QUEUE]
        running = _QUEUE_RUNNING
        stop = _QUEUE_STOP

    items = []
    for item in raw:
        job_id = item["job_id"]
        meta = _remember_archive_meta(job_id)
        est = _estimate_job(job_id, item.get("duration_sec"))
        status = _queue_item_status(item)
        started = item.get("started_at")
        ended = item.get("ended_at")
        elapsed = (ended or time.time()) - started if started else 0
        items.append({
            **item,
            "status": status,
            "current_step": _job(job_id).get("current"),
            "estimate": est,
            "elapsed_sec": round(elapsed),
            "output_dir": meta.get("archive_dir") or item.get("output_dir", ""),
            "project_dir": meta.get("archive_dir") or item.get("project_dir", ""),
        })
    return {"running": running, "stop_requested": stop, "items": items}


def _run_queue_thread(configs: dict):
    global _QUEUE_RUNNING, _QUEUE_STOP
    with _RUN_LOCK:
        with _LOCK:
            _QUEUE_RUNNING = True
            _QUEUE_STOP = False
        try:
            while True:
                with _LOCK:
                    if _QUEUE_STOP:
                        break
                    item = next((x for x in _QUEUE if x.get("status") in {"pending", "error"}), None)
                    if not item:
                        break
                    item["status"] = "running"
                    item["started_at"] = item.get("started_at") or time.time()
                    item["ended_at"] = None
                    item["error"] = None
                    _save_queue_state_unlocked()
                job_id = item["job_id"]
                j = _job(job_id)
                with _LOCK:
                    j["running"] = True
                try:
                    for key in _STEP_KEYS:
                        if _artifact_done(job_id, key):
                            continue
                        cfg = dict(configs.get(key, {}))
                        if key == "download":
                            cfg["url"] = item["url"]
                        code = _run_one(job_id, key, cfg)
                        if code != 0 or j["run"][key]["error"]:
                            raise RuntimeError(j["run"][key]["error"] or f"{key} 失败")
                    meta = _cleanup_success_cache(job_id)
                    with _LOCK:
                        item["status"] = "done"
                        item["ended_at"] = time.time()
                        item["output_dir"] = meta.get("archive_dir", "")
                        item["project_dir"] = meta.get("archive_dir", "")
                        item["title"] = meta.get("project_title") or meta.get("title") or item.get("title", "")
                        _save_queue_state_unlocked()
                except Exception as e:  # noqa: BLE001
                    with _LOCK:
                        item["status"] = "error"
                        item["ended_at"] = time.time()
                        item["error"] = str(e)
                        _save_queue_state_unlocked()
                finally:
                    with _LOCK:
                        j["running"] = False
                        j["current"] = None
        finally:
            with _LOCK:
                _QUEUE_RUNNING = False
                _QUEUE_STOP = False


def _probe_queue_item_thread(item_id: str, url: str):
    meta = _probe_url_meta(url)
    if not meta.get("title") and not meta.get("duration_sec"):
        return
    with _LOCK:
        for item in _QUEUE:
            if item["id"] == item_id:
                if meta.get("title"):
                    item["title"] = meta["title"]
                if meta.get("duration_sec"):
                    item["duration_sec"] = meta["duration_sec"]
                _save_queue_state_unlocked()
                break


def _open_path(path: Path) -> None:
    if not path.exists():
        raise HTTPException(404, "路径不存在")
    if os.name == "nt":
        os.startfile(str(path))  # type: ignore[attr-defined]
    else:
        subprocess.Popen(["xdg-open", str(path)])


_load_queue_state()


# ------------------------------------------------------------------ 路由
@app.get("/", response_class=HTMLResponse)
def index():
    return HTMLResponse(
        (WEB_DIR / "index.html").read_text(encoding="utf-8"),
        headers={"Cache-Control": "no-store, max-age=0"},
    )


@app.get("/api/config")
def get_config():
    from src import tts
    tts_engines = tts.engines_info()
    cur_engine = config.TTS_ENGINE if any(e["key"] == config.TTS_ENGINE for e in tts_engines) else tts.DEFAULT_ENGINE
    return {
        "steps": STEP_DEFS,
        "tts_engines": tts_engines,
        "tts_engine_default": cur_engine,
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


@app.get("/api/queue")
def get_queue():
    return _queue_snapshot()


@app.post("/api/queue/items")
def add_queue_items(payload: dict = Body(...)):
    links = _parse_links(str(payload.get("links") or payload.get("url") or ""))
    if not links:
        raise HTTPException(400, "没有识别到视频链接")
    created = []
    with _LOCK:
        base_order = len(_QUEUE)
        for offset, url in enumerate(links):
            job_id = "job_" + uuid.uuid4().hex[:10]
            item_id = "qi_" + uuid.uuid4().hex[:10]
            project_dir = _next_project_dir("待处理视频")
            _register_job_project(job_id, project_dir)
            work_dir_of(job_id)
            item = {
                "id": item_id,
                "job_id": job_id,
                "url": url,
                "title": "",
                "status": "pending",
                "error": None,
                "order": base_order + offset,
                "duration_sec": None,
                "output_dir": str(project_dir),
                "project_dir": str(project_dir),
                "created_at": time.time(),
                "started_at": None,
                "ended_at": None,
            }
            _QUEUE.append(item)
            created.append(dict(item))
        _save_queue_state_unlocked()
    for item in created:
        threading.Thread(target=_probe_queue_item_thread,
                         args=(item["id"], item["url"]), daemon=True).start()
    return {"items": created}


@app.delete("/api/queue/items/{item_id}")
def delete_queue_item(item_id: str):
    with _LOCK:
        item = next((dict(x) for x in _QUEUE if x["id"] == item_id), None)
        if not item:
            raise HTTPException(404, "队列项不存在")
        if item.get("status") == "running":
            raise HTTPException(409, "正在执行的任务不能删除")
    _delete_job_files(item)
    with _LOCK:
        _QUEUE[:] = [x for x in _QUEUE if x["id"] != item_id]
        for i, x in enumerate(_QUEUE):
            x["order"] = i
        _save_queue_state_unlocked()
    return {"ok": True}


@app.post("/api/queue/reorder")
def reorder_queue(payload: dict = Body(...)):
    ids = list(payload.get("ids") or [])
    with _LOCK:
        by_id = {x["id"]: x for x in _QUEUE}
        if set(ids) != set(by_id):
            raise HTTPException(400, "排序列表与当前队列不匹配")
        _QUEUE[:] = [by_id[item_id] for item_id in ids]
        for i, item in enumerate(_QUEUE):
            item["order"] = i
        _save_queue_state_unlocked()
    return {"ok": True}


@app.post("/api/queue/run")
def run_queue(payload: dict = Body(default={})):
    global _QUEUE_RUNNING
    with _LOCK:
        if _QUEUE_RUNNING:
            raise HTTPException(409, "队列正在执行")
        if not any(x.get("status") in {"pending", "error"} for x in _QUEUE):
            raise HTTPException(400, "没有待执行任务")
        _QUEUE_RUNNING = True
    configs = payload.get("configs") or {}
    threading.Thread(target=_run_queue_thread, args=(configs,), daemon=True).start()
    return {"ok": True}


@app.post("/api/queue/stop")
def stop_queue():
    global _QUEUE_STOP
    with _LOCK:
        _QUEUE_STOP = True
    return {"ok": True}


@app.get("/api/queue/stream")
async def queue_stream():
    async def gen():
        last = None
        while True:
            snap = json.dumps(_queue_snapshot(), ensure_ascii=False)
            if snap != last:
                yield f"data: {snap}\n\n"
                last = snap
            await asyncio.sleep(0.8)
    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache"})


@app.post("/api/queue/items/{item_id}/open-output")
def open_queue_output(item_id: str):
    with _LOCK:
        item = next((dict(x) for x in _QUEUE if x["id"] == item_id), None)
    if not item:
        raise HTTPException(404, "队列项不存在")
    meta = _remember_archive_meta(item["job_id"])
    out_dir = Path(meta.get("archive_dir") or item.get("output_dir") or "")
    if not str(out_dir):
        out_dir = _work_path(item["job_id"])
    _open_path(out_dir)
    return {"ok": True, "path": str(out_dir)}


@app.post("/api/jobs/{job_id}/open-output")
def open_job_output(job_id: str):
    meta = _remember_archive_meta(job_id)
    out_dir = Path(meta.get("archive_dir") or _work_path(job_id))
    _open_path(out_dir)
    return {"ok": True, "path": str(out_dir)}


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
                  file: UploadFile | None = File(default=None),
                  cover_file: UploadFile | None = File(default=None)):
    if _job(job_id)["running"]:
        raise HTTPException(409, "已有步骤在运行")
    try:
        configs = json.loads(configs_json or "{}")
    except json.JSONDecodeError:
        configs = {}
    if file is not None and file.filename:
        configs.setdefault("download", {})["file"] = _save_upload(job_id, file)
    if cover_file is not None and cover_file.filename:
        configs.setdefault("publish", {})["file"] = _save_upload(job_id + ".cover", cover_file)
    threading.Thread(target=_all_thread, args=(job_id, configs), daemon=True).start()
    return {"ok": True}


@app.post("/api/jobs/{job_id}/rerun_from")
def rerun_from(job_id: str, payload: dict = Body(...)):
    if _job(job_id)["running"]:
        raise HTTPException(409, "已有步骤在运行")
    step = str(payload.get("step") or "")
    if step not in _STEP_KEYS:
        raise HTTPException(400, "未知步骤")
    configs = payload.get("configs") or {}
    if not isinstance(configs, dict):
        configs = {}
    if step == "download":
        qi = next((x for x in _QUEUE if x.get("job_id") == job_id), None)
        if qi and qi.get("url"):
            configs.setdefault("download", {})["url"] = qi["url"]
        if not configs.get("download", {}).get("url"):
            raise HTTPException(400, "从第 1 步重跑需要视频链接")
    threading.Thread(target=_rerun_from_thread,
                     args=(job_id, step, configs), daemon=True).start()
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
    data = load_json(_work_path(job_id) / "segments.json")
    segs = data["segments"] if isinstance(data, dict) else (data or [])
    return {"segments": segs}


@app.get("/api/jobs/{job_id}/translation")
def translation(job_id: str):
    return {"segments": load_json(_work_path(job_id) / "translated.json") or []}


@app.get("/api/jobs/{job_id}/cues")
def cues(job_id: str):
    from src.subtitles import cue_list
    segs = load_json(_work_path(job_id) / "dub_segments.json") \
        or load_json(_work_path(job_id) / "translated.json") or []
    return {"cues": cue_list(segs)}


@app.get("/api/jobs/{job_id}/metadata")
def metadata(job_id: str):
    return _remember_archive_meta(job_id)


# --------- 文件 ---------
def _serve(path: Path, media: str, download: bool = False):
    if not path.exists():
        raise HTTPException(404, "文件不存在")
    return FileResponse(path, media_type=media, filename=path.name if download else None)


@app.get("/api/jobs/{job_id}/dub.wav")
def dub(job_id: str):
    return _serve(_work_path(job_id) / "dub.wav", "audio/wav")


@app.get("/api/jobs/{job_id}/video.mp4")
def video(job_id: str, download: bool = False):
    meta = _remember_archive_meta(job_id)
    archived = _archive_file(meta, "video", "video.mp4")
    return _serve(archived or (_work_path(job_id) / "final.mp4"), "video/mp4", download)


@app.get("/api/jobs/{job_id}/cover.png")
def cover(job_id: str):
    meta = _remember_archive_meta(job_id)
    archived = _archive_file(meta, "cover", "cover.png")
    return _serve(archived or (_work_path(job_id) / "cover.png"), "image/png")


@app.get("/api/jobs/{job_id}/source.mp4")
def source(job_id: str):
    return _serve(_work_path(job_id) / "source.mp4", "video/mp4")


@app.get("/api/jobs/{job_id}/sub")
def sub(job_id: str, fmt: str = "srt"):
    ext = {"ass": "subs.ass", "srt": "subs.srt", "vtt": "subs.vtt"}.get(fmt, "subs.srt")
    return _serve(_work_path(job_id) / ext, "text/plain", download=True)


_SAMPLE_DIR = config.WORK_DIR / "_samples"
_SAMPLE_DIR.mkdir(parents=True, exist_ok=True)
for _stale_spec in _SAMPLE_DIR.glob("_spec_*.json"):
    _stale_spec.unlink(missing_ok=True)


# 固定试听文案：试听与"准备全部"用同一句，保证缓存可复用
_SAMPLE_TEXT = "我们从小被教育，爱是神圣的。但真正困住我们的，往往不是生活本身，而是对结果的执念。"
_PREPARE: dict[str, dict] = {}
_PREPARE_LOCK = threading.Lock()


def _sample_path(engine: str, voice: str) -> Path:
    # 试听音频只按 引擎+音色 缓存；前端用 playbackRate 做倍速试听。
    key = uuid.uuid5(uuid.NAMESPACE_DNS, f"{engine}:{voice}").hex[:12]
    return _SAMPLE_DIR / f"{key}.wav"


def _legacy_sample_paths(engine: str, voice: str) -> list[Path]:
    # 兼容旧版本缓存：曾经按 引擎+音色+语速 缓存，也曾经只按音色缓存。
    speeds = [
        "1.00", f"{float(config.TTS_SPEED):.2f}", "0.75", "0.85", "0.90", "0.95",
        "1.05", "1.10", "1.15", "1.20", "1.25", "1.30", "1.40", "1.50",
    ]
    seen: set[str] = set()
    paths: list[Path] = []
    for speed in speeds:
        token = f"{engine}:{voice}:{speed}"
        if token in seen:
            continue
        seen.add(token)
        paths.append(_SAMPLE_DIR / f"{uuid.uuid5(uuid.NAMESPACE_DNS, token).hex[:12]}.wav")
    paths.append(_SAMPLE_DIR / f"{uuid.uuid5(uuid.NAMESPACE_DNS, voice).hex[:10]}.wav")
    return paths


def _existing_sample_path(engine: str, voice: str) -> Path | None:
    canonical = _sample_path(engine, voice)
    if canonical.exists():
        return canonical
    for legacy in _legacy_sample_paths(engine, voice):
        if not legacy.exists():
            continue
        try:
            shutil.copy2(legacy, canonical)
            return canonical
        except OSError:
            return legacy
    return None


def _engine_voices(engine: str) -> list[str]:
    from src import tts
    info = next((e for e in tts.engines_info() if e["key"] == engine), None)
    return list(info["voices"]) if info else []


def _available_sample_engines() -> list[dict]:
    from src import tts
    return [
        e for e in tts.engines_info()
        if e.get("available") and e.get("voices")
    ]


@app.post("/api/voice-sample")
def voice_sample(voice: str = Form(...), engine: str = Form(""), speed: float = Form(1.0)):
    out = _existing_sample_path(engine, voice) or _sample_path(engine, voice)
    if not out.exists():
        with _RUN_LOCK:
            if not out.exists():
                cmd = [sys.executable, "-u", str(config.BASE_DIR / "voice_sample.py"),
                       "--engine", engine, "--voice", voice, "--speed", "1.0",
                       "--text", _SAMPLE_TEXT, "--out", str(out)]
                r = subprocess.run(cmd, cwd=str(config.BASE_DIR), capture_output=True, text=True)
                if r.returncode != 0 or not out.exists():
                    raise HTTPException(500, "试听合成失败")
    return _serve(out, "audio/wav")


def _prepare_worker(engine: str, jobs: list[dict], pk: str):
    spec = _SAMPLE_DIR / f"_spec_{uuid.uuid4().hex[:8]}.json"
    spec.write_text(json.dumps(jobs, ensure_ascii=False), encoding="utf-8")
    try:
        with _RUN_LOCK:
            cmd = [sys.executable, "-u", str(config.BASE_DIR / "voice_sample.py"),
                   "--engine", engine, "--speed", "1.0",
                   "--text", _SAMPLE_TEXT, "--spec", str(spec)]
            subprocess.run(cmd, cwd=str(config.BASE_DIR), capture_output=True, text=True)
    finally:
        with _PREPARE_LOCK:
            _PREPARE.setdefault(pk, {})["running"] = False
        spec.unlink(missing_ok=True)


@app.post("/api/voice-sample/prepare")
def prepare_all(payload: dict = Body(default={})):
    engine = (payload.get("engine") or "").strip()
    engines = [e for e in _available_sample_engines() if not engine or e["key"] == engine]
    if not engines:
        raise HTTPException(400, "该引擎无可用音色")
    total = 0
    running = False
    for info in engines:
        key = info["key"]
        voices = list(info.get("voices") or [])
        total += len(voices)
        jobs = [{"voice": v, "out": str(_sample_path(key, v))}
                for v in voices if _existing_sample_path(key, v) is None]
        with _PREPARE_LOCK:
            if _PREPARE.get(key, {}).get("running"):
                running = True
                continue
            _PREPARE[key] = {"running": bool(jobs)}
        if jobs:
            running = True
            threading.Thread(target=_prepare_worker, args=(key, jobs, key), daemon=True).start()
    return {"total": total, "running": running}


@app.get("/api/voice-sample/prepare-status")
def prepare_status(engine: str = ""):
    engine = (engine or "").strip()
    engines = [e for e in _available_sample_engines() if not engine or e["key"] == engine]
    total = 0
    done = 0
    ready: list[str] = []
    with _PREPARE_LOCK:
        running = any(bool(_PREPARE.get(e["key"], {}).get("running")) for e in engines)
    for info in engines:
        key = info["key"]
        voices = list(info.get("voices") or [])
        total += len(voices)
        current = [v for v in voices if _existing_sample_path(key, v) is not None]
        done += len(current)
        if engine:
            ready = current
    return {"total": total, "done": done, "ready": ready, "running": running}


if __name__ == "__main__":
    import uvicorn
    print("打开 http://127.0.0.1:8800")
    uvicorn.run(app, host="127.0.0.1", port=8800)
