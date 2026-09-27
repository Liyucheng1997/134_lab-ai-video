"""集中配置：本项目环境、本地 ffmpeg、DeepSeek 翻译等。

设计原则：
- Python、F5-TTS、faster-whisper 都放在当前项目 tools/f5-tts-env。
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
DEFAULT_COVER_IMAGE = Path(os.environ.get(
    "DEFAULT_COVER_IMAGE",
    str(BASE_DIR / "pictures" / "01_荣格心理学.png"),
))
DEFAULT_COVER_TITLE_X = float(os.environ.get("DEFAULT_COVER_TITLE_X", "0.47"))
DEFAULT_COVER_TITLE_Y = float(os.environ.get("DEFAULT_COVER_TITLE_Y", "0.10"))
DEFAULT_COVER_TITLE_WIDTH = float(os.environ.get("DEFAULT_COVER_TITLE_WIDTH", "0.53"))
DEFAULT_COVER_TITLE_FONT_SIZE = int(os.environ.get("DEFAULT_COVER_TITLE_FONT_SIZE", "112"))

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

# 本项目自己的 Python 环境（含 CUDA torch / F5-TTS / faster-whisper）。
PROJECT_VENV = TOOLS_DIR / "f5-tts-env"
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


def _find_yt_dlp() -> list[str]:
    """返回调用 yt-dlp 的命令前缀（list 形式，使用处用 *config.YT_DLP 展开）。

    优先用项目环境的 `python.exe -m yt_dlp`：不依赖 Scripts/yt-dlp.exe 那个
    内嵌绝对路径的包装器（环境改名后包装器会失效），更稳。
    """
    if PROJECT_VENV_PY.exists():
        return [str(PROJECT_VENV_PY), "-m", "yt_dlp"]
    exe = shutil.which("yt-dlp")
    if exe:
        return [exe]
    for cand in (
        Path(r"C:\Python313\Scripts\yt-dlp.exe"),
        Path(r"C:\Python314\Scripts\yt-dlp.exe"),
    ):
        if cand.exists():
            return [str(cand)]
    return ["yt-dlp"]  # 交给 PATH 兜底


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

# ---------------------------------------------------------------- yt-dlp 反机器人
# YouTube 触发“Sign in to confirm you're not a bot”时需要带上已登录 cookie。
# 两种方式二选一，cookie 文件优先（浏览器新版加密常导致直接读取失败）。
YTDLP_COOKIE_FILE = os.environ.get("YTDLP_COOKIE_FILE", "").strip()
YTDLP_COOKIES_FROM_BROWSER = os.environ.get("YTDLP_COOKIES_FROM_BROWSER", "").strip()


def ytdlp_cookie_args() -> list[str]:
    """返回 yt-dlp 的 cookie 参数；未配置则为空。"""
    if YTDLP_COOKIE_FILE and Path(YTDLP_COOKIE_FILE).is_file():
        return ["--cookies", YTDLP_COOKIE_FILE]
    if YTDLP_COOKIES_FROM_BROWSER:
        return ["--cookies-from-browser", YTDLP_COOKIES_FROM_BROWSER]
    return []


# YouTube 的 n challenge 需要 JS 运行时 + yt-dlp 官方 EJS 解算脚本，
# 否则只能拿到图片流（报错 Requested format is not available）。
def _find_js_runtime() -> str:
    configured = os.environ.get("YTDLP_JS_RUNTIME", "").strip()
    if configured:
        return configured
    for name in ("deno", "node"):
        found = shutil.which(name)
        if found:
            return f"{name}:{found}"
    return ""


YTDLP_JS_RUNTIME = _find_js_runtime()
# 置空可关闭远程组件下载（届时 YouTube 大概率下载失败）。
YTDLP_REMOTE_COMPONENTS = os.environ.get(
    "YTDLP_REMOTE_COMPONENTS", "ejs:github"
).strip()


def ytdlp_challenge_args() -> list[str]:
    """返回破解 YouTube n challenge 所需的参数。"""
    args: list[str] = []
    if YTDLP_JS_RUNTIME:
        args += ["--js-runtimes", YTDLP_JS_RUNTIME]
    if YTDLP_REMOTE_COMPONENTS:
        args += ["--remote-components", YTDLP_REMOTE_COMPONENTS]
    return args


def ytdlp_access_args() -> list[str]:
    """cookie + challenge 参数合集，所有 yt-dlp 调用都应带上。"""
    return ytdlp_cookie_args() + ytdlp_challenge_args()


# bgutil PO token 服务：YouTube 对无 PO token 的会话隐藏或封锁高清流。
# tools/bgutil-pot/server/build/main.js 常驻在 4416 端口，yt-dlp 插件会自动发现。
POT_SERVER_JS = BASE_DIR / "tools" / "bgutil-pot" / "server" / "build" / "main.js"
POT_SERVER_PORT = 4416


# ---------------------------------------------------------------- DeepSeek 翻译
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash")
try:
    DEEPSEEK_MAX_OUTPUT_TOKENS = int(os.environ.get("DEEPSEEK_MAX_OUTPUT_TOKENS", "8192"))
except ValueError:
    DEEPSEEK_MAX_OUTPUT_TOKENS = 8192
DEEPSEEK_MAX_OUTPUT_TOKENS = max(2048, min(32768, DEEPSEEK_MAX_OUTPUT_TOKENS))

# ---------------------------------------------------------------- Claude Code 本地翻译
def _find_claude_code_cli() -> str:
    configured = os.environ.get("CLAUDE_CODE_CLI", "").strip()
    if configured:
        return configured
    discovered = shutil.which("claude")
    if discovered:
        return discovered
    for candidate in (
        Path.home() / ".local" / "bin" / "claude.exe",
        Path.home() / ".local" / "bin" / "claude",
    ):
        if candidate.is_file():
            return str(candidate)
    return "claude"


CLAUDE_CODE_CLI = _find_claude_code_cli()
CLAUDE_CODE_MODEL = (
    os.environ.get("CLAUDE_CODE_MODEL", "claude-opus-5-5").strip() or "claude-opus-5-5"
)
CLAUDE_CODE_EFFORT = (
    os.environ.get("CLAUDE_CODE_EFFORT", "medium").strip().lower() or "medium"
)
if CLAUDE_CODE_EFFORT not in {"low", "medium", "high", "max"}:
    CLAUDE_CODE_EFFORT = "medium"
try:
    CLAUDE_CODE_TIMEOUT = int(os.environ.get("CLAUDE_CODE_TIMEOUT", "1800"))
except ValueError:
    CLAUDE_CODE_TIMEOUT = 1800
CLAUDE_CODE_TIMEOUT = max(60, min(3600, CLAUDE_CODE_TIMEOUT))

# ---------------------------------------------------------------- 第 5 步 AI 手绘画面
# Claude Code 本地额度画 SVG，再用本机 Edge/Chrome 无头模式渲染成 PNG。
SCENE_ART_MODEL = os.environ.get("SCENE_ART_MODEL", "claude-opus-5-5").strip() or "claude-opus-5-5"
SCENE_ART_EFFORT = os.environ.get("SCENE_ART_EFFORT", "low").strip().lower() or "low"
if SCENE_ART_EFFORT not in {"low", "medium", "high", "max"}:
    SCENE_ART_EFFORT = "low"
try:
    SCENE_ART_TIMEOUT = max(60, min(900, int(os.environ.get("SCENE_ART_TIMEOUT", "300"))))
except ValueError:
    SCENE_ART_TIMEOUT = 300


def _find_headless_browser() -> str:
    configured = os.environ.get("SCENE_ART_BROWSER", "").strip()
    if configured:
        return configured
    roots = [os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
             os.environ.get("ProgramFiles", r"C:\Program Files"),
             os.environ.get("LOCALAPPDATA", "")]
    for root in roots:
        if not root:
            continue
        for rel in (r"Microsoft\Edge\Application\msedge.exe",
                    r"Google\Chrome\Application\chrome.exe"):
            cand = Path(root) / rel
            if cand.is_file():
                return str(cand)
    return shutil.which("msedge") or shutil.which("chrome") or ""


SCENE_ART_BROWSER = _find_headless_browser()

try:
    TRANSLATE_MAX_ZH_SEGMENT_CHARS = int(os.environ.get("TRANSLATE_MAX_ZH_SEGMENT_CHARS", "42"))
except ValueError:
    TRANSLATE_MAX_ZH_SEGMENT_CHARS = 42
TRANSLATE_MAX_ZH_SEGMENT_CHARS = max(0, min(120, TRANSLATE_MAX_ZH_SEGMENT_CHARS))

# ---------------------------------------------------------------- ASR
# faster-whisper-small 已离线缓存；large-v3-turbo 质量更好但首次需联网下载 CT2 权重。
WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "small")
WHISPER_DEVICE = os.environ.get("WHISPER_DEVICE", "cuda")
WHISPER_COMPUTE_TYPE = os.environ.get("WHISPER_COMPUTE_TYPE", "float16")

# ---------------------------------------------------------------- TTS
TTS_ENGINE = os.environ.get("TTS_ENGINE", "f5")
TTS_VOICE = os.environ.get("TTS_VOICE", "人工老龙凤")
TTS_SPEED = float(os.environ.get("TTS_SPEED", "1.00"))  # 配音语速，0.7~1.3 微调
# 段间停顿（秒）：顺序拼接配音时，每段之间保留的停顿（默认即可，无需在界面调）。
TTS_GAP_MIN = float(os.environ.get("TTS_GAP_MIN", "0.08"))
TTS_GAP_MAX = float(os.environ.get("TTS_GAP_MAX", "0.45"))

# F5-TTS（本地·音色克隆）：使用 f5_tts 自带参考片段；也可自定义参考音频做声音克隆。
F5_REF_AUDIO = os.environ.get("TTS_REF_AUDIO", "")   # 自定义参考音频（wav/flac），留空用内置音色
F5_REF_TEXT = os.environ.get("TTS_REF_TEXT", "")     # 自定义参考音频对应的文字
try:
    F5_TTS_PARALLEL = int(os.environ.get("F5_TTS_PARALLEL", "4"))
except ValueError:
    F5_TTS_PARALLEL = 4
F5_TTS_PARALLEL = max(1, min(4, F5_TTS_PARALLEL))

# ---------------------------------------------------------------- 字幕样式
SUB_FONT = os.environ.get("SUB_FONT", "Microsoft YaHei")
SUB_FONTSIZE = int(os.environ.get("SUB_FONTSIZE", "54"))  # 字号按 1080p 画布计，libass 自动随分辨率缩放
SUB_PRIMARY = os.environ.get("SUB_PRIMARY", "#FFFFFF")    # 字体颜色
SUB_OUTLINE = os.environ.get("SUB_OUTLINE", "#000000")    # 描边颜色
SUB_POSITION = os.environ.get("SUB_POSITION", "bottom")   # bottom | middle | top
SUB_BOLD = os.environ.get("SUB_BOLD", "1")               # 是否加粗

# 遮挡原片烧死的英文字幕：在底部垫一条全宽半透明色条，盖住原字幕后再写中文。
# 仅 original 模式有意义（图片模式没有原字幕）；按需在界面勾选，全局默认可用 .env 改。
SUB_COVER_DEFAULT = os.environ.get("SUB_COVER_ORIGINAL", "0").strip() in ("1", "true", "True", "yes")
SUB_COVER_COLOR = os.environ.get("SUB_COVER_COLOR", "black")   # ffmpeg 颜色名或 #RRGGBB
try:
    SUB_COVER_OPACITY = float(os.environ.get("SUB_COVER_OPACITY", "0.9"))
except ValueError:
    SUB_COVER_OPACITY = 0.9
SUB_COVER_OPACITY = max(0.0, min(1.0, SUB_COVER_OPACITY))
try:
    SUB_COVER_HEIGHT = float(os.environ.get("SUB_COVER_HEIGHT", "0.22"))  # 色条高度占画面比例
except ValueError:
    SUB_COVER_HEIGHT = 0.22
SUB_COVER_HEIGHT = max(0.05, min(0.45, SUB_COVER_HEIGHT))

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
