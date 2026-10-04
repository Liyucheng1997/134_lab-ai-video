"""第 5 步「手绘动画」画面：沿用 hand-drawn-story-video skill 的手绘画布引擎。

Q 版动画人物（姿势、表情、走跑跳、对口型、气泡）在手绘画布上表演，配合卡片、便签、涂鸦布景和镜头推拉，
画面动作全部卡在第 4 步配音的时间轴上。

流程：
1. 按配音分段把全片切成若干章节（约 1 分钟一章）。
2. Claude Code（本机订阅额度）先当“导演”：按题材定画风、配乐、人物造型和每章标题/色调/情绪；
   再按章并发写“画面指令”（skill 的 cues 语法）和本章的布景元素。每章在无限画布上占一块独立舞台。
3. 指令按配音时间换算成事件时间轴（关键词按字数在本句内线性定位），口型包络取自 dub.wav。
4. Playwright 驱动本机 Edge 多路并行逐帧调用引擎的 render(t) 截图 → ffmpeg 编码，再拼接。
5. 程序合成 BGM + 音效（随 mood / 动作走），与整轨配音混音；可选烧录 ASS 字幕。

产物在 work/<id>/handdrawn/：plan_*.json（导演/各章指令缓存，重跑直接复用）、world.js、timeline.js、index.html。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
import shutil
import subprocess
import threading
import time
import wave
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import numpy as np

from . import claude_code, config
from .utils import _NO_WINDOW, load_json, log, media_duration, run, save_json

ENGINE_DIR = Path(__file__).with_name("hand_drawn_web")
DOCS_DIR = ENGINE_DIR / "docs"
FONT_DIRS = [config.BASE_DIR / "tools" / "hand_drawn_fonts",
             Path.home() / ".claude" / "skills" / "hand-drawn-story-video" / "template" / "web" / "fonts"]
PLAN_VERSION = "hd2"
ENV_FPS = 30
CHAPTER_GAP = 6000          # 相邻章节舞台在世界坐标里相隔多远（镜头切换时互不入画）

THEMES = {
    "notebook": "手账点阵：米色纸 + 点阵 + 手写字（生活方式、轻松讲书、个人成长）",
    "storybook": "水彩绘本：奶白纸、水彩晕染、圆角卡（童话、寓言、成长、温情、亲子、心理）",
    "chalk": "黑板：墨绿黑板、粉笔线（科普、数学物理、课堂知识点、学习方法）",
    "blueprint": "蓝图：蓝底白线网格（科技、发明、工程、AI、编程、商业模式）",
    "noir": "黑白默片：灰阶排线 + 胶片颗粒，只留红色（悬疑、犯罪、战争、悲剧、存在主义）",
    "roman": "古典壁画：羊皮纸、赭红与金、版画排线（西方古代史、神话、宗教、哲学史）",
    "ink": "水墨：宣纸、毛笔线、朱印（中国历史、诗词、国学、禅、东方哲学）",
    "zettel": "牛皮纸索引卡（笔记、写作、效率、知识管理）",
}
GRADES = ["none", "warm", "gold", "dusk", "cold", "ash", "sepia", "night", "blood"]
MOODS = ["calm", "warm", "happy", "tense", "sad", "dark", "epic"]
VERBS = {"card", "edge", "icon", "note", "group", "dead", "hl", "fade", "swap", "move", "walk", "run",
         "jump", "float", "place", "actor", "pose", "expr", "face", "say", "think", "emote", "talk", "hold",
         "shake", "leave", "cam", "camz", "hud", "mood", "grade", "rain", "fx"}
ACTOR_VERBS = {"actor", "pose", "expr", "face", "say", "think", "emote", "talk", "hold", "shake", "leave", "place", "float"}
SHOW_VERBS = {"card": "NODES", "icon": "ICON_POS", "note": "NOTES", "group": "GROUPS", "edge": "EDGES"}
HOST_LOOK = {"hair": "short", "hairColor": "#2b2420", "top": "#2f6db5", "bottom": "#3b3b44", "glasses": True, "buttons": True}


# ---------------------------------------------------------------- 画幅与舞台
def _layout(w: int, h: int) -> dict:
    """单章舞台的大小：横屏宽而矮，竖屏窄而高；人物缩放让站立的人占画面高度约 35–45%。"""
    if h > w:
        return {"half_w": 450, "top": -1060, "bottom": 60, "actor_s": 2.0, "orient": "竖屏 9:16"}
    return {"half_w": 900, "top": -940, "bottom": 70, "actor_s": 1.5, "orient": "横屏 16:9"}


def _stage(k: int, lay: dict) -> dict:
    cx = k * CHAPTER_GAP
    return {"id": f"c{k}", "cx": cx, "x0": cx - lay["half_w"], "x1": cx + lay["half_w"],
            "y0": lay["top"], "y1": lay["bottom"]}


# ---------------------------------------------------------------- 分章
_END = tuple("。！？!?…；;")


def split_chapters(segs: list[dict], *, chapter_seconds: float = 60, max_lines: int = 18,
                   max_chapters: int = 40) -> list[list[int]]:
    """返回每章包含的分段下标。尽量在句末断开。"""
    idx = [i for i, s in enumerate(segs) if str(s.get("zh") or "").strip()]
    if not idx:
        return []
    total = float(segs[idx[-1]]["end"]) - float(segs[idx[0]]["start"])
    target = max(float(chapter_seconds), total / max(1, max_chapters))
    chapters, cur = [], []
    for i in idx:
        cur.append(i)
        span = float(segs[i]["end"]) - float(segs[cur[0]]["start"])
        text = str(segs[i].get("zh") or "").strip()
        if span >= target * 1.4 or len(cur) >= max_lines * 1.4 or \
                ((span >= target or len(cur) >= max_lines) and text.endswith(_END)):
            chapters.append(cur)
            cur = []
    if cur:
        if chapters and float(segs[cur[-1]]["end"]) - float(segs[cur[0]]["start"]) < target * 0.35:
            chapters[-1] += cur
        else:
            chapters.append(cur)
    return chapters


# ---------------------------------------------------------------- Claude：导演
_DIRECTOR_SCHEMA = {
    "type": "object",
    "properties": {
        "theme": {"type": "string", "enum": list(THEMES)},
        "theme_reason": {"type": "string"},
        "music": {"type": "object", "properties": {
            "preset": {"type": "string", "enum": ["minor", "major", "guofeng"]},
            "bpm": {"type": "integer"},
            "mood": {"type": "string", "enum": MOODS},
            "instr": {"type": "string", "enum": ["latin", "ancient"]}},
            "required": ["preset", "bpm", "mood"]},
        "actors": {"type": "array", "items": {"type": "object", "properties": {
            "id": {"type": "string"}, "name": {"type": "string"}, "role": {"type": "string"},
            "look": {"type": "object"}}, "required": ["id", "name", "look"]}},
        "kinds": {"type": "array", "items": {"type": "object", "properties": {
            "key": {"type": "string"}, "color": {"type": "string"}, "label": {"type": "string"}},
            "required": ["key", "color"]}},
        "chapters": {"type": "array", "items": {"type": "object", "properties": {
            "title": {"type": "string"}, "grade": {"type": "string", "enum": GRADES},
            "mood": {"type": "string", "enum": MOODS}}, "required": ["title", "grade", "mood"]}},
    },
    "required": ["theme", "music", "actors", "chapters"],
}


def _doc(name: str) -> str:
    try:
        return (DOCS_DIR / name).read_text(encoding="utf-8")
    except OSError:
        return ""


def _director_prompt(n_chapters: int, lay: dict) -> str:
    themes = "\n".join(f"- {k}: {v}" for k, v in THEMES.items())
    return f"""你是手绘动画讲解视频的导演。视频是把一段外语视频翻译成中文后的配音讲解（{lay['orient']}），
画面是“Q 版动画人物在手绘画布上表演 + 卡片/便签/涂鸦布景 + 镜头推拉”。请按内容和主题做总体设计：

1. theme：画风预设，选最贴题材的一个：
{themes}
2. music：配乐。preset：minor（忧伤/史诗/思辨）、major（轻快/童趣/积极）、guofeng（中国题材）；bpm 60–96；
   mood 为开场情绪；instr 讲古代史用 ancient，其余 latin。
3. actors：2–5 个动画人物。必须包含讲解员 HOST（旁白的化身，全片主角，讲道理时由他表演）；
   再按内容加入文中出现的关键人物或“代表观众的普通人”（如 YOU、BOSS、KID）。id 用大写英文；
   look 用 characters.md 里的字段做出辨识度（发型、帽子、衣服颜色、胡子、眼镜、age 等）。
4. kinds：卡片类型（0–4 个），{{key, color(#hex), label}}，用于概念卡片分类；没有就给空数组。
5. chapters：恰好 {n_chapters} 个，与给出的章节一一对应：title（≤ 8 字，章节标签）、grade（章节色调）、mood（配乐情绪），
   按内容的情绪曲线起伏，不要每章一样。

参考资料（人物造型字段）：
{_doc('characters.md')}
"""


def plan_director(topic: str, chapters_text: list[str], lay: dict, *, model: str, effort: str,
                  timeout: int) -> dict:
    payload = {"video_topic": topic,
               "chapters": [{"index": k, "text": t[:600]} for k, t in enumerate(chapters_text)]}
    return claude_code.call_structured_json(
        _director_prompt(len(chapters_text), lay), payload, _DIRECTOR_SCHEMA,
        cli_path=config.CLAUDE_CODE_CLI, model=model, effort=effort, timeout=timeout)


def _fallback_director(n: int) -> dict:
    return {"theme": "notebook", "music": {"preset": "major", "bpm": 80, "mood": "warm"},
            "actors": [{"id": "HOST", "name": "讲解员", "look": HOST_LOOK}], "kinds": [],
            "chapters": [{"title": f"第{k + 1}部分", "grade": "none", "mood": "warm"} for k in range(n)]}


# ---------------------------------------------------------------- Claude：分章画面指令
_OBJ = {"type": "object"}
_CHAPTER_SCHEMA = {
    "type": "object",
    "properties": {
        "icons": {"type": "array", "items": {"type": "object", "properties": {
            "id": {"type": "string"}, "icon": {"type": "string"}, "x": {"type": "number"},
            "y": {"type": "number"}, "s": {"type": "number"}}, "required": ["id", "x", "y"]}},
        "notes": {"type": "array", "items": {"type": "object", "properties": {
            "id": {"type": "string"}, "x": {"type": "number"}, "y": {"type": "number"},
            "text": {"type": "string"}, "size": {"type": "number"}, "color": {"type": "string"},
            "style": {"type": "string", "enum": ["title", "plain", "sticky", "bubble", "circle", "legend"]}},
            "required": ["id", "x", "y", "text"]}},
        "nodes": {"type": "array", "items": {"type": "object", "properties": {
            "id": {"type": "string"}, "x": {"type": "number"}, "y": {"type": "number"},
            "kind": {"type": "string"}, "name": {"type": "string"}, "tag": {"type": "string"}},
            "required": ["id", "x", "y", "name"]}},
        "edges": {"type": "array", "items": {"type": "object", "properties": {
            "id": {"type": "string"}, "type": {"type": "string"}, "a": {"type": "string"},
            "b": {"type": "string"}, "label": {"type": "string"}}, "required": ["id", "type"]}},
        "groups": {"type": "array", "items": {"type": "object", "properties": {
            "id": {"type": "string"}, "x0": {"type": "number"}, "x1": {"type": "number"},
            "y0": {"type": "number"}, "y1": {"type": "number"}, "label": {"type": "string"}},
            "required": ["id", "x0", "x1", "y0", "y1"]}},
        "areas": {"type": "array", "items": {"type": "object", "properties": {
            "id": {"type": "string"}, "x0": {"type": "number"}, "x1": {"type": "number"},
            "y0": {"type": "number"}, "y1": {"type": "number"}}, "required": ["id", "x0", "x1", "y0", "y1"]}},
        "lines": {"type": "array", "items": {"type": "object", "properties": {
            "i": {"type": "integer"}, "cues": {"type": "array", "items": {"type": "string"}}},
            "required": ["i", "cues"]}},
    },
    "required": ["icons", "notes", "nodes", "lines"],
}


def _chapter_prompt(lay: dict, w: int, h: int) -> str:
    return f"""你是手绘动画讲解视频的分镜师，负责一章（约 1 分钟）的画面。画幅 {w}×{h}（{lay['orient']}）。
画面引擎：Q 版动画人物在手绘画布上表演，配合涂鸦布景、卡片、便签，镜头在无限画布上推拉。
配音已经录好，你只写“画面指令”（cues）和本章用到的布景元素。

## 输出
- icons / notes / nodes / edges / groups：本章新建的元素（字段见 world.md；icons 的 icon 必须是涂鸦库里的名字，
  id 与画法同名时可省略 icon）。id 用小写英文，章内唯一（系统会自动加章节前缀，你照常写就行）。
- areas：可选，本章额外的镜头区域（如 board 讲解板区域）。本章舞台区域固定叫 stage。
- lines：给出的每一句（按 i）一组 cues。每条 cue 写成 "关键词 > 动作; 动作"，关键词必须是这句原文里出现的字串，
  或 ^（句首）、$（句尾）、@秒数（本句开始后几秒）。语法见 cues.md。

## 舞台（世界坐标，y 向下为正，地面 y=0，人物站在地面上）
- 本章舞台 stage：x 从 {{x0}} 到 {{x1}}，y 从 {lay['top']} 到 {lay['bottom']}。所有元素和人物都放在这个范围内。
- 人物缩放已设为 s≈{lay['actor_s']}，站着高约 {int(300 * lay['actor_s'])}。布景涂鸦 s 取 1.2–2.5；
  卡片、便签、标题放在人物头顶上方的空间（y 约 {lay['top'] + 150} 到 {-int(300 * lay['actor_s']) - 80}）。
- 文字要在手机上看得清：notes 的 size ≥ 56（标题 ≥ 72），涂鸦 cap 字少而短；卡片 name ≤ 8 字。
- 元素之间留足空隙（中心至少相隔 260，卡片至少相隔 380），不要叠在一起，也不要压在人物头上。
{'- 竖屏：画面顶部约 220 像素和底部 25% 会被抖音的按钮、文案和字幕挡住，重要内容放在舞台中部；元素多往上下排，别横着铺开。' if h > w else ''}
- 本章开头系统已自动执行 `cam stage`、章节 hud、调色和配乐情绪；你不要再写 hud。
- 人物上一章可能在别的舞台：本章第一句的 ^ 里，对本章要用的每个人物先 `place ID x` 再 `actor ID`
  （或 `actor ID walkin`），x 在本章舞台范围内。不用的人物不用管。

## 怎么演（最重要）
- 用户要的是“生动形象的动画人物，而不是大段文字”。每一句至少一个人物动作（姿势/表情/走位/情绪符号/气泡），
  说到谁谁就动；讲道理时 HOST `talk HOST` + `pose HOST present/point/think`。
- 情绪三件套：姿势 + 表情 + 符号。连续两句别用同一姿势。
- 每句 1–3 条 cue 即可，不要堆砌；屏幕文字只用于短标签、便签、卡片名（≤ 12 字），讲解交给配音。
- 每章布置 3–8 个布景/道具涂鸦，把抽象概念变成看得见的东西（书、灯泡、钱袋、齿轮、图表、山、房子……）。
- 镜头：大部分时间 `cam stage`；情绪高点 `camz 1.3 人物` 推近，之后记得 `cam stage` 拉回。一句里最多换一次镜头。
- 只能用下列动作：{', '.join(sorted(VERBS))}。不要用 map/era/route/wind/restore/ev。
- 坐标都是数字，不要写表达式；say/think 文字 ≤ 12 字，不含分号和 >。

## 参考资料
{_doc('cues.md')}

{_doc('characters.md')}

{_doc('world.md')}
"""


def plan_chapter(k: int, seg_ids: list[int], segs: list[dict], director: dict, stage: dict,
                 lay: dict, w: int, h: int, *, topic: str, model: str, effort: str, timeout: int) -> dict:
    ch = director["chapters"][k]
    actors = [{"id": a["id"], "name": a.get("name", ""), "role": a.get("role", "")} for a in director["actors"]]
    payload = {
        "video_topic": topic,
        "theme": director.get("theme"),
        "chapter": {"index": k, "title": ch.get("title"), "mood": ch.get("mood")},
        "stage": {"x0": stage["x0"], "x1": stage["x1"], "y0": stage["y0"], "y1": stage["y1"], "ground_y": 0},
        "actors": actors,
        "kinds": [kd.get("key") for kd in director.get("kinds") or []],
        "lines": [{"i": i, "text": str(segs[i].get("zh") or "").strip(),
                   "seconds": round(float(segs[i]["end"]) - float(segs[i]["start"]), 1)} for i in seg_ids],
    }
    prompt = _chapter_prompt(lay, w, h).replace("{x0}", str(stage["x0"])).replace("{x1}", str(stage["x1"]))
    return claude_code.call_structured_json(
        prompt, payload, _CHAPTER_SCHEMA, cli_path=config.CLAUDE_CODE_CLI, model=model,
        effort=effort, timeout=timeout)


def _fallback_chapter(k: int, seg_ids: list[int], segs: list[dict], stage: dict, director: dict) -> dict:
    """Claude 没画出来时的兜底：讲解员在舞台上轮换姿势讲解。"""
    poses = ["present", "point", "think", "shrug", "present", "wave"]
    exprs = ["happy", "thinking", "determined", "neutral", "smug", "happy"]
    title = director["chapters"][k].get("title") or ""
    lines = []
    for n, i in enumerate(seg_ids):
        cues = [f"^ > talk HOST; pose HOST {poses[n % 6]}; expr HOST {exprs[n % 6]}"]
        if n == 0:
            cues.insert(0, f"^ > place HOST {stage['cx'] + 250}; actor HOST walkin; icon book; note title")
        lines.append({"i": i, "cues": cues})
    return {"icons": [{"id": "book", "x": stage["cx"] - 350, "y": -90, "s": 2}],
            "notes": [{"id": "title", "x": stage["cx"], "y": stage["y0"] + 260, "text": title or "……",
                       "size": 80, "style": "title"}],
            "nodes": [], "edges": [], "groups": [], "areas": [], "lines": lines, "_fallback": True}


# ---------------------------------------------------------------- 合并成 world
_ID_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _num(v, default=0.0):
    try:
        f = float(v)
        return f if math.isfinite(f) else default
    except (TypeError, ValueError):
        return default


def build_world(director: dict, chapters: list[dict], stages: list[dict], lay: dict) -> tuple[dict, list[dict]]:
    """把导演设计 + 各章元素合成引擎的 WORLD；返回 (world, 每章的 id 映射)。"""
    actors = {}
    for a in director.get("actors") or []:
        aid = str(a.get("id") or "").strip().upper()
        if not _ID_RE.match(aid) or aid in actors:
            continue
        look = a.get("look") if isinstance(a.get("look"), dict) else {}
        actors[aid] = {"x": stages[0]["cx"], "y": 0, "face": 1, "s": lay["actor_s"], "look": look}
    if "HOST" not in actors:
        actors["HOST"] = {"x": stages[0]["cx"], "y": 0, "face": -1, "s": lay["actor_s"], "look": HOST_LOOK}
    kinds = {}
    for kd in director.get("kinds") or []:
        key = str(kd.get("key") or "").strip()
        if _ID_RE.match(key):
            kinds[key] = {"color": kd.get("color") or "#e07a2e", **({"label": kd["label"]} if kd.get("label") else {})}
    world = {"THEME": director.get("theme") if director.get("theme") in THEMES else "notebook",
             "C": {}, "KINDS": kinds, "ACTORS": actors, "NODES": {}, "EDGES": {}, "ICON_POS": {},
             "EVENTS": {}, "GROUPS": {}, "NOTES": {}, "AREAS": {}, "TIMELINE_META": None, "WIND_KEEP": []}
    maps = []
    for k, (plan, st) in enumerate(zip(chapters, stages)):
        pre = f"c{k}_"
        idmap = {"stage": st["id"]}
        world["AREAS"][st["id"]] = {"x0": st["x0"], "x1": st["x1"], "y0": st["y0"], "y1": st["y1"]}
        for key, bucket in (("icons", "ICON_POS"), ("notes", "NOTES"), ("nodes", "NODES"),
                            ("edges", "EDGES"), ("groups", "GROUPS"), ("areas", "AREAS")):
            for el in plan.get(key) or []:
                if not isinstance(el, dict):
                    continue
                oid = str(el.get("id") or "").strip()
                if not _ID_RE.match(oid) or oid in actors or oid == "stage":
                    continue
                nid = pre + oid
                idmap[oid] = nid
                item = {k2: v for k2, v in el.items() if k2 != "id"}
                for c in ("x", "y", "x0", "x1", "y0", "y1", "s", "size"):
                    if c in item:
                        item[c] = _num(item[c])
                if bucket == "ICON_POS" and not item.get("icon"):
                    item["icon"] = oid
                world[bucket][nid] = item
        # 关系线两端、分组等引用的 id 也要换成带前缀的
        for nid, e in list(world["EDGES"].items()):
            if not nid.startswith(pre):
                continue
            for f in ("a", "b", "to", "with"):
                if isinstance(e.get(f), str):
                    e[f] = idmap.get(e[f], e[f])
            if isinstance(e.get("from"), list):
                e["from"] = [idmap.get(x, x) for x in e["from"]]
            if not (e.get("a") and e.get("b")) and not (e.get("from") and e.get("to")):
                world["EDGES"].pop(nid)
        maps.append(idmap)
    return world, maps


# ---------------------------------------------------------------- 时间轴
def _norm_idx(text: str) -> list[int]:
    idx, k = [], -1
    for ch in text:
        if re.match(r"[一-鿿A-Za-z0-9]", ch):
            k += 1
        idx.append(max(k, 0))
    return idx


def _split_subs(text: str) -> list[tuple[int, int, str]]:
    """按标点切成不超过 ~18 字的字幕块（与 skill 的 timeline.py 一致）。"""
    parts, start = [], 0
    for m in re.finditer(r"[，。；：！？、,.;:!?][”」』]*|——", text):
        parts.append((start, m.end())); start = m.end()
    if start < len(text):
        parts.append((start, len(text)))
    merged = []
    for a, b in parts:
        if merged and (b - merged[-1][0]) <= 16 and text[merged[-1][1] - 1] not in "。！？；":
            merged[-1] = (merged[-1][0], b)
        else:
            merged.append((a, b))
    res = []
    for a, b in merged:
        if b - a > 22:
            mid = a + (b - a) // 2
            res += [(a, mid), (mid, b)]
        else:
            res.append((a, b))
    out = []
    for a, b in res:
        s = re.sub(r"[“”「」]", "", text[a:b].strip())
        s = re.sub(r"[，。；、,.;]+$", "", s)
        if s:
            out.append((a, b, s))
    return out


def _clean_action(act: str, idmap: dict, world: dict, stage_id: str) -> str | None:
    """校验并改写一个动作：动词白名单、元素 id 加章节前缀、引用不存在的人物/元素就丢掉。"""
    parts = act.split()
    if not parts or parts[0] not in VERBS:
        return None
    verb, args = parts[0], parts[1:]
    actors = world["ACTORS"]
    if verb in ("hud",):
        return None                     # 章节标签由系统统一加
    if verb in ("say", "think"):
        if len(args) < 2 or args[0] not in actors:
            return None
        return f"{verb} {args[0]} {' '.join(args[1:])[:30]}"
    if verb in ("mood", "grade", "rain", "fx"):
        return act if args else None
    if verb in ("cam", "camz"):
        zoom = []
        if verb == "camz":
            if not args:
                return None
            zoom, args = [args[0]], args[1:]
        ids = [idmap.get(a, a) for a in args]
        known = [a for a in ids if a in actors or any(a in world[b] for b in
                 ("NODES", "ICON_POS", "NOTES", "GROUPS", "AREAS", "EDGES"))]
        if not known:
            known = [stage_id]
        return " ".join([verb, *zoom, *known])
    if not args:
        return None
    target = idmap.get(args[0], args[0])
    rest = args[1:]
    if verb in SHOW_VERBS:
        return f"{verb} {target}" if target in world[SHOW_VERBS[verb]] else None
    if verb == "swap":
        ids = [idmap.get(a, a) for a in args]
        return f"swap {' '.join(ids)}" if all(i in world["NODES"] for i in ids) else None
    if verb in ("dead", "hl", "fade"):
        exists = target in actors or any(target in world[b] for b in
                                         ("NODES", "ICON_POS", "NOTES", "GROUPS", "AREAS", "EDGES"))
        return " ".join([verb, target, *rest]) if exists else None
    if verb in ("move", "walk", "run", "jump"):
        if target not in actors and target not in world["ICON_POS"]:
            return None
        nums = [str(round(_num(v))) for v in rest[:2]]
        return " ".join([verb, target, *nums]) if nums else None
    if verb in ACTOR_VERBS:
        if target not in actors:
            return None
        if verb in ("place", "float"):
            nums = [str(round(_num(v))) for v in rest[:2]]
            return " ".join([verb, target, *nums]) if nums else None
        if verb == "talk":
            return f"talk {target}" + (" off" if rest[:1] == ["off"] else "")
        return " ".join([verb, target, *rest[:1]])
    return None


def build_timeline(segs: list[dict], chapters_idx: list[list[int]], plans: list[dict], director: dict,
                   world: dict, idmaps: list[dict], stages: list[dict], *, duration: float,
                   engine_subs: bool, size: tuple[int, int], voice_env: str) -> dict:
    events, subs, lines = [], [], []
    dropped = 0
    visible: set[str] = set()

    def add(t, act, lid):
        events.append({"t": round(max(0.0, t), 3), "act": act, "line": lid})

    for k, seg_ids in enumerate(chapters_idx):
        plan, idmap, st = plans[k], idmaps[k], stages[k]
        ch = director["chapters"][k] if k < len(director["chapters"]) else {}
        cues_by_i = {}
        for ln in plan.get("lines") or []:
            try:
                cues_by_i[int(ln.get("i"))] = [str(c) for c in ln.get("cues") or []]
            except (TypeError, ValueError):
                continue
        t_ch = float(segs[seg_ids[0]]["start"])
        lid0 = f"s{seg_ids[0]}"
        add(t_ch - .6, f"cam {st['id']}", lid0)
        if ch.get("title"):
            add(t_ch - .55, f"hud {ch['title']}", lid0)
        if ch.get("grade"):
            add(t_ch - .55, f"grade {ch['grade']}", lid0)
        if ch.get("mood"):
            add(t_ch - .55, f"mood {ch['mood']}", lid0)
        placed: set[str] = set()
        first_actor_evt: list[str] = []
        for n, i in enumerate(seg_ids):
            seg = segs[i]
            text = str(seg.get("zh") or "").strip()
            start, end = float(seg["start"]), float(seg["end"])
            dur = max(.1, end - start)
            lid = f"s{i}"
            nidx = _norm_idx(text)
            nchar = max(1, (nidx[-1] + 1) if nidx else 1)

            def at_char(ci, which=0):
                if not nidx:
                    return start
                k2 = min(nidx[min(ci, len(nidx) - 1)], nchar - 1) + which
                return start + dur * k2 / nchar

            cursor = 0
            for cue in cues_by_i.get(i, []):
                if ">" not in cue:
                    continue
                key, acts = [s.strip() for s in cue.split(">", 1)]
                if key == "^":
                    when = start - .25
                elif key == "$":
                    when = end + .05
                elif key.startswith("@"):
                    when = start + _num(key[1:])
                else:
                    pos = text.find(key, cursor)
                    if pos < 0:
                        pos = text.find(key)
                    if pos < 0:
                        when = start - .25 if n == 0 else start
                    else:
                        cursor = pos
                        when = at_char(pos) - .08
                for act in acts.split(";"):
                    act = act.strip()
                    if not act:
                        continue
                    clean = _clean_action(act, idmap, world, st["id"])
                    if not clean:
                        dropped += 1
                        continue
                    verb, *rest = clean.split()
                    aid = rest[0] if rest and rest[0] in world["ACTORS"] and verb not in ("cam", "camz") else None
                    if aid:
                        if verb == "place":
                            placed.add(aid)
                        elif aid not in placed:
                            first_actor_evt.append(aid)
                            placed.add(aid)
                        if verb == "actor":
                            visible.add(aid)
                        elif verb in ("leave", "fade"):
                            visible.discard(aid)
                        elif verb != "place" and aid not in visible:
                            add(when - .05, f"actor {aid}", lid)     # 没上场就先让他上场
                            visible.add(aid)
                    if verb == "talk" and len(rest) == 1:
                        clean = f"talk {rest[0]} {end:.3f}"
                    add(when, clean, lid)
            if text and engine_subs:
                for a, b, s in _split_subs(text):
                    subs.append({"t0": round(at_char(a), 3), "t1": round(at_char(max(a, b - 1), 1) + .15, 3),
                                 "text": s})
            lines.append({"id": lid, "t0": round(start, 3), "dur": round(dur, 3), "text": text,
                          "recorded": True, "silent": not text})
        # 没被 place 过就直接上场的人物：在章节开头挪到本章舞台上
        for j, aid in enumerate(dict.fromkeys(first_actor_evt)):
            x = st["cx"] + (250 if aid == "HOST" else -250 + 260 * (j % 3) - 260)
            add(t_ch - .3, f"place {aid} {round(x)}", lid0)
    events.sort(key=lambda e: e["t"])
    subs.sort(key=lambda s: s["t0"])
    for a, b in zip(subs, subs[1:]):
        if a["t1"] > b["t0"] - .02 or b["t0"] - a["t1"] < .25:
            a["t1"] = b["t0"] - .02
    meta = {"title": "", "size": list(size), "music": director.get("music") or {}}
    if dropped:
        log("compose", f"手绘动画：丢弃 {dropped} 个无效画面指令（引用了不存在的人物/元素或语法不对）")
    return {"duration": round(duration, 3), "meta": meta, "lines": lines, "events": events,
            "subs": subs, "env": voice_env, "envFps": ENV_FPS}


def voice_envelope(wav: Path, duration: float) -> str:
    """配音的响度包络（每秒 30 帧，0–9），给人物对口型用。"""
    from .hand_drawn_audio import read_voice, SR
    x = read_voice(wav)
    hop = SR // ENV_FPS
    n = len(x) // hop
    if n <= 0:
        return ""
    e = np.sqrt(np.mean(x[:n * hop].reshape(n, hop) ** 2, axis=1))
    ref = np.percentile(e, 92) + 1e-6
    e = np.clip(e / ref, 0, 1) ** .8
    total = int(duration * ENV_FPS) + 1
    e = np.concatenate([e, np.zeros(max(0, total - len(e)))])[:total]
    return "".join(chr(48 + int(round(v * 9))) for v in e)


# ---------------------------------------------------------------- 页面
def _font_dir() -> Path | None:
    for d in FONT_DIRS:
        if (d / "LongCang-Regular.ttf").is_file():
            return d
    return None


def write_page(out_dir: Path, world: dict, timeline: dict) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "world.js").write_text(
        "window.WORLD = " + json.dumps(world, ensure_ascii=False) + ";\n", encoding="utf-8")
    (out_dir / "timeline.js").write_text(
        "window.TIMELINE = " + json.dumps(timeline, ensure_ascii=False) + ";\n", encoding="utf-8")
    save_json(out_dir / "timeline.json", {k: v for k, v in timeline.items() if k != "env"})
    fd = _font_dir()
    faces = ""
    if fd:
        for fam in ("LongCang", "MaShanZheng", "ZhiMangXing"):
            f = fd / f"{fam}-Regular.ttf"
            if f.is_file():
                faces += f'  @font-face {{ font-family: "{fam}"; src: url("{f.as_uri()}"); }}\n'
    else:
        log("compose", "手绘动画：未找到手写字体（tools/hand_drawn_fonts），将用系统楷体代替")
    eng = lambda name: (ENGINE_DIR / name).as_uri()
    w, h = timeline["meta"]["size"]
    html = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>hand drawn</title>
<style>
{faces}  html, body {{ margin: 0; background: #f5efe3; overflow: hidden; }}
  canvas {{ display: block; }}
</style></head>
<body>
<canvas id="c" width="{w}" height="{h}"></canvas>
<script src="{eng('themes.js')}"></script>
<script src="{eng('geo.js')}"></script>
<script src="world.js"></script>
<script src="timeline.js"></script>
<script src="{eng('board.js')}"></script>
<script src="{eng('characters.js')}"></script>
</body></html>
"""
    page = out_dir / "index.html"
    page.write_text(html, encoding="utf-8")
    return page


# ---------------------------------------------------------------- 渲染
_BROWSER_ARGS = ["--allow-file-access-from-files", "--force-color-profile=srgb",
                 "--font-render-hinting=none", "--hide-scrollbars", "--mute-audio"]


async def _open(pw, page_path: Path, w: int, h: int):
    browser = config.SCENE_ART_BROWSER
    if not browser:
        raise RuntimeError("找不到 Edge/Chrome，无法渲染手绘画面（可设 SCENE_ART_BROWSER）")
    br = await pw.chromium.launch(executable_path=browser, headless=True, args=_BROWSER_ARGS)
    pg = await br.new_page(viewport={"width": w, "height": h}, device_scale_factor=1)
    errs: list[str] = []
    pg.on("pageerror", lambda e: errs.append(str(e)))
    await pg.goto(page_path.as_uri())
    await pg.evaluate("window.fontsReady")
    if errs:
        await br.close()
        raise RuntimeError(f"手绘画面脚本出错：{errs[0][:300]}")
    return br, pg


async def _render_part(page_path: Path, w: int, h: int, fps: int, f0: int, f1: int, out: Path,
                       counter: list[int]) -> Path:
    from playwright.async_api import async_playwright
    enc = subprocess.Popen(
        [config.FFMPEG, "-y", "-v", "error", "-f", "image2pipe", "-framerate", str(fps), "-c:v", "mjpeg",
         "-i", "-", "-vf", "scale=in_range=pc:out_range=tv,format=yuv420p", "-color_range", "tv",
         "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-r", str(fps), str(out)],
        stdin=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=_NO_WINDOW)
    try:
        async with async_playwright() as pw:
            br, pg = await _open(pw, page_path, w, h)
            try:
                for f in range(f0, f1):
                    await pg.evaluate(f"render({f / fps})")
                    enc.stdin.write(await pg.screenshot(type="jpeg", quality=92))
                    counter[0] += 1
            finally:
                await br.close()
        enc.stdin.close()
        err = enc.stderr.read().decode("utf-8", "replace")
        if enc.wait() != 0:
            raise RuntimeError(f"手绘画面编码失败：{err[-300:]}")
    except BaseException:
        enc.kill()
        raise
    return out


async def _render_all(page_path: Path, w: int, h: int, fps: int, frames: int, workers: int,
                      part_dir: Path, pct0: int, pct1: int) -> list[Path]:
    step = (frames + workers - 1) // workers
    counter = [0]
    jobs = [_render_part(page_path, w, h, fps, i * step, min(frames, (i + 1) * step),
                         part_dir / f"part_{i:02d}.mp4", counter)
            for i in range(workers) if i * step < frames]
    t0 = time.time()

    async def _report():
        while True:
            await asyncio.sleep(4)
            done = counter[0]
            el = time.time() - t0
            eta = (frames - done) / (done / el) if done else 0
            pct = pct0 + round((pct1 - pct0) * done / max(1, frames))
            log("compose", f"手绘动画逐帧渲染：{done}/{frames} 帧，约剩 {eta / 60:.1f} 分钟（{pct}%）")

    rep = asyncio.ensure_future(_report())
    try:
        return list(await asyncio.gather(*jobs))
    finally:
        rep.cancel()


def render_stills(page_path: Path, w: int, h: int, times: list[float], out_dir: Path) -> list[Path]:
    """导出几张静帧（检查画面用）。"""
    from playwright.async_api import async_playwright

    async def _go():
        out = []
        async with async_playwright() as pw:
            br, pg = await _open(pw, page_path, w, h)
            for t in times:
                await pg.evaluate(f"render({t})")
                p = out_dir / f"still_{t:07.2f}.png"
                await pg.screenshot(path=str(p))
                out.append(p)
            await br.close()
        return out
    out_dir.mkdir(parents=True, exist_ok=True)
    return asyncio.run(_go())


# ---------------------------------------------------------------- 主流程
def _hash(*parts) -> str:
    raw = json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha1(raw).hexdigest()[:12]


def _nvenc_ok() -> bool:
    from .compose import _nvenc_available
    return _nvenc_available()


def make_video(segs: list[dict], work_dir: Path, audio: Path, out_path: Path, *, ass: Path | None,
               width: int = 1920, height: int = 1080, topic: str = "", fps: int = 25,
               chapter_seconds: float = 60, concurrency: int = 6, time_budget_min: float = 15,
               workers: int = 6, bgm: bool = True, sfx: bool = True, engine_subs: bool = True,
               model: str | None = None, effort: str | None = None) -> dict:
    segs = [s for s in segs if str(s.get("zh") or "").strip()]
    if not segs:
        raise RuntimeError("没有可用的中文配音分段，无法生成手绘动画")
    model = model or config.HAND_DRAWN_MODEL
    effort = effort or config.HAND_DRAWN_EFFORT
    timeout = config.HAND_DRAWN_TIMEOUT
    hd = work_dir / "handdrawn"
    hd.mkdir(parents=True, exist_ok=True)
    lay = _layout(width, height)
    chapters_idx = split_chapters(segs, chapter_seconds=chapter_seconds)
    stages = [_stage(k, lay) for k in range(len(chapters_idx))]
    texts = ["".join(str(segs[i].get("zh") or "") for i in ids) for ids in chapters_idx]
    log("compose", f"手绘动画：全片分 {len(chapters_idx)} 章，模型 {model}/{effort}（2%）")

    # 1) 导演
    dkey = _hash(PLAN_VERSION, "director", topic, texts, width, height, model)
    dpath = hd / f"plan_director_{dkey}.json"
    director = load_json(dpath)
    if not director:
        log("compose", "手绘动画：Claude 正在定画风、人物和章节（3%）")
        try:
            director = plan_director(topic, texts, lay, model=model, effort=effort, timeout=timeout)
            if len(director.get("chapters") or []) < len(chapters_idx):
                raise ValueError("章节数不够")
            director["chapters"] = director["chapters"][:len(chapters_idx)]
            save_json(dpath, director)
        except Exception as e:  # noqa: BLE001
            log("compose", f"手绘动画：导演设计失败（{str(e)[:200]}），使用默认画风")
            director = _fallback_director(len(chapters_idx))
    actor_names = "、".join(f"{a.get('id')}({a.get('name', '')})" for a in director.get("actors") or [])
    log("compose", f"手绘动画：画风 {director.get('theme')}（{director.get('theme_reason', '')[:60]}），"
                   f"人物 {actor_names}（6%）")

    # 2) 分章画面指令（并发 + 总时限）
    plans: list[dict | None] = [None] * len(chapters_idx)
    todo = []
    for k, ids in enumerate(chapters_idx):
        key = _hash(PLAN_VERSION, "chapter", k, texts[k], director, stages[k], width, height, model, effort)
        p = hd / f"plan_ch{k:02d}_{key}.json"
        cached = load_json(p)
        if cached:
            plans[k] = cached
        else:
            todo.append((k, ids, p))
    if todo:
        log("compose", f"手绘动画：{len(todo)} 章需要写分镜（已缓存 {len(chapters_idx) - len(todo)} 章），"
                       f"并发 {concurrency}，时限 {time_budget_min:g} 分钟（8%）")
        deadline = time.time() + time_budget_min * 60
        done = [0]
        lock = threading.Lock()

        def _one(k, ids, path):
            if time.time() > deadline:
                return k, None, "超过时限"
            for attempt in range(2):
                try:
                    plan = plan_chapter(k, ids, segs, director, stages[k], lay, width, height,
                                        topic=topic, model=model, effort=effort, timeout=timeout)
                    save_json(path, plan)
                    return k, plan, ""
                except Exception as e:  # noqa: BLE001
                    err = str(e)[:200]
                    if time.time() > deadline:
                        break
            return k, None, err

        pool = ThreadPoolExecutor(max_workers=max(1, concurrency))
        futs = [pool.submit(_one, *job) for job in todo]
        try:
            for fut in as_completed(futs, timeout=max(1, deadline - time.time()) + 30):
                k, plan, err = fut.result()
                plans[k] = plan
                with lock:
                    done[0] += 1
                    pct = 8 + round(22 * done[0] / len(todo))
                    if plan:
                        log("compose", f"手绘动画：第 {k + 1} 章分镜完成（{done[0]}/{len(todo)}，{pct}%）")
                    else:
                        log("compose", f"手绘动画：第 {k + 1} 章分镜失败（{err}），用讲解员兜底（{pct}%）")
        except Exception:  # noqa: BLE001  —— 总时限到了，剩下的章节用兜底
            log("compose", "手绘动画：分镜总时限已到，未完成的章节用讲解员兜底")
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
    fallback = 0
    for k, ids in enumerate(chapters_idx):
        if not plans[k]:
            plans[k] = _fallback_chapter(k, ids, segs, stages[k], director)
            fallback += 1

    # 3) world + 时间轴 + 页面
    audio_dur = media_duration(audio) or float(segs[-1]["end"])
    duration = audio_dur + 2.0          # 片尾留 2 秒给引擎的淡出
    world, idmaps = build_world(director, plans, stages, lay)
    env = voice_envelope(audio, duration)
    tl = build_timeline(segs, chapters_idx, plans, director, world, idmaps, stages, duration=duration,
                        engine_subs=engine_subs, size=(width, height), voice_env=env)
    page = write_page(hd, world, tl)
    log("compose", f"手绘动画：时间轴 {duration / 60:.1f} 分钟，{len(tl['events'])} 个画面动作（31%）")

    # 4) 逐帧渲染
    frames = int(math.ceil(duration * fps))
    part_dir = hd / "parts"
    shutil.rmtree(part_dir, ignore_errors=True)
    part_dir.mkdir(parents=True)
    workers = max(1, min(int(workers), 12))
    log("compose", f"手绘动画：开始逐帧渲染 {frames} 帧（{fps} fps，{workers} 路并行）（32%）")
    t0 = time.time()
    parts = asyncio.run(_render_all(page, width, height, fps, frames, workers, part_dir, 32, 90))
    log("compose", f"手绘动画：渲染完成，用时 {(time.time() - t0) / 60:.1f} 分钟（90%）")

    # 5) 配乐 + 音效 + 配音
    from . import hand_drawn_audio
    mix_wav = hd / "mix.wav"
    levels = hand_drawn_audio.mix(tl, audio, mix_wav, bgm=bgm, sfx=sfx)
    log("compose", f"手绘动画：混音完成（人声 {levels['voice_db']} dB，BGM {levels['bgm_db']} dB）（93%）")

    # 6) 拼接 + 字幕 + 封装
    lst = part_dir / "parts.txt"
    lst.write_text("".join(f"file '{p.name}'\n" for p in parts), encoding="utf-8")
    cmd = [config.FFMPEG, "-y", "-hide_banner", "-f", "concat", "-safe", "0", "-i", str(lst),
           "-i", str(mix_wav)]
    if ass:
        from .compose import _escape_subs
        cmd += ["-vf", f"subtitles='{_escape_subs(ass)}'"]
        cmd += (["-c:v", "h264_nvenc", "-preset", "p5", "-cq", "20", "-pix_fmt", "yuv420p"] if _nvenc_ok()
                else ["-c:v", "libx264", "-preset", "medium", "-crf", "19", "-pix_fmt", "yuv420p"])
    else:
        cmd += ["-c:v", "copy"]
    cmd += ["-map", "0:v", "-map", "1:a", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
            "-t", f"{duration:.3f}", "-movflags", "+faststart", str(out_path)]
    run(cmd, desc="手绘画面 + 配乐配音" + (" + 字幕" if ass else "") + " 合成成片（96%）")
    shutil.rmtree(part_dir, ignore_errors=True)
    log("compose", f"输出完成：{out_path}")
    return {"chapters": len(chapters_idx), "fallback_chapters": fallback, "theme": world["THEME"],
            "actors": list(world["ACTORS"]), "events": len(tl["events"]), "frames": frames,
            "duration": round(duration, 2), "levels": levels}
