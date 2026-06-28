"""集中配置：复用 114 听书软件的 venv、本地 ffmpeg、DeepSeek 翻译等。

设计原则：
- 不修改 114 项目的环境，只复用它的 .venv（torch+cu128 / faster-whisper / f5-tts）。
- ffmpeg 用项目内自带的静态构建（tools/），并在导入时塞进 PATH，
  这样 yt-dlp / torchcodec / f5-tts 都能找到它。
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

# ---------------------------------------------------------------- 路径
BASE_DIR = Path(__file__).resolve().parent.parent
TOOLS_DIR = BASE_DIR / "tools"
WORK_DIR = BASE_DIR / "work"          # 每个视频一个子目录，存中间产物（可断点续跑）
OUTPUT_DIR = BASE_DIR / "output"      # 最终 mp4
WORK_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)

# ---------------------------------------------------------------- 模型存放在项目内
# 把 HuggingFace / torch 的缓存指到项目目录，避免塞满 C 盘。
# 必须在任何 huggingface_hub / torch 导入之前设置这些环境变量。
MODELS_DIR = BASE_DIR / "models"
_HF_HOME = MODELS_DIR / "huggingface"
(_HF_HOME / "hub").mkdir(parents=True, exist_ok=True)
os.environ["HF_HOME"] = str(_HF_HOME)
os.environ["HF_HUB_CACHE"] = str(_HF_HOME / "hub")
os.environ["TORCH_HOME"] = str(MODELS_DIR / "torch")
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

# 复用 114 听书软件的虚拟环境（Python 3.11 + CUDA 12.8）
REUSE_VENV = Path(r"F:\我的编程项目\114_听书软件\.venv")
REUSE_VENV_PY = REUSE_VENV / "Scripts" / "python.exe"

# ---------------------------------------------------------------- ffmpeg
def _find_ffmpeg() -> tuple[str, str]:
    """返回 (ffmpeg, ffprobe) 可执行路径，并把所在目录加进 PATH。"""
    # 1) 项目内静态构建
    for cand in TOOLS_DIR.rglob("ffmpeg.exe"):
        bin_dir = cand.parent
        os.environ["PATH"] = str(bin_dir) + os.pathsep + os.environ.get("PATH", "")
        return str(cand), str(bin_dir / "ffprobe.exe")
    # 2) 系统 PATH
    sys_ff = shutil.which("ffmpeg")
    if sys_ff:
        return sys_ff, shutil.which("ffprobe") or "ffprobe"
    raise RuntimeError("找不到 ffmpeg，请把静态构建放到 tools/ 下，或安装到系统 PATH。")


FFMPEG, FFPROBE = _find_ffmpeg()


def _find_yt_dlp() -> str:
    exe = shutil.which("yt-dlp")
    if exe:
        return exe
    for cand in (
        Path(r"C:\Python313\Scripts\yt-dlp.exe"),
        Path(r"C:\Python314\Scripts\yt-dlp.exe"),
    ):
        if cand.exists():
            return str(cand)
    return "yt-dlp"  # 交给 PATH 兜底


YT_DLP = _find_yt_dlp()

# ---------------------------------------------------------------- .env
def _load_env() -> None:
    env_path = BASE_DIR / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        key, val = key.strip(), val.strip().strip('"').strip("'")
        os.environ.setdefault(key, val)


_load_env()

# ---------------------------------------------------------------- DeepSeek 翻译
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")

# ---------------------------------------------------------------- ASR
# faster-whisper-small 已离线缓存；large-v3-turbo 质量更好但首次需联网下载 CT2 权重。
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "small")
WHISPER_DEVICE = os.environ.get("WHISPER_DEVICE", "cuda")
WHISPER_COMPUTE_TYPE = os.environ.get("WHISPER_COMPUTE_TYPE", "float16")

# ---------------------------------------------------------------- TTS
TTS_VOICE = os.environ.get("TTS_VOICE", "温柔女声")  # 见 tts.py 内置音色
# 段间停顿（秒）：顺序拼接配音时，每段之间保留的停顿，参考原间隔但限制在此区间。
TTS_GAP_MIN = float(os.environ.get("TTS_GAP_MIN", "0.08"))
TTS_GAP_MAX = float(os.environ.get("TTS_GAP_MAX", "0.45"))

# ---------------------------------------------------------------- 字幕样式
SUB_FONT = os.environ.get("SUB_FONT", "Microsoft YaHei")
SUB_FONTSIZE = int(os.environ.get("SUB_FONTSIZE", "54"))  # 字号按 1080p 画布计，libass 自动随分辨率缩放
