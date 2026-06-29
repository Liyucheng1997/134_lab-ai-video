"""集中配置：本项目环境、本地 ffmpeg、DeepSeek 翻译等。

设计原则：
- Python、Qwen3-TTS、faster-whisper 都放在当前项目 tools/qwen3-tts-env。
- ffmpeg 用项目内兼容当前 NVIDIA 驱动的静态构建，并在导入时塞进 PATH。
"""
from __future__ import annotations

import os
import re
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

# 本项目自己的 Python 环境（从 Qwen3-TTS 可用环境克隆，含 CUDA torch / Qwen3-TTS）。
PROJECT_VENV = TOOLS_DIR / "qwen3-tts-env"
PROJECT_VENV_PY = PROJECT_VENV / "python.exe"
# 兼容旧变量名，避免外部脚本还引用 REUSE_VENV。
REUSE_VENV = PROJECT_VENV
REUSE_VENV_PY = PROJECT_VENV_PY
if PROJECT_VENV.exists():
    _path_parts = [
        str(PROJECT_VENV),
        str(PROJECT_VENV / "Scripts"),
        str(PROJECT_VENV / "Library" / "bin"),
    ]
    _nvidia_root = PROJECT_VENV / "Lib" / "site-packages" / "nvidia"
    if _nvidia_root.exists():
        _path_parts.extend(str(p) for p in _nvidia_root.glob("*\\bin") if p.exists())
    os.environ["PATH"] = os.pathsep.join(_path_parts + [os.environ.get("PATH", "")])

# ---------------------------------------------------------------- ffmpeg
def _find_ffmpeg() -> tuple[str, str]:
    """返回 (ffmpeg, ffprobe) 可执行路径，并把所在目录加进 PATH。"""
    # 1) 优先使用当前项目内已验证兼容本机驱动的 ffmpeg。
    preferred = TOOLS_DIR / "ffmpeg-nvenc-compatible" / "bin" / "ffmpeg.exe"
    candidates = [preferred] if preferred.exists() else []
    # 2) 项目内其它静态构建
    candidates.extend(c for c in TOOLS_DIR.rglob("ffmpeg.exe") if c != preferred)
    for cand in candidates:
        bin_dir = cand.parent
        os.environ["PATH"] = str(bin_dir) + os.pathsep + os.environ.get("PATH", "")
        return str(cand), str(bin_dir / "ffprobe.exe")
    # 3) 系统 PATH
    sys_ff = shutil.which("ffmpeg")
    if sys_ff:
        return sys_ff, shutil.which("ffprobe") or "ffprobe"
    raise RuntimeError("找不到 ffmpeg，请把静态构建放到 tools/ 下，或安装到系统 PATH。")


FFMPEG, FFPROBE = _find_ffmpeg()


def _find_yt_dlp() -> str:
    project_ytdlp = PROJECT_VENV / "Scripts" / "yt-dlp.exe"
    if project_ytdlp.exists():
        return str(project_ytdlp)
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
        key = key.strip()
        val = val.strip()
        # 去掉行内注释（仅当未被引号包裹且 # 前有空白时，避免误删值里的 #）
        if val[:1] not in ("'", '"'):
            m = re.search(r"\s#", val)
            if m:
                val = val[:m.start()].strip()
        val = val.strip('"').strip("'")
        # 用 .env 覆盖已存在的系统环境变量：项目 .env 为准，避免旧的系统变量盖住新配置
        os.environ[key] = val


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
TTS_ENGINE = os.environ.get("TTS_ENGINE", "qwen3")
TTS_VOICE = os.environ.get("TTS_VOICE", "ryan")
TTS_SPEED = float(os.environ.get("TTS_SPEED", "1.15"))  # 配音语速，0.7~1.3 微调
# 段间停顿（秒）：顺序拼接配音时，每段之间保留的停顿（默认即可，无需在界面调）。
TTS_GAP_MIN = float(os.environ.get("TTS_GAP_MIN", "0.08"))
TTS_GAP_MAX = float(os.environ.get("TTS_GAP_MAX", "0.45"))

# Qwen3-TTS：默认使用 CustomVoice 的 ryan，模型和缓存都放在当前项目内。
QWEN_TTS_MODEL = os.environ.get("QWEN_TTS_MODEL", "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice")
QWEN_TTS_MODE = os.environ.get("QWEN_TTS_MODE", "custom-voice")
QWEN_TTS_LANGUAGE = os.environ.get("QWEN_TTS_LANGUAGE", "Chinese")
QWEN_TTS_INSTRUCT = os.environ.get(
    "QWEN_TTS_INSTRUCT",
    "适合中文视频口播，声音自然、清晰、可信，节奏稳定但不拖沓。",
)
QWEN_TTS_DEVICE = os.environ.get("QWEN_TTS_DEVICE", "cuda:0")
QWEN_TTS_DTYPE = os.environ.get("QWEN_TTS_DTYPE", "auto")
QWEN_TTS_ATTENTION = os.environ.get("QWEN_TTS_ATTENTION", "sdpa")
QWEN_TTS_CACHE_DIR = Path(os.environ.get("QWEN_TTS_CACHE_DIR", str(MODELS_DIR / "qwen3-tts")))
QWEN_TTS_MAX_CHARS = int(os.environ.get("QWEN_TTS_MAX_CHARS", "280"))
QWEN_TTS_BATCH_SIZE = int(os.environ.get("QWEN_TTS_BATCH_SIZE", "2"))

# F5-TTS（本地·音色克隆）：使用 f5_tts 自带参考片段；也可自定义参考音频做声音克隆。
F5_REF_AUDIO = os.environ.get("TTS_REF_AUDIO", "")   # 自定义参考音频（wav/flac），留空用内置音色
F5_REF_TEXT = os.environ.get("TTS_REF_TEXT", "")     # 自定义参考音频对应的文字

# Azure TTS（云端·神经网络语音，REST V1）：需在 .env 填 KEY 与 REGION。
AZURE_SPEECH_KEY = os.environ.get("AZURE_SPEECH_KEY", "")
AZURE_SPEECH_REGION = os.environ.get("AZURE_SPEECH_REGION", "eastus")
# 朗读风格（mstts express-as style），留空为默认；荣格/疗愈类可用 calm / narration-relaxed / gentle。
AZURE_TTS_STYLE = os.environ.get("AZURE_TTS_STYLE", "")
AZURE_TTS_STYLE_DEGREE = os.environ.get("AZURE_TTS_STYLE_DEGREE", "1")

# CosyVoice2（阿里·开源本地）：需安装 CosyVoice 仓库并下载 CosyVoice2-0.5B 模型。
# 暂搁置：当前共享环境 transformers 版本下 Qwen2 LM 会过度生成（停不下来），输出不稳定。
# 待用独立 venv（transformers==4.51.3）修复后，设 COSYVOICE_ENABLED=1 启用。
COSYVOICE_ENABLED = os.environ.get("COSYVOICE_ENABLED", "0") == "1"
COSYVOICE_REPO_DIR = os.environ.get("COSYVOICE_REPO_DIR", str(TOOLS_DIR / "CosyVoice"))
COSYVOICE_MODEL_DIR = os.environ.get(
    "COSYVOICE_MODEL_DIR", str(MODELS_DIR / "cosyvoice" / "CosyVoice2-0.5B"))
# 参考音频（zero-shot 音色克隆）；留空则用 F5 内置参考片段兜底。
COSYVOICE_REF_AUDIO = os.environ.get("COSYVOICE_REF_AUDIO", "")
COSYVOICE_REF_TEXT = os.environ.get("COSYVOICE_REF_TEXT", "")
# 情感/风格指令（instruct2），如「用温暖沉稳、富有疗愈感的语气朗读」；留空走 zero-shot。
COSYVOICE_INSTRUCT = os.environ.get(
    "COSYVOICE_INSTRUCT",
    "用温暖、沉稳、富有疗愈感的语气，像一位睿智的引路人在娓娓道来。",
)

# Gemini TTS（云端·可控情感，REST generateContent）：需在 .env 填 GEMINI_API_KEY。
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", os.environ.get("GOOGLE_API_KEY", ""))
# 接入地址：国内直连官方域名常被墙/拒，可填你的反代/中转地址（到 /v1beta 上一级即可）。
GEMINI_BASE_URL = os.environ.get(
    "GEMINI_BASE_URL", "https://generativelanguage.googleapis.com").rstrip("/")
GEMINI_TTS_MODEL = os.environ.get("GEMINI_TTS_MODEL", "gemini-2.5-pro-preview-tts")
# 自然语言风格指令（会拼到正文前引导语气）；留空则不加引导。
GEMINI_TTS_STYLE = os.environ.get(
    "GEMINI_TTS_STYLE", "用温暖、沉稳、富有疗愈感的语气朗读")
# 自动语气：用 DeepSeek 读全文后，按 Gemini 官方风格提示生成贴合内容的语气/情感指令（更真实）。
GEMINI_TTS_AUTO_STYLE = os.environ.get("GEMINI_TTS_AUTO_STYLE", "1") == "1"
# 计费估算（美元/百万 token）：用于在日志里估算花费。官方价可能调整，可用 .env 覆盖。
GEMINI_PRICE_IN = float(os.environ.get("GEMINI_PRICE_IN", "0"))    # 0=按模型默认表
GEMINI_PRICE_OUT = float(os.environ.get("GEMINI_PRICE_OUT", "0"))
USD_TO_CNY = float(os.environ.get("USD_TO_CNY", "7.2"))

# ---------------------------------------------------------------- 字幕样式
SUB_FONT = os.environ.get("SUB_FONT", "Microsoft YaHei")
SUB_FONTSIZE = int(os.environ.get("SUB_FONTSIZE", "54"))  # 字号按 1080p 画布计，libass 自动随分辨率缩放
SUB_PRIMARY = os.environ.get("SUB_PRIMARY", "#FFFFFF")    # 字体颜色
SUB_OUTLINE = os.environ.get("SUB_OUTLINE", "#000000")    # 描边颜色
SUB_POSITION = os.environ.get("SUB_POSITION", "bottom")   # bottom | middle | top
SUB_BOLD = os.environ.get("SUB_BOLD", "1")               # 是否加粗

# 字幕样式预设（面向 灵性 / 情感 / 荣格心理学 赛道）：用户只选其一，无需逐项调。
# 字号偏大、清晰；颜色温暖/沉静，描边保证任何画面上都看得清。
SUB_PRESETS = [
    {"key": "classic", "name": "经典白字", "font": "Microsoft YaHei", "fontsize": 70,
     "primary": "#FFFFFF", "outline": "#000000", "position": "bottom", "bold": "1"},
    {"key": "cream", "name": "温暖米黄", "font": "KaiTi", "fontsize": 74,
     "primary": "#FFF1D0", "outline": "#3A2410", "position": "bottom", "bold": "1"},
    {"key": "gold", "name": "治愈淡金", "font": "Microsoft YaHei", "fontsize": 70,
     "primary": "#FFD98A", "outline": "#241A0A", "position": "bottom", "bold": "1"},
    {"key": "serene", "name": "静谧青蓝", "font": "Microsoft YaHei", "fontsize": 70,
     "primary": "#D6ECFF", "outline": "#0E2440", "position": "bottom", "bold": "1"},
    {"key": "jung", "name": "荣格深邃", "font": "SimSun", "fontsize": 72,
     "primary": "#F2ECDD", "outline": "#1C1430", "position": "bottom", "bold": "1"},
]
SUB_PRESET = os.environ.get("SUB_PRESET", "classic")

# ---------------------------------------------------------------- B 站自动投稿
# 通过 bili_login.py 扫码生成，避免在项目里保存账号密码。
BILIBILI_COOKIE_FILE = os.environ.get("BILIBILI_COOKIE_FILE", str(WORK_DIR / "bilibili.cookie.json"))
# 默认投到「知识 / 社科·法律·心理」，更适合哲学、心理、观点类内容；也可在第 6 步覆盖。
BILIBILI_TID = int(os.environ.get("BILIBILI_TID", "228"))
BILIBILI_COPYRIGHT = int(os.environ.get("BILIBILI_COPYRIGHT", "1"))  # 1 自制；2 转载
BILIBILI_UPLOAD_LINE = os.environ.get("BILIBILI_UPLOAD_LINE", "bda2")
BILIBILI_UPLOAD_FALLBACK_LINES = [
    x.strip() for x in os.environ.get("BILIBILI_UPLOAD_FALLBACK_LINES", "bda2,bda,tx").split(",")
    if x.strip()
]
BILIBILI_UPLOAD_THREADS = int(os.environ.get("BILIBILI_UPLOAD_THREADS", "3"))
BILIBILI_UPLOAD_TIMEOUT = int(os.environ.get("BILIBILI_UPLOAD_TIMEOUT", "60"))
