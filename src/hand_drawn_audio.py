"""手绘动画模式的配乐（程序合成，无版权问题）+ 音效 + 配音混音。

移植自 hand-drawn-story-video skill 的 src/audio.py：乐器 / BGM / 音效库原样保留，
主流程改为直接混第 4 步的整轨配音 dub.wav（不再逐句读 build/voice/*.wav）。
BGM：尼龙吉他分解和弦 + 铺底 + 手风琴旋律 + 沙锤，两种调式（story.META["music"]["preset"] = "minor" / "major"）。
     故事里用 `mood 名字` 切换配器（calm / warm / happy / tense / sad / dark / epic / silent），2 秒渐变。
     飓风（fx wind_start … restore）期间音乐自动被风声吞没。
"""
import subprocess, wave
from pathlib import Path
import numpy as np
from scipy.signal import butter, sosfilt
from scipy.ndimage import uniform_filter1d

from . import config
from .utils import _NO_WINDOW

SR = 48000
rng = np.random.default_rng(7)


def T(d): return np.arange(int(d * SR)) / SR
def midi(m): return 440 * 2 ** ((m - 69) / 12)
def bp(x, lo, hi, o=2): return sosfilt(butter(o, [lo, hi], btype="band", fs=SR, output="sos"), x)
def hp(x, f, o=2): return sosfilt(butter(o, f, btype="high", fs=SR, output="sos"), x)
def lp(x, f, o=2): return sosfilt(butter(o, f, btype="low", fs=SR, output="sos"), x)
def env(n, a, d):
    t = np.arange(n) / SR
    return np.minimum(1, t / max(a, 1e-4)) * np.exp(-t / d)


def add(buf, sig, at, g=1.0):
    i = int(at * SR)
    if i >= len(buf) or i + len(sig) <= 0: return
    if i < 0: sig, i = sig[-i:], 0
    n = min(len(sig), len(buf) - i)
    buf[i:i + n] += g * sig[:n]


# ─────────────── 乐器 ───────────────
def pluck(freq, dur=1.8, bright=.5, g=.996):
    """Karplus-Strong 拨弦（用 IIR 梳状滤波实现，很快）"""
    N = max(2, int(SR / freq))
    n = int(dur * SR)
    burst = rng.uniform(-1, 1, N)
    burst = lp(burst, 1500 + 5000 * bright, 1)
    # y[i] = g/2 * (y[i-N] + y[i-N-1])，按长度 N 的块向量化
    y = np.zeros(n + N + 1)
    y[1:N + 1] = burst                      # 前面垫一个 0，方便取 y[i-N-1]
    for s in range(N + 1, n + N + 1, N):
        e = min(s + N, n + N + 1)
        y[s:e] = g / 2 * (y[s - N:e - N] + y[s - N - 1:e - N - 1])
    y = hp(y[1:n + 1], 80)
    return y * env(n, .002, dur * .45) / (np.abs(y).max() + 1e-9)


def pad(freqs, dur):
    t = T(dur)
    s = np.zeros_like(t)
    for f in freqs:
        for det in (-.6, .6):
            s += np.sin(2 * np.pi * (f + det) * t + rng.uniform(0, 6)) + .25 * np.sin(2 * np.pi * 2 * (f + det) * t)
    s = lp(s, 900)
    fade = np.minimum(1, t / .8) * np.minimum(1, (dur - t) / .8)
    return s * fade / (len(freqs) * 2)


def reed(freq, dur):
    """手风琴：几组略微跑调的簧片（锯齿波）+ 风箱颤音"""
    t = T(dur)
    vib = 1 + .004 * np.sin(2 * np.pi * 5.2 * t)
    s = np.zeros_like(t)
    for det in (0, 2.2, -1.8):
        ph = np.cumsum(2 * np.pi * (freq + det) * vib / SR)
        s += 2 * ((ph / (2 * np.pi)) % 1) - 1
    s = bp(s, 300, 3200)
    e = np.minimum(1, t / .06) * np.minimum(1, (dur - t) / .12) * (1 + .12 * np.sin(2 * np.pi * 1.3 * t))
    return s * e / 3


def shaker():
    n = rng.standard_normal(int(.09 * SR))
    return hp(n, 5000) * env(len(n), .01, .025)


def horn(freq, dur):
    """铜号（古罗马的 cornu / tuba）：带一点滑音的锯齿波，低通，起音慢"""
    t = T(dur)
    glide = 1 - .02 * np.exp(-t / .06)
    vib = 1 + .003 * np.sin(2 * np.pi * 4.6 * t) * np.minimum(1, t / .4)
    s = np.zeros_like(t)
    for mult, g in ((1, 1), (.5, .45)):
        ph = np.cumsum(2 * np.pi * freq * mult * glide * vib / SR)
        s += g * (2 * ((ph / (2 * np.pi)) % 1) - 1)
    s = lp(s, 1500, 2)
    e = np.minimum(1, t / .14) * np.minimum(1, (dur - t) / .2)
    return s * e * .5


def drum(strong=True):
    """战鼓：音高下坠的低频 + 一点皮面噪声"""
    d = .55 if strong else .35
    t = T(d); f = 52 + 70 * np.exp(-t / .04)
    body = np.sin(2 * np.pi * np.cumsum(f) / SR) * env(len(t), .002, .18 if strong else .1)
    skin = bp(rng.standard_normal(len(t)), 150, 1200) * env(len(t), .001, .03)
    return (body + .35 * skin) * (1 if strong else .55)


# ─────────────── BGM ───────────────
BPM = 76
BEAT = 60 / BPM
BAR = 3 * BEAT
# 每小节：和弦（midi 音），根音
PROG_A = [("Dm", [50, 57, 62, 65, 69]), ("Dm", [50, 57, 62, 65, 69]), ("Gm", [43, 55, 58, 62, 67]), ("A7", [45, 57, 61, 64, 67]),
          ("Dm", [50, 57, 62, 65, 69]), ("Bb", [46, 53, 58, 62, 65]), ("Gm", [43, 55, 58, 62, 67]), ("A7", [45, 52, 57, 61, 64])]
PROG_B = [("Bb", [46, 53, 58, 62, 65]), ("F", [41, 53, 57, 60, 65]), ("Gm", [43, 55, 58, 62, 67]), ("Dm", [50, 57, 62, 65, 69]),
          ("Bb", [46, 53, 58, 62, 65]), ("F", [41, 53, 57, 60, 65]), ("Gm", [43, 55, 58, 62, 67]), ("A7", [45, 52, 57, 61, 64])]
# 手风琴旋律（每小节若干 (音, 拍数)）
MEL_A = [[(69, 2), (65, 1)], [(64, 1), (62, 1), (64, 1)], [(65, 2), (67, 1)], [(64, 3)],
         [(69, 1), (70, 1), (69, 1)], [(65, 2), (62, 1)], [(67, 1), (65, 1), (64, 1)], [(64, 2), (61, 1)]]
MEL_B = [[(70, 2), (69, 1)], [(69, 2), (65, 1)], [(67, 1), (65, 1), (67, 1)], [(69, 3)],
         [(70, 1), (72, 1), (70, 1)], [(69, 2), (65, 1)], [(67, 1), (65, 1), (64, 1)], [(62, 2), (64, 1)]]
ARP = [0, 2, 3, 4, 3, 2]   # 每小节 6 个八分音符的分解顺序
# 大调（G 大调，明亮、轻快）
MAJ_A = [("G", [43, 55, 59, 62, 67]), ("D", [50, 57, 62, 66, 69]), ("Em", [40, 55, 59, 64, 67]), ("C", [48, 55, 60, 64, 67]),
         ("G", [43, 55, 59, 62, 67]), ("C", [48, 55, 60, 64, 67]), ("D", [50, 57, 62, 66, 69]), ("G", [43, 55, 59, 62, 67])]
MAJ_B = [("C", [48, 55, 60, 64, 67]), ("G", [43, 55, 59, 62, 67]), ("Am", [45, 57, 60, 64, 69]), ("D", [50, 57, 62, 66, 69]),
         ("C", [48, 55, 60, 64, 67]), ("G", [43, 55, 59, 62, 67]), ("D", [50, 57, 62, 66, 69]), ("D", [50, 57, 62, 66, 69])]
MMEL_A = [[(71, 2), (74, 1)], [(74, 1), (72, 1), (71, 1)], [(71, 2), (67, 1)], [(69, 3)],
          [(71, 1), (72, 1), (74, 1)], [(76, 2), (74, 1)], [(72, 1), (71, 1), (69, 1)], [(67, 3)]]
MMEL_B = [[(72, 2), (76, 1)], [(74, 2), (71, 1)], [(72, 1), (71, 1), (69, 1)], [(66, 3)],
          [(72, 2), (76, 1)], [(74, 2), (71, 1)], [(69, 1), (71, 1), (72, 1)], [(74, 3)]]
PRESETS = {"minor": (PROG_A, PROG_B, MEL_A, MEL_B), "major": (MAJ_A, MAJ_B, MMEL_A, MMEL_B)}


def bgm_stems(dur, preset="minor", bpm=76, instr="latin"):
    """instr：latin = 尼龙吉他 + 手风琴 + 沙锤（默认）；ancient = 里拉琴 + 铜号 + 战鼓（古代史、帝国、战争）"""
    global BPM, BEAT, BAR
    if preset == "guofeng": return bgm_guofeng(dur, bpm)
    BPM = bpm; BEAT = 60 / BPM; BAR = 3 * BEAT
    PA, PB, MA, MB = PRESETS.get(preset, PRESETS["minor"])
    n = int(dur * SR) + SR * 4
    st = {k: np.zeros(n) for k in ("guitar", "bass", "pad", "accordion", "shaker")}
    nbars = int(dur / BAR) + 2
    for b in range(nbars):
        phrase = (b // 8) % 4                    # A A B A 循环
        prog = PB if phrase == 2 else PA
        mel = MB if phrase == 2 else MA
        name, ch = prog[b % 8]
        t0 = b * BAR
        # 吉他分解（带一点人味：时值和力度的小抖动）
        for k, idx in enumerate(ARP):
            f = midi(ch[idx] + (12 if idx >= 3 and k == 3 else 0))
            at = t0 + k * BEAT / 2 + rng.normal(0, .008)
            if instr == "ancient":   # 里拉琴：高一个八度、更亮、余音更长
                add(st["guitar"], pluck(f * 2, 2.4, .65 + .2 * rng.random(), .9975), at, (.42 if k == 0 else .3) * (.85 + .3 * rng.random()))
            else:
                add(st["guitar"], pluck(f, 1.6, .35 + .2 * rng.random()), at, (.55 if k == 0 else .38) * (.85 + .3 * rng.random()))
        add(st["bass"], pluck(midi(ch[0] - 12 if ch[0] > 45 else ch[0]), 2.2, .2, .997), t0, .7)
        add(st["pad"], pad([midi(m) for m in ch[1:4]], BAR + .9), t0 - .2, 1)
        beat = 0
        for m, d in mel[b % 8]:
            if instr == "ancient": add(st["accordion"], horn(midi(m - 12), d * BEAT * .98), t0 + beat * BEAT, .55)
            else: add(st["accordion"], reed(midi(m), d * BEAT * .95), t0 + beat * BEAT, .5)
            beat += d
        if instr == "ancient":   # 战鼓：每小节一重两轻，第 8 小节加花
            add(st["shaker"], drum(True), t0, .9); add(st["shaker"], drum(False), t0 + BEAT * 2, .7)
            if b % 8 == 7: add(st["shaker"], drum(False), t0 + BEAT * 2.5, .6); add(st["shaker"], drum(False), t0 + BEAT * 1, .5)
        else:
            for k in range(6):
                add(st["shaker"], shaker(), t0 + k * BEAT / 2, .5 if k % 2 == 0 else .3)
    # 轻微混响（多抽头延迟）让吉他不那么干
    for k in ("guitar", "accordion"):
        x = st[k]; wet = np.zeros_like(x)
        for d, g in ((.031, .35), (.057, .28), (.089, .22), (.137, .16), (.211, .1)):
            i = int(d * SR); wet[i:] += g * x[:-i]
        st[k] = x + lp(wet, 3500)
    return {k: v[:int(dur * SR)] for k, v in st.items()}


# ─────────────── 国风（五声音阶：古筝 + 笛箫 + 弦乐铺底 + 堂鼓） ───────────────
# 和其他预设共用同一套分轨名，混音和 mood 逻辑不用改：guitar=古筝，accordion=笛/箫，shaker=堂鼓
GF_SCALE = [62, 64, 66, 69, 71]                    # D 宫调：D E F# A B
def gf_note(i):                                    # 第 i 个五声音级（可以跨八度）
    return GF_SCALE[i % 5] + 12 * (i // 5) - 12
GF_ROOTS = [50, 47, 43, 45, 50, 47, 45, 50]        # 每小节的根音：D Bm G A D Bm A D
GF_MEL_A = [[(9, 1), (10, 1), (11, 2)], [(13, 1.5), (12, .5), (11, 2)], [(10, 1), (9, 1), (7, 2)], [(9, 4)],
            [(11, 1), (12, 1), (13, 1), (12, 1)], [(11, 2), (9, 2)], [(10, 1), (8, 1), (7, 2)], [(5, 4)]]
GF_MEL_B = [[(12, 2), (13, 1), (14, 1)], [(15, 3), (13, 1)], [(12, 1), (11, 1), (10, 2)], [(11, 4)],
            [(13, 1), (12, 1), (10, 1), (9, 1)], [(10, 2), (12, 2)], [(11, 1), (10, 1), (9, 2)], [(10, 4)]]
GF_ARP = [0, 2, 4, 5, 7, 5, 4, 2]                  # 古筝分解：八个八分音符，按五声音级往上再回来


def zheng(freq, dur=2.2, bright=.75):
    """古筝：亮一点的拨弦 + 揉弦"""
    y = pluck(freq, dur, bright, .9965)
    t = np.arange(len(y)) / SR
    vib = 1 + .004 * np.sin(2 * np.pi * 5.5 * t) * np.clip((t - .25) / .4, 0, 1)
    idx = np.clip(np.cumsum(vib), 0, len(y) - 1)
    return np.interp(idx, np.arange(len(y)), y)


def dizi(freq, dur, breath=.22):
    """笛/箫：正弦 + 谐波 + 气声，带颤音和一点滑音"""
    t = T(dur)
    vib = 1 + .006 * np.sin(2 * np.pi * 5.0 * t) * np.clip((t - .15) / .3, 0, 1)
    glide = 1 - .02 * np.exp(-t / .05)
    ph = np.cumsum(2 * np.pi * freq * vib * glide / SR)
    s = np.sin(ph) + .3 * np.sin(2 * ph) + .08 * np.sin(3 * ph)
    s += breath * bp(rng.standard_normal(len(t)), freq * .8, min(freq * 4, 9000)) * .6
    e = np.minimum(1, t / .07) * np.minimum(1, (dur - t) / .15)
    return s * e * .35


def tanggu(g=1.0):
    """堂鼓：低沉的鼓皮 + 一点拍击声"""
    t = T(.9); f = 70 + 60 * np.exp(-t / .04)
    body = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t / .28)
    slap = bp(rng.standard_normal(len(t)), 300, 2500) * np.exp(-t / .015) * .4
    return (body + slap) * g * .8


def bgm_guofeng(dur, bpm=72):
    beat = 60 / bpm; bar = 4 * beat
    n = int(dur * SR) + SR * 4
    st = {k: np.zeros(n) for k in ("guitar", "bass", "pad", "accordion", "shaker")}
    for b in range(int(dur / bar) + 2):
        phrase = (b // 8) % 4
        mel = GF_MEL_B if phrase == 2 else GF_MEL_A
        root = GF_ROOTS[b % 8]; t0 = b * bar
        base = {50: 5, 47: 4, 43: 3, 45: 3}.get(root, 5)     # 分解从根音附近的五声音级开始
        for k, step in enumerate(GF_ARP):
            at = t0 + k * beat / 2 + rng.normal(0, .01)
            add(st["guitar"], zheng(midi(gf_note(base + step)), 2.0, .65 + .2 * rng.random()), at, (.5 if k == 0 else .32) * (.85 + .3 * rng.random()))
        if b % 4 == 3:   # 每四小节一次刮奏（从高往低）
            for k in range(10): add(st["guitar"], zheng(midi(gf_note(17 - k)), 1.2, .9), t0 + 3 * beat + k * .045, .2)
        add(st["bass"], zheng(midi(root - 12), 2.8, .25), t0, .75)
        add(st["bass"], zheng(midi(root - 12 + 7), 2.0, .25), t0 + 2 * beat, .4)
        add(st["pad"], pad([midi(root), midi(root + 7), midi(root + 12 + (4 if root in (50, 43) else 3))], bar + .9), t0 - .2, 1)
        beat_i = 0
        for i, d in mel[b % 8]:
            add(st["accordion"], dizi(midi(gf_note(i)), d * beat * .96), t0 + beat_i * beat, .55)
            beat_i += d
        add(st["shaker"], tanggu(1), t0, .9); add(st["shaker"], tanggu(.55), t0 + 2.5 * beat, .7)
        if b % 2: add(st["shaker"], tanggu(.7), t0 + 3 * beat, .6)
    for k in ("guitar", "accordion"):   # 混响：大一点的厅
        x = st[k]; wet = np.zeros_like(x)
        for d, g in ((.043, .4), (.077, .32), (.121, .26), (.187, .2), (.263, .14), (.371, .1)):
            i = int(d * SR); wet[i:] += g * x[:-i]
        st[k] = x + lp(wet, 4000)
    return {k: v[:int(dur * SR)] for k, v in st.items()}


def ramp_curve(n, points):
    """points: [(t, gain)]，线性插值 → 每个采样的增益"""
    ts = np.array([p[0] for p in points]); gs = np.array([p[1] for p in points])
    return np.interp(np.arange(n) / SR, ts, gs)


# ─────────────── 音效 ───────────────
def sfx_bank():
    b = {}
    def scribble(d, bright=1.0, seed=0):
        r = np.random.default_rng(seed)
        n = r.standard_normal(int(d * SR)); t = np.arange(len(n)) / SR
        am = np.abs(lp(r.standard_normal(len(n)), 18)) * 6          # 笔尖一顿一顿
        s = bp(n, 1800 * bright, 7000) * np.clip(am, 0, 1.4)
        return s * np.minimum(1, t / .03) * np.minimum(1, (d - t) / .08) * .5
    b["scribble"] = [scribble(.55, 1, i) for i in range(4)]
    b["scribble_long"] = [scribble(1.1, .9, 10 + i) for i in range(2)]
    b["squeak"] = [scribble(.6, 1.4, 20 + i) * 1.2 for i in range(2)]
    n = rng.standard_normal(int(.9 * SR)); t = np.arange(len(n)) / SR
    b["whoosh"] = bp(n, 250, 2500) * np.sin(np.pi * np.clip(t / .9, 0, 1)) ** 2 * .5
    n = rng.standard_normal(int(.3 * SR)); t = np.arange(len(n)) / SR
    b["flip"] = bp(n, 1500, 9000) * np.sin(np.pi * t / .3) ** 3 * .4
    t = T(.12); f = 420 + 700 * t / .12
    b["pop"] = np.sin(2 * np.pi * np.cumsum(f) / SR) * env(len(t), .002, .035) * .5
    t = T(2.6)   # 去世：低沉的钟
    bell = sum(a * np.sin(2 * np.pi * 196 * r * t) * np.exp(-t / (1.4 / r ** .5)) for r, a in ((1, 1), (2.01, .5), (2.76, .35), (5.4, .15)))
    b["bell"] = bell * np.minimum(1, t / .004) * .28
    t = T(.5); f = 38 + 90 * np.exp(-t / .05)
    b["thud"] = (np.sin(2 * np.pi * np.cumsum(f) / SR) * env(len(t), .002, .16) + .3 * lp(rng.standard_normal(len(t)), 900) * env(len(t), .001, .03)) * .7
    def gun():
        t = T(.9); n = rng.standard_normal(len(t))
        crack = bp(n, 700, 6000) * env(len(t), .0005, .025)
        boom = lp(n, 400) * env(len(t), .002, .18) * 3
        tail = lp(n, 1500) * env(len(t), .01, .35) * .25
        return (crack + boom + tail) * .45
    b["gun"] = gun()
    v = np.zeros(int(2.2 * SR))
    for k, at in enumerate([0, .23, .31, .55, .7, .98, 1.1]): add(v, gun(), at, .8 - k * .07)
    b["guns"] = v
    t = T(.35); n = rng.standard_normal(len(t))
    b["stab"] = bp(n, 800, 5000) * np.sin(np.pi * t / .35) ** 2 * .35
    t = T(2.4)   # 火车汽笛（两音）+ 车轮声
    vib = 1 + .006 * np.sin(2 * np.pi * 6 * t)
    wh = (np.sin(2 * np.pi * 523 * vib * t) + .8 * np.sin(2 * np.pi * 659 * vib * t) + .3 * np.sin(2 * np.pi * 1046 * vib * t))
    wh = wh * np.minimum(1, t / .15) * np.minimum(1, (1.5 - t).clip(0) / .3) * .12
    ch = np.zeros(len(t))
    for k in range(12): add(ch, bp(rng.standard_normal(int(.12 * SR)), 200, 1500) * env(int(.12 * SR), .005, .04), .1 + k * .19, .35)
    b["train"] = wh + ch
    n = rng.standard_normal(int(.22 * SR)); tt = np.arange(len(n)) / SR   # 换姿势的衣服摆动声
    b["swish"] = bp(n, 1500, 8000) * np.sin(np.pi * tt / .22) ** 2 * .3
    t = T(.09)   # 脚步
    b["step"] = [lp(rng.standard_normal(len(t)), 700 + 200 * k) * env(len(t), .002, .02) * .6 for k in range(3)]
    t = T(.1); f = 900 + 500 * np.exp(-t / .02)
    b["blip"] = np.sin(2 * np.pi * np.cumsum(f) / SR) * env(len(t), .003, .04) * .35
    t = T(1.2)
    b["chime"] = sum(np.sin(2 * np.pi * f0 * t) * np.exp(-t / .5) for f0 in (1568, 2093, 2637)) * np.minimum(1, t / .003) * .12
    t = T(.45); f = 180 + 520 * t / .45 + 60 * np.sin(2 * np.pi * 14 * t)
    b["boing"] = np.sin(2 * np.pi * np.cumsum(f) / SR) * env(len(t), .004, .18) * .4
    # ── 古代 / 战争音效 ──

    def clang(d=1.0, base=820):    # 金属撞击：几个不成谐波的正弦 + 噪声
        t = T(d)
        s = sum(a * np.sin(2 * np.pi * base * r * t) * np.exp(-t / (.35 / r ** .6)) for r, a in ((1, 1), (1.47, .7), (2.09, .55), (2.94, .4), (4.1, .25)))
        return (s * .25 + bp(rng.standard_normal(len(t)), 2000, 9000) * env(len(t), .0005, .02)) * np.minimum(1, t / .001)
    v = np.zeros(int(1.6 * SR))
    for k, (at, bs) in enumerate([(0, 820), (.18, 1010), (.42, 760), (.55, 930)]): add(v, clang(1.0, bs), at, .8 - k * .12)
    b["clash"] = v * .55
    t = T(2.2)   # 号角：两个音
    hn = np.zeros(len(t))
    add(hn, horn(midi(50), 1.0), 0, 1); add(hn, horn(midi(57), 1.15), .95, 1.1)
    b["horn"] = hn * .9
    v = np.zeros(int(2.4 * SR))
    for k, at in enumerate([0, .38, .76, .95, 1.14, 1.52]): add(v, drum(k % 3 == 0), at, .9)
    b["drums"] = v * .8
    d = 2.8; t = T(d)   # 火：低沉的呼呼声 + 噼啪
    fire = lp(rng.standard_normal(len(t)), 500) * .5 * (1 + .3 * np.sin(2 * np.pi * .7 * t))
    for _ in range(60): add(fire, bp(rng.standard_normal(int(.012 * SR)), 1500, 7000) * env(int(.012 * SR), .0005, .003), rng.uniform(0, d), rng.uniform(.3, 1))
    b["fire"] = fire * np.minimum(1, t / .4) * np.minimum(1, (d - t) / .8) * .5
    d = 2.6; t = T(d)   # 人群欢呼
    crowd = sum(bp(rng.standard_normal(len(t)), lo, hi) for lo, hi in ((300, 900), (700, 1800), (1500, 3200)))
    crowd *= (1 + .4 * np.sin(2 * np.pi * 3.1 * t + 1) * np.sin(2 * np.pi * 1.3 * t)) * np.sin(np.pi * np.clip(t / d, 0, 1)) ** .7
    b["crowd"] = crowd * .3
    d = 2.8; t = T(d); f = 34 + 60 * np.exp(-t / .08)   # 大炮
    boom = np.sin(2 * np.pi * np.cumsum(f) / SR) * env(len(t), .002, .5) + lp(rng.standard_normal(len(t)), 700) * env(len(t), .001, .35) * .8
    b["cannon"] = (boom + bp(rng.standard_normal(len(t)), 1000, 6000) * env(len(t), .0005, .03) * .6) * .75
    # ── 国风画面用的音效 ──
    t = T(4.0)   # 锣：一组不谐和的泛音，音高慢慢往下沉
    drop = 1 - .02 * (1 - np.exp(-t / .8)); f0 = 110
    gong = sum(a * np.sin(2 * np.pi * f0 * r * drop * t + r) * np.exp(-t / dd) for r, a, dd in ((1, 1, 2.6), (1.48, .6, 1.8), (2.03, .45, 1.4), (2.74, .3, 1.0), (3.6, .2, .7)))
    b["gong"] = (gong + .3 * bp(rng.standard_normal(len(t)), 300, 3000) * np.exp(-t / .05)) * np.minimum(1, t / .01) * .16
    b["drum"] = tanggu(1.0) * .9
    def brush(d, seed):
        r = np.random.default_rng(seed); n = r.standard_normal(int(d * SR)); tt = np.arange(len(n)) / SR
        return bp(n, 900, 4500) * np.sin(np.pi * np.clip(tt / d, 0, 1)) ** 1.5 * .35
    b["brush"] = [brush(.45, 30 + i) for i in range(4)]
    def rustle(d, seed):
        r = np.random.default_rng(seed); n = r.standard_normal(int(d * SR)); tt = np.arange(len(n)) / SR
        crack = (r.random(len(n)) < .004) * r.standard_normal(len(n)) * 3
        return (bp(n, 1500, 7000) * .5 + hp(crack, 2000)) * np.sin(np.pi * np.clip(tt / d, 0, 1)) * .3
    b["rustle"] = rustle(1.1, 41)
    t = T(.25); b["stamp"] = (lp(rng.standard_normal(len(t)), 500) * np.exp(-t / .03) * .8 + np.sin(2 * np.pi * 90 * t) * np.exp(-t / .06)) * .55
    t = T(.12); b["wood"] = np.sin(2 * np.pi * 1200 * t) * np.exp(-t / .025) * .3
    t = T(1.4); nn = rng.standard_normal(len(t))
    b["ink"] = (bp(nn, 120, 900) * np.sin(np.pi * np.clip(t / 1.4, 0, 1)) ** 2 * .7 + np.sin(2 * np.pi * 55 * t) * np.exp(-t / .5) * .5) * .6
    return b


def noise_bed(dur, lo, hi, seed):
    r = np.random.default_rng(seed)
    return bp(r.standard_normal(int(dur * SR)), lo, hi)


# ─────────────── 主流程 ───────────────
def read_voice(path: Path) -> np.ndarray:
    """任意格式配音 → 48k 单声道 float32。"""
    out = subprocess.run([config.FFMPEG, "-v", "error", "-i", str(path), "-ac", "1", "-ar", str(SR),
                          "-f", "s16le", "-"], capture_output=True, creationflags=_NO_WINDOW)
    if out.returncode != 0:
        raise RuntimeError(f"读取配音失败：{out.stderr.decode('utf-8', 'replace')[-300:]}")
    return np.frombuffer(out.stdout, np.int16).astype(np.float32) / 32768


def save_wav(path: Path, x: np.ndarray) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes((np.clip(x, -1, 1) * 32767).astype("<i2").tobytes())


MOODS = {"calm": dict(guitar=.8, bass=.4, pad=.5, accordion=0, shaker=0),
         "warm": dict(guitar=.85, bass=.55, pad=.5, accordion=.55, shaker=0),
         "happy": dict(guitar=1, bass=.7, pad=.4, accordion=.7, shaker=.55),
         "tense": dict(guitar=.3, bass=.75, pad=1, accordion=0, shaker=0),
         "sad": dict(guitar=.45, bass=.3, pad=.9, accordion=.25, shaker=0),
         "dark": dict(guitar=.2, bass=.3, pad=1, accordion=0, shaker=0),
         "epic": dict(guitar=1, bass=.7, pad=.7, accordion=.8, shaker=.3),
         "silent": dict(guitar=0, bass=0, pad=0, accordion=0, shaker=0)}
MOODS["festive"] = MOODS["happy"]; MOODS["full"] = MOODS["epic"]
MOODS_GUOFENG = dict(MOODS, **{
    "calm": dict(guitar=.8, bass=.45, pad=.5, accordion=0, shaker=0),
    "warm": dict(guitar=.85, bass=.55, pad=.5, accordion=.6, shaker=0),
    "happy": dict(guitar=1, bass=.6, pad=.4, accordion=.7, shaker=.25),
    "tense": dict(guitar=.25, bass=.8, pad=1, accordion=0, shaker=.55),
    "sad": dict(guitar=.4, bass=.3, pad=.9, accordion=.45, shaker=0),
    "dark": dict(guitar=.15, bass=.35, pad=1, accordion=0, shaker=.3),
    "epic": dict(guitar=1, bass=.75, pad=.75, accordion=.7, shaker=.7)})
FX_GAIN = {"gun": .85, "guns": .9, "stab": .8, "thud": .8, "train": .9, "gong": .9, "drum": .9, "clash": .6,
           "horn": .5, "drums": .7, "fire": .5, "crowd": .45, "cannon": .95}


def _music(tl: dict, N: int, active: np.ndarray) -> np.ndarray:
    D, ev = float(tl["duration"]), tl["events"]
    music_cfg = tl.get("meta", {}).get("music", {})
    moods = MOODS_GUOFENG if music_cfg.get("preset") == "guofeng" else MOODS
    st = bgm_stems(D, music_cfg.get("preset", "minor"), music_cfg.get("bpm", 76), music_cfg.get("instr", "latin"))
    marks = [(0, music_cfg.get("mood", "warm"))]
    marks += [(e["t"], e["act"].split()[1]) for e in ev if e["act"].startswith("mood ") and len(e["act"].split()) > 1]
    marks.sort()
    music = np.zeros(N)
    for stem in ("guitar", "bass", "pad", "accordion", "shaker"):
        if stem not in st:
            continue
        pts = [(0, moods.get(marks[0][1], moods["warm"])[stem])]
        for tm, md in marks[1:]:
            pts += [(tm, pts[-1][1]), (tm + 2.0, moods.get(md, moods["warm"])[stem])]
        pts += [(max(0, D - 4), pts[-1][1]), (D, 0)]
        x = np.zeros(N)
        x[:min(N, len(st[stem]))] = st[stem][:N]
        music += x * ramp_curve(N, pts) * (1.2 if stem == "pad" else .6 if stem == "shaker" else 1)
    music = music / (np.abs(music).max() + 1e-9)
    duck = uniform_filter1d(active, int(.25 * SR))
    duck = 1 - .62 * np.clip(duck * 1.2, 0, 1)
    fade_in = np.minimum(1, np.arange(N) / SR / 1.2)
    return music * duck * fade_in


def _effects(tl: dict, N: int) -> np.ndarray:
    D, ev = float(tl["duration"]), tl["events"]
    gf = tl.get("meta", {}).get("music", {}).get("preset") == "guofeng"
    b = sfx_bank()
    fx = np.zeros(N)
    last = {}

    def put(name, t, g, gap=.12):
        if name not in b or t - last.get(name, -9) < gap:
            return
        last[name] = t
        s = b[name]
        if isinstance(s, list): s = s[int(t * 7) % len(s)]
        if name == "step": last.pop(name, None)
        add(fx, s, t, g)

    for e in ev:
        parts = e["act"].split()
        if not parts:
            continue
        v, t = parts[0], e["t"]
        arg = parts[1] if len(parts) > 1 else ""
        if v in ("card", "ev"): put("scribble", t + .02, .22, .25)
        elif v == "group": put("scribble_long", t, .12, .5)
        elif v == "edge": put("scribble", t + .05, .16, .3)
        elif v == "icon": put("pop", t, .5, .2); put("scribble", t + .05, .14, .3)
        elif v == "note": put("brush", t, .4, .25) if gf else put("scribble", t, .16, .3)
        elif v == "hl": put("squeak", t, .22, .3)
        elif v in ("cam", "camz"): put("whoosh", t - .1, .22, 1.2)
        elif v == "hud": put("gong", t + .1, .8, .5) if gf else put("flip", t, .5, .5)
        elif v == "dead": put("bell", t + .05, .7, .5)
        elif v == "swap": put("whoosh", t, .35, .3)
        elif v == "actor": put("pop", t, .45, .2)
        elif v in ("walk", "run"):
            walk = v == "walk"
            for k in range(int((2.2 if walk else 1.4) / (.36 if walk else .2))):
                put("step", t + k * (.36 if walk else .2), .35 if walk else .45, .05)
        elif v == "jump": put("boing", t, .5, .3)
        elif v in ("say", "think"): put("wood", t, .6, .2) if gf else put("blip", t, .5, .2)
        elif v == "emote":
            soft = arg in ("heart", "sparkle", "music")
            put("chime" if soft else "pop", t, .5 if soft else .35, .2)
        elif v == "pose": put("swish", t, .22, .4)
        elif v == "leave": put("whoosh", t, .2, .5)
        elif v == "fx" and arg in b: put(arg, t, FX_GAIN.get(arg, .7), .1)
    rain_on = next((e["t"] for e in ev if e["act"] == "rain on"), None)
    if rain_on is not None and rain_on < D:
        rain_off = next((e["t"] for e in ev if e["act"] == "rain off" and e["t"] > rain_on), D)
        dur = max(1.0, min(D - rain_on, rain_off - rain_on + 3))
        rain = noise_bed(dur, 400, 7000, 3) * .35 + noise_bed(dur, 80, 400, 4) * .25
        tt = np.arange(len(rain)) / SR
        rain *= np.minimum(1, tt / 1.5) * np.clip((dur - tt) / 2.5, 0, 1) * .5
        add(fx, rain, rain_on)
    return fx


def mix(tl: dict, voice_path: Path, out_path: Path, *, bgm: bool = True, sfx: bool = True) -> dict:
    """按时间轴 tl（lines / events / meta / duration）合成 BGM + 音效，与整轨配音混音写到 out_path。

    配音原样使用（不改响度），BGM 和音效按人声实际电平定音量：比例与 skill 原版一致（原版把人声拉到 RMS 0.16，BGM 系数 .36）。
    """
    N = int(float(tl["duration"]) * SR)
    voice = np.zeros(N)
    x = read_voice(voice_path)[:N]
    voice[:len(x)] = x
    active = np.zeros(N)
    for ln in tl["lines"]:
        if not ln.get("silent"):
            a, b = int(ln["t0"] * SR), int((ln["t0"] + ln["dur"]) * SR)
            active[max(0, a):max(0, b)] = 1
    on = active > 0
    v_rms = float(np.sqrt(np.mean(voice[on] ** 2))) if on.any() else .1
    music = _music(tl, N, active) * max(.03, min(.6, v_rms * 2.9)) if bgm else np.zeros(N)
    fx = _effects(tl, N) * max(.15, min(1.5, v_rms * 6.25)) if sfx else np.zeros(N)
    out = voice + music + fx
    peak = float(np.abs(out).max())
    if peak > .98:
        out = np.tanh(out * 1.1 / peak) / np.tanh(1.1) * .95
    save_wav(out_path, out)
    db = lambda y: round(20 * np.log10(np.sqrt(np.mean(y ** 2)) + 1e-9), 1)
    return {"voice_db": db(voice[on]) if on.any() else None,
            "bgm_db": db(music[on]) if bgm and on.any() else None,
            "fx_db": db(fx) if sfx else None}
