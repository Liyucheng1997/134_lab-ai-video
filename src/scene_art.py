"""第 5 步 AI 手绘画面：按文稿分场景，让 Claude Code（本地订阅额度）用 SVG 作画。

流程：
1. 把配音分段按时长粗分成场景（几句话一个画面，不逐帧对齐）。
2. 多线程并发调用 Claude Code CLI，每个场景画一幅带 CSS 循环动画的 SVG。
3. 用本机 Edge/Chrome（Playwright 驱动）逐帧定格录制一段无缝循环动画，
   并叠加风格纸张/画布纹理；另存第 0 帧 PNG 作静态兜底。
4. 产物缓存在 work/<id>/scenes/，按「风格 + 文本 + 画布 + 动静」哈希命名，重跑直接复用。

有总时限：超过时限还没开始画的场景直接沿用相邻画面，保证整步在可控时间内结束。
"""
from __future__ import annotations

import hashlib
import queue
import re
import subprocess
import threading
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from . import claude_code, config
from .utils import _NO_WINDOW, log, save_json

# 循环动画片段：6 秒 × 25 帧；Claude 的动画周期都必须整除 6 秒。
LOOP_SECONDS = 6
FPS = 25

# ---------------------------------------------------------------- 风格
# texture：渲染时叠在画面上的纸张/画布质感层（不花 Claude 额度）。
STYLES: dict[str, dict] = {
    "oil": {
        "name": "古典油画",
        "guide": (
            "风格：古典油画（伦勃朗式明暗 + 印象派笔触）。深褐、焦赭、暖金、普鲁士蓝为主，"
            "强烈的单一光源和大面积暗部；用大量半透明、方向一致的短笔触 path 叠出体积，"
            "边缘用 feTurbulence + feDisplacementMap 做不规则的颜料感；背景用径向渐变营造烛光氛围。"
        ),
        "texture": "canvas",
    },
    "ink": {
        "name": "中国水墨",
        "guide": (
            "风格：中国水墨画（写意山水 / 禅意人物）。底色为米黄宣纸 #efe6d2，大量留白；"
            "墨分五色：浓墨、淡墨、焦墨、清墨，用 feGaussianBlur 做晕染、feTurbulence 做飞白与枯笔；"
            "远山用淡墨渐隐，近景用浓墨；可点缀一处朱砂红（如小印章方块、红日、红衣），不写任何汉字。"
        ),
        "texture": "paper",
    },
    "anime": {
        "name": "日系动漫",
        "guide": (
            "风格：日系动画电影背景（新海诚 / 吉卜力感）。高饱和但柔和的渐变天空、体积云、"
            "丁达尔光束与镜头光晕，赛璐璐分层上色（清晰的明暗两阶），人物为小比例背影或剪影，"
            "场景细节丰富（窗、街道、草地、星空、水面倒影）。"
        ),
        "texture": "grain",
    },
    "surreal": {
        "name": "超现实梦境",
        "guide": (
            "风格：超现实主义梦境（达利 / 马格利特）。空旷的地平线与长投影，漂浮或错位的象征物"
            "（门、楼梯、镜子、面具、钟、眼睛、月亮、海），冷暖对比的黄昏色调，画面安静而诡异，"
            "强调潜意识与梦的象征。"
        ),
        "texture": "grain",
    },
    "redbook": {
        "name": "荣格红书",
        "guide": (
            "风格：荣格《红书》手抄本插画 + 炼金术版画。深红、群青、墨绿、金色，对称的曼陀罗、"
            "蛇、太阳与月亮、生命之树、炼金容器等原型符号，平涂色块配细密金色描线与装饰边框，"
            "中世纪彩绘手稿的庄重感；不写任何文字或字母。"
        ),
        "texture": "parchment",
    },
}
DEFAULT_STYLE = "oil"


def public_styles() -> list[dict]:
    return [{"key": k, "name": v["name"]} for k, v in STYLES.items()]


def normalize_style(style: str | None) -> str:
    key = str(style or "").strip().lower()
    return key if key in STYLES else DEFAULT_STYLE


# ---------------------------------------------------------------- 分场景
_SENTENCE_END = tuple("。！？!?…；;")


def split_scenes(segs: list[dict], *, scene_seconds: float = 40,
                 max_scenes: int = 48) -> list[dict]:
    """把配音分段按时长粗分成场景；每个场景 = 连续几句话。"""
    segs = [s for s in segs if str(s.get("zh") or "").strip()]
    if not segs:
        return []
    total = float(segs[-1]["end"])
    target = max(float(scene_seconds), total / max(1, int(max_scenes)))

    scenes: list[dict] = []
    cur: list[dict] = []
    for seg in segs:
        cur.append(seg)
        span = float(seg["end"]) - float(cur[0]["start"])
        text = str(seg.get("zh") or "").strip()
        if span >= target * 1.5 or (span >= target and text.endswith(_SENTENCE_END)):
            scenes.append(_scene_of(cur))
            cur = []
    if cur:
        tail = _scene_of(cur)
        if scenes and tail["end"] - tail["start"] < target * 0.4:
            scenes[-1] = _scene_of(scenes[-1]["_segs"] + cur)
        else:
            scenes.append(tail)
    for i, sc in enumerate(scenes):
        sc["index"] = i
        sc.pop("_segs", None)
    return scenes


def _scene_of(segs: list[dict]) -> dict:
    return {
        "start": float(segs[0]["start"]),
        "end": float(segs[-1]["end"]),
        "text": "".join(str(s.get("zh") or "").strip() for s in segs),
        "_segs": list(segs),
    }


# ---------------------------------------------------------------- Claude 作画
_SVG_SCHEMA = {
    "type": "object",
    "properties": {
        "concept": {"type": "string", "description": "一句话描述这幅画的视觉隐喻"},
        "svg": {"type": "string", "description": "完整的 <svg>…</svg> 代码"},
    },
    "required": ["concept", "svg"],
    "additionalProperties": False,
}


def _system_prompt(style: str, w: int, h: int, animate: bool) -> str:
    guide = STYLES[style]["guide"]
    if animate:
        motion = f"""8. 动画：在 SVG 内 <style> 里用 CSS @keyframes 给 4~8 个元素加循环动效，让画面像动态插画一样"活"起来，
   例如火焰烛光摇曳、云雾/水面缓慢漂移、光晕呼吸明暗、尘埃/萤火/花瓣/雪粒飘浮、枝叶衣摆轻摆、星光闪烁、水波荡漾、人物轻微呼吸。
   - 全部 infinite 循环，周期只能是 1s、1.5s、2s、3s 或 {LOOP_SECONDS}s；0% 与 100% 关键帧必须完全相同，保证 {LOOP_SECONDS} 秒无缝循环；
   - 只动画 transform 和 opacity，给动画元素写 transform-box: fill-box 和合适的 transform-origin；
   - 幅度克制、节奏舒缓，符合心理学讲解的沉静氛围，不要卡通式跳动；同类元素可用负的 animation-delay 错开；
   - 不要用 SMIL <animate>、不要动画 filter、不要 JS。
9. 控制体量以保证速度：约 60~150 个元素，SVG 总长度不超过 16000 字符。
10. 禁止 <script>、<foreignObject>、<image>、外部链接、<text>。"""
    else:
        motion = """8. 控制体量以保证速度：约 60~150 个元素，SVG 总长度不超过 12000 字符。
9. 禁止 <script>、<foreignObject>、<image>、外部链接、CSS 动画、<text>。"""
    return f"""你是为「荣格心理学讲解」中文视频绘制配图的插画师，只能用 SVG 代码作画。
每次会给你视频中的一段讲解词，请为它画一幅插画，作为这段时间里的视频背景画面。

作画要求：
1. 先读懂这段话，挑一个具体、有画面感的视觉隐喻（人物处境、场景、原型象征物），而不是抽象图表。
2. {guide}
3. 画布：<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}">，必须铺满整个画布。
4. 画面底部约 22% 会叠加字幕：这里不要放主体，保持相对暗、简洁；主体放在画面上中部。
5. 有前景、中景、远景的层次和明确光源；人物用剪影或简化造型，比例要对，不画五官细节。
6. 绝对不要出现任何文字、字母、数字、图表、UI 元素。
7. 技法：用贝塞尔曲线 path 画有机形状，多层半透明叠色做体积与光影；可用渐变和最多 4 个 filter。
{motion}
只返回 JSON：concept（一句话画面构思）和 svg（完整 SVG 代码）。"""



def _sanitize_svg(svg: str, w: int, h: int) -> str:
    s = str(svg or "")
    start, end = s.find("<svg"), s.rfind("</svg>")
    if start < 0 or end < 0:
        raise ValueError("返回内容里没有完整的 <svg>")
    s = s[start:end + len("</svg>")]
    s = re.sub(r"<(script|foreignObject|text)\b.*?</\1\s*>", "", s, flags=re.S | re.I)
    s = re.sub(r"<(script|foreignObject|image|text)\b[^>]*/>", "", s, flags=re.I)
    s = re.sub(r"<image\b.*?</image\s*>", "", s, flags=re.S | re.I)
    s = re.sub(r"\son\w+\s*=\s*(\"[^\"]*\"|'[^']*')", "", s, flags=re.I)
    s = re.sub(r"(?:xlink:)?href\s*=\s*([\"'])\s*(?:https?:|file:|data:|//)[^\"']*\1", "", s,
               flags=re.I)

    m = re.match(r"<svg\b[^>]*>", s, flags=re.S)
    tag = m.group(0)
    attrs = re.sub(r"\s(width|height|preserveAspectRatio)\s*=\s*(\"[^\"]*\"|'[^']*')", "",
                   tag[4:-1], flags=re.I)
    if "xmlns=" not in attrs:
        attrs += ' xmlns="http://www.w3.org/2000/svg"'
    if "xlink:" in s and "xmlns:xlink" not in attrs:
        attrs += ' xmlns:xlink="http://www.w3.org/1999/xlink"'
    if "viewbox" not in attrs.lower():
        attrs += f' viewBox="0 0 {w} {h}"'
    attrs += f' width="{w}" height="{h}" preserveAspectRatio="xMidYMid slice"'
    s = "<svg " + attrs.strip() + ">" + s[len(tag):]
    ET.fromstring(s)          # 校验是合法 XML，不合法就抛错重画
    return s


def draw_svg(style: str, text: str, *, topic: str, index: int, total: int,
             w: int, h: int, animate: bool = True) -> tuple[str, str]:
    """调用 Claude Code 画一幅场景，返回 (concept, svg)。"""
    out = claude_code.call_structured_json(
        _system_prompt(style, w, h, animate),
        {"video_topic": topic, "scene": f"{index + 1}/{total}", "narration": text},
        _SVG_SCHEMA,
        cli_path=config.CLAUDE_CODE_CLI,
        model=config.SCENE_ART_MODEL,
        effort=config.SCENE_ART_EFFORT,
        timeout=config.SCENE_ART_TIMEOUT,
    )
    return str(out.get("concept") or "").strip(), _sanitize_svg(out.get("svg"), w, h)


# ---------------------------------------------------------------- 渲染
def _texture_layer(kind: str, w: int, h: int) -> str:
    grain = {
        "canvas": ("0.85 0.30", 2, "overlay", 0.30),
        "paper": ("0.035", 4, "multiply", 0.22),
        "parchment": ("0.05", 4, "multiply", 0.20),
        "grain": ("0.9", 1, "overlay", 0.10),
    }[kind]
    freq, octaves, blend, opacity = grain
    vignette = {"canvas": 0.50, "paper": 0.12, "parchment": 0.40, "grain": 0.30}[kind]
    # id 加前缀，避免和 Claude 画里的 id 撞名。
    return f"""
<svg class="layer" style="mix-blend-mode:{blend};opacity:{opacity}" width="{w}" height="{h}">
  <filter id="__tex_noise"><feTurbulence type="fractalNoise" baseFrequency="{freq}" numOctaves="{octaves}" seed="7"/>
  <feColorMatrix type="saturate" values="0"/></filter>
  <rect width="100%" height="100%" filter="url(#__tex_noise)"/></svg>
<svg class="layer" width="{w}" height="{h}">
  <radialGradient id="__tex_vig" cx="50%" cy="45%" r="75%">
    <stop offset="0.55" stop-color="#000" stop-opacity="0"/>
    <stop offset="1" stop-color="#000" stop-opacity="{vignette}"/></radialGradient>
  <rect width="100%" height="100%" fill="url(#__tex_vig)"/></svg>"""


def _html_page(svg: str, style: str, w: int, h: int) -> str:
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
html,body{{margin:0;padding:0;width:{w}px;height:{h}px;overflow:hidden;background:#111}}
.layer,#art{{position:absolute;left:0;top:0;width:{w}px;height:{h}px;display:block}}
#art>svg{{display:block;width:{w}px;height:{h}px}}
</style></head><body><div id="art">{svg}</div>{_texture_layer(STYLES[style]["texture"], w, h)}
</body></html>"""


# 把页面上所有 CSS/Web 动画和 SMIL 动画定格到指定毫秒，逐帧截图保证时间精确、循环无缝。
_SEEK_JS = """ms => {
  document.getAnimations().forEach(a => { a.pause(); a.currentTime = ms; });
  document.querySelectorAll('svg').forEach(s => {
    if (s.pauseAnimations) { s.pauseAnimations(); s.setCurrentTime(ms / 1000); }
  });
}"""


class Renderer:
    """每个工作线程独占一个无头浏览器（Playwright 同步 API 不能跨线程）。"""

    def __init__(self, w: int, h: int):
        self.w, self.h = w, h
        self._pw = None
        self._browser = None
        self._page = None

    def _ensure(self):
        if self._page is not None:
            return self._page
        browser = config.SCENE_ART_BROWSER
        if not browser:
            raise RuntimeError("找不到 Edge/Chrome，无法渲染画面（可设 SCENE_ART_BROWSER）")
        from playwright.sync_api import sync_playwright
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(
            executable_path=browser, headless=True,
            args=["--disable-gpu", "--hide-scrollbars", "--mute-audio"])
        self._page = self._browser.new_page(viewport={"width": self.w, "height": self.h})
        return self._page

    def render(self, svg: str, style: str, out_png: Path, out_loop: Path | None) -> None:
        """渲染第 0 帧 PNG；out_loop 非空时再录一段 LOOP_SECONDS 秒的循环动画 mp4。"""
        page = self._ensure()
        html = out_png.with_suffix(".html")
        html.write_text(_html_page(svg, style, self.w, self.h), encoding="utf-8")
        try:
            page.goto(html.as_uri(), wait_until="load")
            page.evaluate(_SEEK_JS, 0)
            page.screenshot(path=str(out_png), type="png")
            if out_loop is not None:
                self._record_loop(page, out_loop)
        finally:
            html.unlink(missing_ok=True)

    def _record_loop(self, page, out_loop: Path) -> None:
        tmp = out_loop.with_name(out_loop.stem + ".tmp.mp4")
        cmd = [config.FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
               "-f", "image2pipe", "-framerate", str(FPS), "-c:v", "mjpeg", "-i", "-",
               "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
               "-pix_fmt", "yuv420p", "-g", str(FPS * 2), str(tmp)]
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE,
                                creationflags=_NO_WINDOW)
        try:
            for i in range(LOOP_SECONDS * FPS):
                page.evaluate(_SEEK_JS, i * 1000 / FPS)
                proc.stdin.write(page.screenshot(type="jpeg", quality=92))
            proc.stdin.close()
            err = proc.stderr.read().decode("utf-8", "replace")
            if proc.wait() != 0 or not tmp.is_file():
                raise RuntimeError(f"循环动画编码失败：{err[-300:]}")
        except Exception:
            proc.kill()
            tmp.unlink(missing_ok=True)
            raise
        tmp.replace(out_loop)

    def close(self) -> None:
        for obj in (self._browser, self._pw):
            if obj is None:
                continue
            try:
                obj.close() if obj is self._browser else obj.stop()
            except Exception:
                pass
        self._pw = self._browser = self._page = None


def animation_available() -> bool:
    try:
        import playwright.sync_api  # noqa: F401
    except ImportError:
        return False
    return bool(config.SCENE_ART_BROWSER)


# ---------------------------------------------------------------- 主流程
def _scene_key(style: str, text: str, w: int, h: int, animate: bool) -> str:
    raw = f"v2|{style}|{w}x{h}|{'anim' if animate else 'still'}|{text}".encode("utf-8")
    return hashlib.sha1(raw).hexdigest()[:12]


def _ready(sc: dict) -> bool:
    return sc["status"] in ("drawn", "cached")


def generate_scenes(segs: list[dict], work_dir: Path, *, style: str = DEFAULT_STYLE,
                    width: int = 1920, height: int = 1080, scene_seconds: float = 40,
                    max_scenes: int = 48, concurrency: int = 6,
                    time_budget_min: float = 18, topic: str = "",
                    animate: bool = True) -> list[dict]:
    """生成全部场景。返回 [{index,start,end,text,png,loop,concept,status}]。

    loop 为循环动画 mp4 路径（静态模式或录制失败时为 None）。
    """
    style = normalize_style(style)
    if animate and not animation_available():
        log("compose", "未安装 playwright 或找不到浏览器，改为静态画面")
        animate = False
    scenes = split_scenes(segs, scene_seconds=scene_seconds, max_scenes=max_scenes)
    if not scenes:
        raise RuntimeError("没有可用的配音分段，无法分场景作画")
    scene_dir = work_dir / "scenes"
    scene_dir.mkdir(parents=True, exist_ok=True)
    total = len(scenes)
    avg = (scenes[-1]["end"] - scenes[0]["start"]) / total
    log("compose", f"AI 手绘：{STYLES[style]['name']}{'（动态）' if animate else '（静态）'}，"
                   f"共 {total} 个场景（平均每幅约 {avg:.0f} 秒），模型 {config.SCENE_ART_MODEL}，"
                   f"并发 {concurrency}，时限 {time_budget_min:g} 分钟")

    todo: queue.Queue = queue.Queue()
    for sc in scenes:
        sc["key"] = _scene_key(style, sc["text"], width, height, animate)
        png = scene_dir / f"{style}_{sc['key']}.png"
        loop = png.with_suffix(".mp4") if animate else None
        sc["png"], sc["loop"] = str(png), (str(loop) if loop else None)
        if png.is_file() and (loop is None or loop.is_file()):
            sc["status"] = "cached"
            meta = png.with_suffix(".txt")
            sc["concept"] = meta.read_text(encoding="utf-8") if meta.is_file() else ""
        else:
            sc["status"] = "pending"
            todo.put(sc)
    cached = total - todo.qsize()
    if cached:
        log("compose", f"复用已画好的场景 {cached} 幅")

    deadline = time.monotonic() + max(1.0, float(time_budget_min)) * 60
    lock = threading.Lock()
    counter = {"done": cached}

    def _draw_one(sc: dict, renderer: Renderer) -> None:
        last_err = ""
        for attempt in range(2):
            if time.monotonic() > deadline:
                sc["status"], sc["error"] = "skipped", last_err or "超过时限未绘制"
                return
            try:
                concept, svg = draw_svg(style, sc["text"], topic=topic, index=sc["index"],
                                        total=total, w=width, h=height, animate=animate)
                png = Path(sc["png"])
                png.with_suffix(".svg").write_text(svg, encoding="utf-8")
                loop = Path(sc["loop"]) if sc["loop"] else None
                try:
                    renderer.render(svg, style, png, loop)
                except Exception as exc:  # noqa: BLE001
                    if loop is None or not png.is_file():
                        raise
                    # 静态帧已出，只是录动画失败：退回静态，不重画浪费额度。
                    log("compose", f"场景 {sc['index'] + 1} 动画录制失败，改用静态：{str(exc)[:160]}")
                    sc["loop"] = None
                png.with_suffix(".txt").write_text(concept, encoding="utf-8")
                sc["concept"], sc["status"] = concept, "drawn"
                return
            except Exception as exc:  # noqa: BLE001
                last_err = str(exc)[:300]
                log("compose", f"场景 {sc['index'] + 1} 第 {attempt + 1} 次作画失败：{last_err}")
        sc["status"], sc["error"] = "failed", last_err

    def _worker() -> None:
        renderer = Renderer(width, height)
        try:
            while True:
                try:
                    sc = todo.get_nowait()
                except queue.Empty:
                    return
                _draw_one(sc, renderer)
                with lock:
                    counter["done"] += 1
                    done = counter["done"]
                pct = round(done / total * 90)
                if _ready(sc):
                    log("compose", f"场景 {sc['index'] + 1} 画好：{sc.get('concept', '')[:40]}"
                                   f"（已完成 {done} 幅，共 {total} 幅，{pct}%）")
                else:
                    log("compose", f"场景 {sc['index'] + 1} 未画成，沿用相邻画面"
                                   f"（已完成 {done} 幅，共 {total} 幅，{pct}%）")
        finally:
            renderer.close()

    threads = [threading.Thread(target=_worker, daemon=True)
               for _ in range(max(1, min(int(concurrency), todo.qsize())))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    ok = [sc for sc in scenes if _ready(sc)]
    if not ok:
        first_err = next((sc.get("error") for sc in scenes if sc.get("error")), "未知错误")
        raise RuntimeError(f"AI 手绘全部失败：{first_err}")
    # 没画成的场景沿用前一幅（开头缺失则用第一幅画好的）。
    fallback = ok[0]
    for sc in scenes:
        if _ready(sc):
            fallback = sc
        else:
            sc["png"], sc["loop"] = fallback["png"], fallback["loop"]

    missing = total - len(ok)
    log("compose", f"AI 手绘完成：新画 {sum(sc['status'] == 'drawn' for sc in scenes)} 幅，"
                   f"复用 {sum(sc['status'] == 'cached' for sc in scenes)} 幅"
                   + (f"，{missing} 幅沿用相邻画面" if missing else ""))
    save_json(scene_dir / "scenes.json", [
        {k: sc.get(k) for k in ("index", "start", "end", "text", "png", "loop", "concept",
                                "status", "error")}
        for sc in scenes
    ])
    return scenes
