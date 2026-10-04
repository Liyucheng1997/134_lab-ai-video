// 手绘画布渲染器：render(t) 画出第 t 秒的画面。所有动画都只由 t 和 build/timeline.js 决定（可逐帧重现）。
(() => {
const cv = document.getElementById("c");
const W = cv.width, H = cv.height;                        // 画幅来自 index.html（story.META["size"]），竖屏 1080×1920
const VERT = H > W, TOPY = VERT ? 220 : 0;                 // 竖屏：顶部 220px 是抖音“关注/推荐”区，章节标签和图例往下挪
const ctx = cv.getContext("2d");
const WD = window.WORLD;
// ── 画风（themes.js）：world.js 的 THEME 可以是预设名，也可以是 {base: 预设名, 要改的字段…} ──
const TH = (() => {
  const all = window.THEMES || {}, t = WD.THEME || "notebook";
  const baseName = typeof t === "string" ? t : t.base || "notebook";
  const base = all[baseName] || all.notebook || {}, over = typeof t === "object" ? t : {};
  const out = {name: baseName};
  for (const k of new Set([...Object.keys(base), ...Object.keys(over)])) {
    const a = base[k], b = over[k];
    out[k] = a && b && typeof a === "object" && typeof b === "object" && !Array.isArray(a) ? {...a, ...b} : b ?? a;
  }
  for (const k of ["fonts", "line", "fill", "card", "sticky", "sub", "hud", "actor", "bubble", "bg", "tex"]) out[k] = out[k] || {};
  return out;
})();
const C = Object.assign({paper: "#f5efe3", dot: "#d8cdb9", ink: "#2d2824", soft: "#6f665c", red: "#c23b2e", love: "#d6457a", gold: "#d9a52b",
  green: "#6f9a52", blue: "#5b9bd5", sky: "#cfe6f4", out: "#8c8579", orange: "#e07a2e", navy: "#2f6db5", purple: "#8a55b8",
  ja: "#e07a2e", au: "#2f6db5", wo: "#8a55b8", sea: "#cfe2e8", land: "#f6efdf", panel: "#fffaf0"}, TH.C || {}, WD.C || {});
const NODES = WD.NODES || {}, EDGES = WD.EDGES || {}, ICON_POS = WD.ICON_POS || {}, EVENTS = WD.EVENTS || {};
const GROUPS = WD.GROUPS || {}, NOTES = WD.NOTES || {}, AREAS = WD.AREAS || {};
const KINDS = WD.KINDS || {};                              // 卡片类型 -> {color, label}，也生成右上角小图例
const kindColor = k => (KINDS[k] && KINDS[k].color) || C[k] || C.out;
const TLM = Object.assign({x: 2000, y0: -130, y1: 2520, title: "时间线", ticks: []}, WD.TIMELINE_META || {});
const TL_X = TLM.x;
const WIND_KEEP = new Set(WD.WIND_KEEP || []);             // 飓风吹不走的元素
const TLN = window.TIMELINE || {duration: 10, events: [], subs: []};
const FT = TH.fonts;
const HAND = FT.hand || "LongCang, KaiTi, serif";
const KAI = FT.label || "KaiTi, STKaiti, serif";
const TITLE = FT.title || HAND;
const LATIN = FT.latin || TITLE;
const SUBFONT = FT.sub || "'Microsoft YaHei', 'PingFang SC', sans-serif";
// 字体串：fH 手写 / fL 标签 / fT 标题，带上画风规定的字重
const fH = s => `${FT.handW || ""} ${s}px ${HAND}`.trim();
const fL = s => `${FT.labelW || ""} ${s}px ${KAI}`.trim();
const fT = s => `${FT.titleW || ""} ${s}px ${TITLE}`.trim();
const LW = TH.line.mul ?? 1, LSTYLE = TH.line.style || "pen", LAMP = TH.line.amp ?? 1;

// ───────────── 工具 ─────────────
const clamp = (x, a = 0, b = 1) => Math.max(a, Math.min(b, x));
const ease = x => { x = clamp(x); return x < .5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2; };
const easeOut = x => 1 - Math.pow(1 - clamp(x), 3);
const lerp = (a, b, k) => a + (b - a) * k;
function hashStr(s) { let h = 2166136261; for (const ch of s) h = Math.imul(h ^ ch.charCodeAt(0), 16777619); return h >>> 0; }
function rng(seed) { let a = seed >>> 0; return () => { a |= 0; a = a + 0x6D2B79F5 | 0; let t = Math.imul(a ^ a >>> 15, 1 | a); t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t; return ((t ^ t >>> 14) >>> 0) / 4294967296; }; }
function hexA(hex, a) { const n = parseInt(hex.slice(1), 16); return `rgba(${n >> 16 & 255},${n >> 8 & 255},${n & 255},${a})`; }
function mix(h1, h2, k) {
  const a = parseInt(h1.slice(1), 16), b = parseInt(h2.slice(1), 16);
  const c = [16, 8, 0].map(s => Math.round(lerp(a >> s & 255, b >> s & 255, k)));
  return `rgb(${c[0]},${c[1]},${c[2]})`;
}

// 把折线重采样后加上低频抖动 → 手绘线
function sketch(points, seed, amp = 2.2, closed = false) {
  const r = rng(seed);
  amp *= LAMP;
  const pts = closed ? [...points, points[0]] : points;
  const out = [];
  for (let i = 0; i < pts.length - 1; i++) {
    const [x0, y0] = pts[i], [x1, y1] = pts[i + 1];
    const L = Math.hypot(x1 - x0, y1 - y0), n = Math.max(2, Math.ceil(L / 7));
    for (let k = 0; k < n; k++) out.push([lerp(x0, x1, k / n), lerp(y0, y1, k / n)]);
  }
  out.push(pts[pts.length - 1]);
  // 低频噪声：每 ~45px 一个随机偏移，中间平滑插值
  let acc = 0; const knots = [], step = 45;
  const total = out.reduce((s, p, i) => i ? s + Math.hypot(p[0] - out[i - 1][0], p[1] - out[i - 1][1]) : 0, 0);
  for (let k = 0; k <= total / step + 2; k++) knots.push([(r() - .5) * 2 * amp, (r() - .5) * 2 * amp]);
  const res = [];
  for (let i = 0; i < out.length; i++) {
    if (i) acc += Math.hypot(out[i][0] - out[i - 1][0], out[i][1] - out[i - 1][1]);
    const u = acc / step, k = Math.floor(u), f = u - k, s = f * f * (3 - 2 * f);
    const a = knots[k], b = knots[k + 1] || a;
    res.push([out[i][0] + lerp(a[0], b[0], s), out[i][1] + lerp(a[1], b[1], s)]);
  }
  return res;
}
function withLen(pts) {
  const cum = [0];
  for (let i = 1; i < pts.length; i++) cum.push(cum[i - 1] + Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]));
  return {pts, cum, len: cum[cum.length - 1]};
}
function bez(p0, p1, p2, p3, n = 40) {
  const o = [];
  for (let i = 0; i <= n; i++) {
    const t = i / n, u = 1 - t;
    o.push([u * u * u * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t * t * t * p3[0],
            u * u * u * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t * t * t * p3[1]]);
  }
  return o;
}
function quad(p0, c, p1, n = 40) { return bez(p0, [p0[0] + 2 / 3 * (c[0] - p0[0]), p0[1] + 2 / 3 * (c[1] - p0[1])], [p1[0] + 2 / 3 * (c[0] - p1[0]), p1[1] + 2 / 3 * (c[1] - p1[1])], p1, n); }
function ellipsePts(cx, cy, rx, ry, a0 = 0, a1 = Math.PI * 2, n = 48) {
  const o = []; for (let i = 0; i <= n; i++) { const a = lerp(a0, a1, i / n); o.push([cx + rx * Math.cos(a), cy + ry * Math.sin(a)]); } return o;
}
function roundRectPts(x, y, w, h, r) {
  const o = [], arc = (cx, cy, a0) => { for (let i = 0; i <= 6; i++) { const a = a0 + i / 6 * Math.PI / 2; o.push([cx + r * Math.cos(a), cy + r * Math.sin(a)]); } };
  arc(x + r, y + r, Math.PI); arc(x + w - r, y + r, -Math.PI / 2); arc(x + w - r, y + h - r, 0); arc(x + r, y + h - r, Math.PI / 2);
  return o;
}

// 画一条（部分）手绘线；世界坐标，线宽不低于屏幕 1.3px
let Z = 1;
function drawStroke(s, p = 1, alpha = 1) {
  if (p <= 0 || alpha <= 0) return;
  const {pts, cum, len} = s;
  const target = len * clamp(p);
  const w = Math.max(s.w * LW, 1.3 / Z);
  ctx.globalAlpha = alpha * (s.alpha ?? 1);
  ctx.strokeStyle = s.c;
  ctx.lineCap = "round"; ctx.lineJoin = "round";
  const path = (ox = 0, oy = 0) => {
    ctx.beginPath(); ctx.moveTo(pts[0][0] + ox, pts[0][1] + oy);
    for (let i = 1; i < pts.length; i++) {
      if (cum[i] > target) {
        const f = (target - cum[i - 1]) / (cum[i] - cum[i - 1] || 1);
        ctx.lineTo(lerp(pts[i - 1][0], pts[i][0], f) + ox, lerp(pts[i - 1][1], pts[i][1], f) + oy); break;
      }
      ctx.lineTo(pts[i][0] + ox, pts[i][1] + oy);
    }
  };
  const style = s.dash ? "pen" : (s.style || LSTYLE);
  if (style === "brush" && pts.length > 3 && w > 1.6) {
    // 毛笔：起笔重、收笔轻，中间随长度起伏
    const ph = (pts[0][0] * 7.1 + pts[0][1] * 3.3) % 6.28;
    ctx.setLineDash([]);
    for (let i = 1; i < pts.length; i++) {
      if (cum[i - 1] > target) break;
      const u = cum[i] / (len || 1);
      const k = (.55 + .45 * Math.sin(cum[i] * .018 + ph)) * (1.25 - .7 * u) * Math.min(1, .35 + cum[i] / 14);
      ctx.lineWidth = Math.max(1.1 / Z, w * (.45 + .95 * k));
      ctx.beginPath(); ctx.moveTo(pts[i - 1][0], pts[i - 1][1]);
      const e = cum[i] > target ? (target - cum[i - 1]) / (cum[i] - cum[i - 1] || 1) : 1;
      ctx.lineTo(lerp(pts[i - 1][0], pts[i][0], e), lerp(pts[i - 1][1], pts[i][1], e)); ctx.stroke();
    }
  } else if (style === "chalk") {
    // 粉笔：断断续续的两层，带点颗粒
    ctx.lineWidth = w; ctx.setLineDash([w * 5, w * .9, w * 2.2, w * .7]); path(); ctx.stroke();
    ctx.globalAlpha *= .45; ctx.lineWidth = w * .55; ctx.setLineDash([w * .8, w * 1.6]); path(w * .35, -w * .3); ctx.stroke();
  } else {
    ctx.lineWidth = w; ctx.setLineDash(s.dash ? s.dash.map(d => d) : []);
    path(); ctx.stroke();
    if (style === "double" && !s.dash && w > 1.6) {   // 素描/版画：旁边再轻轻带一笔
      ctx.globalAlpha *= .38; ctx.lineWidth = w * .45; path(w * .55 + .6, -w * .4 - .4); ctx.stroke();
    }
  }
  ctx.setLineDash([]); ctx.globalAlpha = 1;
}
// 排线阴影：在多边形里沿 45° 画平行线，只画右下半边（版画 / 素描的暗面）
function hatchPoly(pts, color, alpha, gap, full = false) {
  if (alpha <= 0 || pts.length < 3) return;
  let x0 = 1e9, y0 = 1e9, x1 = -1e9, y1 = -1e9;
  for (const [x, y] of pts) { x0 = Math.min(x0, x); y0 = Math.min(y0, y); x1 = Math.max(x1, x); y1 = Math.max(y1, y); }
  if (x1 - x0 < 12 || y1 - y0 < 12) return;
  ctx.save(); ctx.beginPath(); pts.forEach((p, i) => i ? ctx.lineTo(p[0], p[1]) : ctx.moveTo(p[0], p[1])); ctx.closePath(); ctx.clip();
  ctx.globalAlpha = alpha; ctx.strokeStyle = color; ctx.lineWidth = Math.max(1, gap * .2); ctx.lineCap = "round";
  const cx = (x0 + x1) / 2, cy = (y0 + y1) / 2, span = (x1 - x0) + (y1 - y0);
  ctx.beginPath();
  for (let c = full ? -span / 2 : -span * .04; c <= span / 2; c += gap) {
    // 直线 x + y = cx + cy + c，从左上到右下的对角线方向
    const k = cx + cy + c, wob = Math.sin(c * .37) * gap * .25;
    ctx.moveTo(x0 - 4, k - x0 + 4 + wob); ctx.lineTo(x1 + 4, k - x1 - 4 - wob);
  }
  ctx.stroke(); ctx.restore(); ctx.globalAlpha = 1;
}
function fillPoly(pts, color, alpha) {
  if (alpha <= 0) return;
  ctx.globalAlpha = alpha; ctx.fillStyle = color; ctx.beginPath();
  pts.forEach((p, i) => i ? ctx.lineTo(p[0], p[1]) : ctx.moveTo(p[0], p[1]));
  ctx.closePath(); ctx.fill(); ctx.globalAlpha = 1;
}
// 从左往右“写出来”的文字
function revealText(str, x, y, font, color, p, align = "center", alpha = 1) {
  if (p <= 0 || alpha <= 0) return;
  ctx.font = font; ctx.textAlign = align; ctx.textBaseline = "middle";
  const w = ctx.measureText(str).width;
  const x0 = align === "center" ? x - w / 2 : align === "left" ? x : x - w;
  ctx.save(); ctx.beginPath(); ctx.rect(x0 - 6, y - 400, (w + 12) * clamp(p), 800); ctx.clip();
  ctx.globalAlpha = alpha; ctx.fillStyle = color; ctx.fillText(str, x, y); ctx.restore(); ctx.globalAlpha = 1;
}
function textW(str, font) { ctx.font = font; return ctx.measureText(str).width; }

// 一组笔画（涂鸦用）：依次画出
class Pen {
  constructor(seed) { this.r = rng(seed); this.items = []; this.seed = seed; }
  _s(pts, o = {}, closed = false) {
    const sp = sketch(pts, (this.r() * 1e9) | 0, o.amp ?? 1.6, closed);
    this.items.push({kind: "s", ...withLen(sp), c: o.c || C.ink, w: o.w || 3, dash: o.dash, fill: o.fill, fillPts: o.fill ? pts : null, alpha: o.alpha, noHatch: o.noHatch});
    return this;
  }
  line(pts, o) { return this._s(pts, o); }
  poly(pts, o) { return this._s(pts, o, true); }
  circle(cx, cy, r, o) { return this._s(ellipsePts(cx, cy, r, r), o); }
  ellipse(cx, cy, rx, ry, o) { return this._s(ellipsePts(cx, cy, rx, ry), o); }
  arc(cx, cy, r, a0, a1, o) { return this._s(ellipsePts(cx, cy, r, r, a0, a1), o); }
  curve(p0, p1, p2, p3, o) { return this._s(bez(p0, p1, p2, p3), o); }
  text(str, x, y, size, color, font = HAND) { this.items.push({kind: "t", str, x, y, size, color: color || C.ink, font}); return this; }
  transform(ox, oy, s) {
    for (const it of this.items) {
      if (it.kind === "s") {
        it.pts = it.pts.map(([x, y]) => [ox + x * s, oy + y * s]);
        it.cum = it.cum.map(v => v * s); it.len *= s; it.w *= Math.sqrt(s);
        if (it.fillPts) it.fillPts = it.fillPts.map(([x, y]) => [ox + x * s, oy + y * s]);
        if (it.dash) it.dash = it.dash.map(d => d * s);
      } else { it.x = ox + it.x * s; it.y = oy + it.y * s; it.size *= s; }
    }
    return this;
  }
  bbox() {
    let x0 = 1e9, y0 = 1e9, x1 = -1e9, y1 = -1e9;
    for (const it of this.items) {
      if (it.kind === "s") for (const [x, y] of it.pts) { x0 = Math.min(x0, x); y0 = Math.min(y0, y); x1 = Math.max(x1, x); y1 = Math.max(y1, y); }
      else { const w = textW(it.str, penFont(it)); x0 = Math.min(x0, it.x - w / 2); x1 = Math.max(x1, it.x + w / 2); y0 = Math.min(y0, it.y - it.size / 2); y1 = Math.max(y1, it.y + it.size / 2); }
    }
    return {x0, y0, x1, y1};
  }
  draw(p, alpha = 1) {
    const strokes = this.items.filter(i => i.kind === "s");
    const total = strokes.reduce((s, i) => s + i.len, 0) || 1;
    let acc = 0;
    for (const it of this.items) {
      if (it.kind === "s") {
        const st = acc / total, en = (acc + it.len) / total; acc += it.len;
        const q = clamp((p * 1.05 - st) / Math.max(1e-6, en - st));
        if (it.fillPts) {
          const fa = alpha * clamp(p * 1.6 - .3) * (it.alpha ?? 1);
          if (TH.fill.wash) {   // 水彩：颜色略微溢出线条、边缘深一点
            fillPoly(it.fillPts.map(([x, y]) => [x + 3, y + 2]), it.fill, fa * .35);
            fillPoly(it.fillPts, it.fill, fa * (TH.fill.alpha ?? 1) * .8);
          } else fillPoly(it.fillPts, it.fill, fa * (TH.fill.alpha ?? 1));
          if (TH.fill.hatch && fa > .05 && !it.noHatch) hatchPoly(it.fillPts, it.hatchC || C.ink, TH.fill.hatch * fa, TH.fill.hatchGap || 7);
        }
        drawStroke(it, q, alpha);
      } else revealText(it.str, it.x, it.y, penFont(it), it.color, clamp((p - .55) / .45), "center", alpha);
    }
  }
}

// ───────────── 小涂鸦 ─────────────
const ICONS = {
  ice(P) {
    const top = [[0, -52], [46, -28], [0, -4], [-46, -28]];
    P.poly([[-46, -28], [0, -4], [0, 50], [-46, 26]], {fill: "#bfe0f2", c: C.blue})
     .poly([[46, -28], [0, -4], [0, 50], [46, 26]], {fill: "#a8d3ec", c: C.blue})
     .poly(top, {fill: "#e3f3fb", c: C.blue})
     .line([[58, -58], [70, -70]], {c: C.blue, w: 2}).line([[64, -46], [80, -48]], {c: C.blue, w: 2}).line([[52, -66], [54, -82]], {c: C.blue, w: 2});
  },
  pigtail(P) {
    P.curve([-40, 10], [-40, -40], [20, -40], [15, -5], {c: "#e0879a", w: 6})
     .curve([15, -5], [10, 25], [-20, 15], [-8, -5], {c: "#e0879a", w: 6})
     .curve([-8, -5], [5, -25], [40, -10], [45, 25], {c: "#e0879a", w: 6})
     .text("?", 52, -34, 54, C.red);
  },
  pigtail_ex(P) { ICONS.pigtail(P); P.items.pop(); P.text("!", 56, -34, 60, C.red); },
  ghost(P) {
    P.poly([[-38, 50], [-38, -10], [-30, -40], [0, -55], [30, -40], [38, -10], [38, 50], [26, 38], [13, 50], [0, 38], [-13, 50], [-26, 38]], {fill: "#ecebea", c: C.soft})
     .circle(-13, -14, 5, {fill: C.ink, w: 2}).circle(13, -14, 5, {fill: C.ink, w: 2}).ellipse(0, 10, 7, 9, {c: C.soft, w: 2});
  },
  magnet(P) {
    P.line([[-34, -40], [-34, 10]], {c: C.red, w: 16}).line([[34, -40], [34, 10]], {c: C.red, w: 16})
     .arc(0, 10, 34, 0, Math.PI, {c: C.red, w: 16})
     .line([[-34, -52], [-34, -40]], {c: "#999", w: 16}).line([[34, -52], [34, -40]], {c: "#999", w: 16})
     .line([[-60, -70], [-48, -60]], {w: 2}).line([[60, -70], [48, -60]], {w: 2});
  },
  lens(P) { P.circle(-10, -12, 34, {fill: "#e3f3fb", w: 4}).line([[14, 12], [48, 46]], {w: 9, c: "#7a5230"}).arc(-10, -12, 22, 3.6, 4.6, {c: "#fff", w: 3}); },
  globe(P) {
    P.circle(0, 0, 44, {fill: "#d9ecd0", c: C.green, w: 3}).ellipse(0, 0, 18, 44, {c: C.green, w: 2})
     .line([[-44, 0], [44, 0]], {c: C.green, w: 2}).curve([-38, -22], [-15, -30], [15, -30], [38, -22], {c: C.green, w: 2})
     .curve([-38, 22], [-15, 30], [15, 30], [38, 22], {c: C.green, w: 2});
  },
  scroll(P) {
    P.poly([[-60, -38], [60, -38], [60, 38], [-60, 38]], {fill: "#f1dca6", c: "#8a6a3a"})
     .ellipse(-60, 0, 10, 40, {fill: "#e3c686", c: "#8a6a3a"}).ellipse(60, 0, 10, 40, {fill: "#e3c686", c: "#8a6a3a"});
    for (let i = 0; i < 4; i++) P.line([[-40, -22 + i * 15], [-10 + (i % 2) * 20, -24 + i * 15], [40 - (i === 3) * 30, -20 + i * 15]], {c: "#8a6a3a", w: 2, amp: 2.5});
  },
  tree(P) {
    P.poly([[-10, 60], [-8, 0], [8, 0], [12, 60]], {fill: "#9b6b43", c: "#6b4a2e"})
     .poly([[-55, 0], [-62, -30], [-40, -58], [-5, -66], [30, -60], [58, -36], [55, -4], [30, 8], [-20, 10]], {fill: "#9cc27e", c: C.green})
     .line([[-16, 28], [16, 22]], {c: "#6b4a2e", w: 2}).line([[-16, 36], [16, 30]], {c: "#6b4a2e", w: 2});
  },
  soil(P) {
    P.poly([[-50, 30], [-30, 5], [0, -5], [30, 5], [50, 30]], {fill: "#b58a5f", c: "#7b5a3a"});
    for (let i = 0; i < 6; i++) P.circle(-30 + i * 12, 18 - (i % 2) * 8, 2.5, {fill: "#5d4028", c: "#5d4028", w: 1});
  },
  dots(P, o) {        // 两排小圆点（人数、数量）：{n, color, text}
    const n = o.n || 10, per = Math.ceil(n / 2);
    for (let i = 0; i < n; i++) { const row = i < per ? 0 : 1, k = row ? i - per : i; P.circle(-(per - 1) * 17 + k * 34 + row * 17, -14 + row * 32, 12, {fill: "#cfe0f3", c: o.color || C.blue, w: 2}); }
    if (o.text) P.text(o.text, 0, 50, 26, o.color || C.blue, KAI);
  },
  dots_cross(P, o) {  // 给上面的圆点逐个打十字（同样的 n）
    const n = o.n || 10, per = Math.ceil(n / 2);
    for (let i = 0; i < n; i++) { const row = i < per ? 0 : 1, k = row ? i - per : i, x = -(per - 1) * 17 + k * 34 + row * 17, y = -14 + row * 32 - 3;
      P.line([[x, y - 6], [x, y + 6]], {c: "#555", w: 2.5, amp: .5}).line([[x - 5, y - 1], [x + 5, y - 1]], {c: "#555", w: 2.5, amp: .5}); }
  },
  target(P, o) {     // 靶子 + 一行小字 {text}
    P.circle(0, -10, 36, {c: C.red, w: 3}).circle(0, -10, 20, {c: C.red, w: 2}).circle(0, -10, 5, {fill: C.red, c: C.red, w: 2});
    if (o.text) P.text(o.text, 0, 48, 24, C.ink, KAI);
  },
  fish(P) {
    P.poly([[-40, 0], [-20, -22], [15, -24], [38, 0], [15, 24], [-20, 22]], {fill: "#f0c64c", c: "#b98710"})
     .poly([[38, 0], [62, -22], [58, 0], [62, 22]], {fill: "#f0c64c", c: "#b98710"})
     .circle(-22, -5, 3.5, {fill: C.ink, w: 1.5}).curve([-5, -18], [2, -6], [2, 6], [-5, 18], {c: "#b98710", w: 2});
  },
  loop(P, o) {       // 绕一圈的虚线循环箭头 + 上方小字 {text}（套在别的涂鸦外面表示“周而复始”）
    P.arc(10, 0, 78, -2.6, 2.2, {c: C.soft, w: 2.5, dash: [8, 7]}).line([[-40, 62], [-46, 46], [-28, 50]], {c: C.soft, w: 2.5});
    if (o.text) P.text(o.text, 10, -98, 24, C.ink, KAI);
  },
  shroud(P) {
    P.poly([[-50, -30], [40, -36], [48, 30], [-44, 36]], {fill: "#f7f6f2", c: C.soft})
     .line([[-30, -10], [30, -14]], {c: C.soft, w: 1.5, dash: [5, 5]}).line([[-30, 8], [32, 5]], {c: C.soft, w: 1.5, dash: [5, 5]})
     .line([[40, -50], [70, -80]], {c: "#777", w: 2}).curve([40, -50], [20, -60], [10, -40], [-10, -44], {c: C.purple, w: 1.5});
  },
  sheet(P) {
    P.poly([[-60, 10], [-30, -20], [0, -5], [30, -30], [60, -10], [55, 40], [25, 20], [-5, 45], [-35, 25], [-62, 50]], {fill: "#fbfbfb", c: C.soft})
     .line([[-20, -40], [-20, -90]], {c: C.purple, w: 2.5}).line([[-30, -78], [-20, -92], [-10, -78]], {c: C.purple, w: 2.5})
     .line([[20, -50], [20, -100]], {c: C.purple, w: 2.5}).line([[10, -88], [20, -102], [30, -88]], {c: C.purple, w: 2.5});
  },
  swap(P) {
    P.curve([-50, -10], [-20, -50], [20, -50], [50, -10], {c: C.orange, w: 3.5}).line([[38, -24], [50, -10], [56, -28]], {c: C.orange, w: 3.5})
     .curve([50, 10], [20, 50], [-20, 50], [-50, 10], {c: C.navy, w: 3.5}).line([[-38, 24], [-50, 10], [-56, 28]], {c: C.navy, w: 3.5});
  },
  cows(P) {
    const cow = (x, y, s) => { P.poly([[x - 26 * s, y - 12 * s], [x + 20 * s, y - 12 * s], [x + 20 * s, y + 12 * s], [x - 26 * s, y + 12 * s]], {fill: "#fff", c: C.ink, w: 2});
      P.circle(x + 28 * s, y - 14 * s, 9 * s, {fill: "#fff", c: C.ink, w: 2}); P.line([[x - 20 * s, y + 12 * s], [x - 20 * s, y + 24 * s]], {w: 2}).line([[x + 14 * s, y + 12 * s], [x + 14 * s, y + 24 * s]], {w: 2});
      P.circle(x - 6 * s, y - 2 * s, 5 * s, {fill: C.ink, c: C.ink, w: 1}); };
    cow(-40, 0, 1); cow(30, -28, .75); cow(40, 22, .7);
    P.line([[-80, 20], [-80, -30]], {c: C.green, w: 3}).line([[-90, -18], [-80, -32], [-70, -18]], {c: C.green, w: 3});
  },
  money(P) {
    for (let i = 0; i < 3; i++) P.poly([[-44 + i * 8, -26 + i * 10], [36 + i * 8, -26 + i * 10], [36 + i * 8, 10 + i * 10], [-44 + i * 8, 10 + i * 10]], {fill: "#cfe8c0", c: C.green, w: 2});
    P.text("$", 4, 1, 36, C.green, "serif");
  },
  sea(P) {
    for (let i = 0; i < 3; i++) P.curve([-60, -10 + i * 20], [-30, -30 + i * 20], [-10, 10 + i * 20], [20, -10 + i * 20], {c: C.blue, w: 3})
      .curve([20, -10 + i * 20], [40, -30 + i * 20], [50, 5 + i * 20], [66, -10 + i * 20], {c: C.blue, w: 3});
  },
  coffins(P) {
    const cf = x => P.poly([[x - 12, -44], [x + 12, -44], [x + 20, -20], [x + 12, 44], [x - 12, 44], [x - 20, -20]], {fill: "#c9a77f", c: "#6b4a2e", w: 2.5});
    cf(-40); cf(40);
    P.curve([-20, -60], [0, -80], [0, -80], [20, -60], {c: C.red, w: 2.5}).curve([20, 60], [0, 80], [0, 80], [-20, 60], {c: C.red, w: 2.5});
  },
  lifebar(P, o) {     // 竖向的寿命/时期条：{len, color, text}
    const L = o.len || 1000, c = o.color || C.love;
    P.line([[0, 0], [0, L]], {c, w: 10, amp: 3}).circle(0, 0, 12, {fill: c, c}).circle(0, L, 12, {fill: c, c});
    [...(o.text || "")].forEach((ch, i) => P.text(ch, -38, 120 + i * 42, 34, c));
  },
  rome(P) {
    P.arc(0, 10, 44, Math.PI, Math.PI * 2, {fill: "#efe3cf", c: "#8a6a3a", w: 3}).line([[-52, 10], [52, 10]], {c: "#8a6a3a", w: 3})
     .line([[0, -34], [0, -58]], {c: "#8a6a3a", w: 3}).line([[-8, -50], [8, -50]], {c: "#8a6a3a", w: 3})
     .line([[-40, 10], [-40, 40]], {c: "#8a6a3a", w: 2}).line([[40, 10], [40, 40]], {c: "#8a6a3a", w: 2}).line([[-52, 40], [52, 40]], {c: "#8a6a3a", w: 3});
  },
  butterflies(P) {
    const bf = (x, y, s) => { P.ellipse(x - 12 * s, y - 8 * s, 12 * s, 9 * s, {fill: "#f5d33c", c: "#b89a10", w: 2}).ellipse(x + 12 * s, y - 8 * s, 12 * s, 9 * s, {fill: "#f5d33c", c: "#b89a10", w: 2})
      .ellipse(x - 9 * s, y + 8 * s, 8 * s, 7 * s, {fill: "#f5d33c", c: "#b89a10", w: 2}).ellipse(x + 9 * s, y + 8 * s, 8 * s, 7 * s, {fill: "#f5d33c", c: "#b89a10", w: 2})
      .line([[x, y - 14 * s], [x, y + 14 * s]], {w: 2}); };
    bf(-50, 10, 1); bf(10, -30, .8); bf(55, 15, .9); bf(-5, 40, .6);
  },
  basket(P) {
    P.poly([[-55, -5], [55, -5], [42, 40], [-42, 40]], {fill: "#d7b37a", c: "#8a6a3a"}).arc(0, -5, 55, Math.PI, Math.PI * 2, {c: "#8a6a3a", w: 3})
     .circle(0, -14, 16, {fill: "#fde4cf", c: C.ink, w: 2}).line([[-30, 12], [30, 12]], {c: "#8a6a3a", w: 2}).line([[-26, 26], [26, 26]], {c: "#8a6a3a", w: 2});
  },
  heart(P) { P.poly(bez([0, 36], [-70, -10], [-30, -60], [0, -24], 20).concat(bez([0, -24], [30, -60], [70, -10], [0, 36], 20)), {fill: "#f3a3b8", c: C.love, w: 3}); },
  ants(P) {
    for (let i = 0; i < 6; i++) { const x = -110 + i * 44, y = (i % 2) * 10 - 5;
      P.circle(x - 10, y, 5, {fill: C.ink, w: 1}).circle(x, y, 6, {fill: C.ink, w: 1}).circle(x + 12, y, 8, {fill: C.ink, w: 1});
      P.line([[x - 4, y], [x - 12, y - 10]], {w: 1.5}).line([[x, y], [x, y + 12]], {w: 1.5}).line([[x + 4, y], [x + 12, y + 11]], {w: 1.5}); }
  },
  // ───── 通用道具 / 布景（可在 world.js 的 ICON_POS 里用 icon: "名字" 引用，或让人物 hold） ─────
  star(P) { P.poly([[0, -40], [11, -12], [40, -12], [16, 6], [25, 36], [0, 18], [-25, 36], [-16, 6], [-40, -12], [-11, -12]], {fill: "#f5d33c", c: "#b89a10"}); },
  rabbit(P) {
    P.ellipse(0, 10, 38, 26, {fill: "#fbfaf6", c: C.ink}).circle(36, -14, 18, {fill: "#fbfaf6", c: C.ink})
     .ellipse(30, -48, 7, 22, {fill: "#fbfaf6", c: C.ink}).ellipse(44, -46, 7, 21, {fill: "#fbfaf6", c: C.ink})
     .circle(-38, 6, 9, {fill: "#fbfaf6", c: C.ink}).circle(42, -16, 3, {fill: C.ink, w: 1}).line([[-18, 34], [-24, 42]], {w: 3}).line([[20, 34], [26, 42]], {w: 3});
  },
  stump(P) {
    P.poly([[-44, 40], [-36, -20], [36, -20], [44, 40]], {fill: "#9b6b43", c: "#6b4a2e"}).ellipse(0, -20, 36, 11, {fill: "#d9b182", c: "#6b4a2e"})
     .ellipse(0, -20, 18, 5, {c: "#8a6a3a", w: 2}).line([[-60, 40], [60, 40]], {c: C.green, w: 3});
  },
  hoe(P) { P.line([[-6, 60], [18, -70]], {c: "#8a5a3a", w: 7}).poly([[10, -76], [44, -70], [42, -56], [14, -62]], {fill: "#9aa3aa", c: C.ink, w: 2.5}); },
  sprout(P) { for (let i = 0; i < 4; i++) { const x = -45 + i * 30; P.line([[x, 30], [x, -5]], {c: C.green, w: 3}).ellipse(x - 9, -8, 10, 5, {fill: "#9cc27e", c: C.green, w: 2}).ellipse(x + 9, -14, 10, 5, {fill: "#9cc27e", c: C.green, w: 2}); } P.line([[-70, 30], [70, 30]], {c: "#8a6a3a", w: 3}); },
  weeds(P) { for (let i = 0; i < 7; i++) { const x = -60 + i * 20; P.line([[x, 30], [x - 8 + (i % 3) * 8, -20 - (i % 2) * 18]], {c: "#8a9a5b", w: 2.5}); } P.line([[-70, 30], [70, 30]], {c: "#8a6a3a", w: 3}); },
  house(P) {
    P.poly([[-70, -10], [0, -70], [70, -10]], {fill: "#c97c5d", c: C.ink}).poly([[-58, -10], [58, -10], [58, 60], [-58, 60]], {fill: "#f4e4c8", c: C.ink})
     .poly([[-14, 60], [-14, 18], [14, 18], [14, 60]], {fill: "#9b6b43", c: C.ink, w: 2.5}).poly([[24, 6], [48, 6], [48, 28], [24, 28]], {fill: "#cfe6f4", c: C.ink, w: 2.5});
  },
  wall(P) {
    P.poly([[-150, -80], [150, -80], [150, 60], [-150, 60]], {fill: "#e6dccb", c: C.ink});
    for (let r = 0; r < 4; r++) for (let k = 0; k < 6; k++) P.line([[-150 + k * 60 + (r % 2) * 30, -80 + r * 35], [-150 + k * 60 + (r % 2) * 30, -45 + r * 35]], {c: "#b8ab94", w: 1.6, amp: .8});
    for (let r = 1; r < 4; r++) P.line([[-150, -80 + r * 35], [150, -80 + r * 35]], {c: "#b8ab94", w: 1.6, amp: .8});
  },
  mountain(P) { P.poly([[-120, 50], [-40, -60], [0, -10], [40, -80], [120, 50]], {fill: "#c9d6c0", c: "#6c7a66"}).poly([[40, -80], [28, -58], [52, -58]], {fill: "#fff", c: "#6c7a66", w: 2}); },
  sun(P) { P.circle(0, 0, 32, {fill: "#f7c948", c: "#c99a10"}); for (let i = 0; i < 8; i++) { const a = i / 8 * Math.PI * 2; P.line([[Math.cos(a) * 44, Math.sin(a) * 44], [Math.cos(a) * 62, Math.sin(a) * 62]], {c: "#c99a10", w: 3}); } },
  moon(P) { P.poly(bez([0, -44], [-60, -30], [-50, 44], [10, 44], 16).concat(bez([10, 44], [-26, 26], [-30, -24], [0, -44], 16)), {fill: "#f4e7a6", c: "#b8a45a"}); },
  table(P) { P.poly([[-80, -10], [80, -10], [80, 4], [-80, 4]], {fill: "#b98b5e", c: C.ink}).line([[-66, 4], [-66, 60]], {w: 6, c: "#7a5230"}).line([[66, 4], [66, 60]], {w: 6, c: "#7a5230"}); },
  chair(P) { P.line([[-20, -60], [-20, 60]], {w: 6, c: "#7a5230"}).poly([[-24, 0], [30, 0], [30, 10], [-24, 10]], {fill: "#b98b5e", c: C.ink}).line([[26, 10], [26, 60]], {w: 6, c: "#7a5230"}); },
  bed(P) { P.poly([[-110, 0], [110, 0], [110, 30], [-110, 30]], {fill: "#e9e4f5", c: C.ink}).poly([[-110, -40], [-96, -40], [-96, 50], [-110, 50]], {fill: "#9b6b43", c: C.ink}).ellipse(-70, -6, 24, 12, {fill: "#fff", c: C.ink, w: 2.5}); },
  door(P) { P.poly([[-40, -100], [40, -100], [40, 60], [-40, 60]], {fill: "#a8744b", c: C.ink}).circle(26, -18, 5, {fill: "#e8b83a", c: C.ink, w: 1.5}).poly([[-28, -86], [28, -86], [28, -30], [-28, -30]], {c: "#6b4a2e", w: 2}); },
  rifle(P) { P.line([[-60, 6], [50, -6]], {w: 5, c: "#444"}).poly([[-70, 4], [-40, 2], [-38, 18], [-72, 22]], {fill: "#7a5230", c: C.ink, w: 2}); },
  book(P) { P.poly([[-40, -30], [0, -24], [0, 30], [-40, 24]], {fill: "#f7f2e6", c: C.ink, w: 2.5}).poly([[0, -24], [40, -30], [40, 24], [0, 30]], {fill: "#f7f2e6", c: C.ink, w: 2.5}).line([[-32, -12], [-8, -9]], {w: 1.5, c: C.soft}).line([[8, -9], [32, -12]], {w: 1.5, c: C.soft}); },
  boat(P) { P.poly([[-90, 0], [90, 0], [60, 36], [-60, 36]], {fill: "#9b6b43", c: C.ink}).line([[0, 0], [0, -90]], {w: 4}).poly([[4, -86], [60, -20], [4, -20]], {fill: "#fbfaf6", c: C.ink, w: 2.5}); ICONS.sea(P); },
  spear(P) { P.line([[-80, 8], [60, -6]], {w: 5, c: "#7a5230"}).poly([[58, -14], [92, -9], [60, 4]], {fill: "#9aa3aa", c: C.ink, w: 2}); },
  sword(P) { P.line([[-60, 0], [50, 0]], {w: 6, c: "#9aa3aa"}).line([[-60, -14], [-60, 14]], {w: 5, c: "#b98710"}).line([[-60, 0], [-80, 0]], {w: 6, c: "#7a5230"}); },
  flag(P) { P.line([[0, 60], [0, -70]], {w: 4, c: "#6b4a2e"}).poly([[2, -70], [70, -56], [2, -36]], {fill: C.red, c: C.ink, w: 2.5}); },
  coin(P) { P.circle(0, 0, 30, {fill: "#f0c64c", c: "#b98710"}).circle(0, 0, 20, {c: "#b98710", w: 2}); },
  moneybag(P) { P.poly([[-40, 40], [-46, 0], [-20, -30], [20, -30], [46, 0], [40, 40]], {fill: "#d9b182", c: "#7b5a3a"}).line([[-18, -34], [18, -34]], {w: 4, c: "#7b5a3a"}).text("$", 0, 8, 40, "#7b5a3a", "serif"); },
  gift(P) { P.poly([[-40, -10], [40, -10], [40, 44], [-40, 44]], {fill: "#e79a92", c: C.ink}).line([[0, -10], [0, 44]], {w: 6, c: "#f5d33c"}).curve([0, -10], [-30, -40], [-30, -10], [0, -10], {c: "#f5d33c", w: 4}).curve([0, -10], [30, -40], [30, -10], [0, -10], {c: "#f5d33c", w: 4}); },
  letter(P) { P.poly([[-50, -32], [50, -32], [50, 32], [-50, 32]], {fill: "#fbfaf6", c: C.ink}).line([[-50, -32], [0, 6], [50, -32]], {w: 2.5}); },
  laptop(P) { P.poly([[-50, -40], [50, -40], [50, 20], [-50, 20]], {fill: "#cfe6f4", c: C.ink}).poly([[-64, 20], [64, 20], [72, 32], [-72, 32]], {fill: "#9aa3aa", c: C.ink}); },
  phone(P) { P.poly([[-24, -46], [24, -46], [24, 46], [-24, 46]], {fill: "#3a3a44", c: C.ink}).poly([[-18, -36], [18, -36], [18, 32], [-18, 32]], {fill: "#cfe6f4", c: C.ink, w: 1.5}); },
  chart(P) { P.line([[-60, 50], [-60, -50]], {w: 3}).line([[-60, 50], [60, 50]], {w: 3}); [[-40, 20], [-10, -5], [20, -25], [48, -45]].forEach(([x, y], i) => P.poly([[x - 10, 50], [x + 10, 50], [x + 10, y], [x - 10, y]], {fill: ["#9cc27e", "#5b9bd5", "#f0c64c", "#e79a92"][i], c: C.ink, w: 2})); },
  bulb(P) { P.circle(0, -14, 32, {fill: "#fbe68a", c: "#b89a10"}).poly([[-14, 16], [14, 16], [12, 36], [-12, 36]], {fill: "#9aa3aa", c: C.ink, w: 2}); for (let i = 0; i < 5; i++) { const a = -Math.PI + i / 4 * Math.PI; P.line([[Math.cos(a) * 44, -14 + Math.sin(a) * 44], [Math.cos(a) * 58, -14 + Math.sin(a) * 58]], {c: "#b89a10", w: 3}); } },
  gear(P) { const o = []; for (let i = 0; i < 24; i++) { const a = i / 24 * Math.PI * 2, r = i % 3 === 0 ? 30 : 40; o.push([Math.cos(a) * r, Math.sin(a) * r]); } P.poly(o, {fill: "#c4cfd8", c: C.ink}).circle(0, 0, 12, {fill: C.paper, c: C.ink, w: 2}); },
  clock(P) { P.circle(0, 0, 40, {fill: "#fbfaf6", c: C.ink}).line([[0, 0], [0, -26]], {w: 4}).line([[0, 0], [18, 8]], {w: 4}); },
  ground(P, o) { const L = o.len || 900; const pts = []; for (let x = -L / 2; x <= L / 2; x += 40) pts.push([x, Math.sin(x * .02) * 3]); P.line(pts, {c: "#8a7a5a", w: 3, amp: 2}); for (let x = -L / 2 + 30; x < L / 2; x += 110) P.line([[x, 0], [x - 5, -12]], {c: C.green, w: 2}).line([[x + 6, 0], [x + 9, -14]], {c: C.green, w: 2}); },
  cloud_big(P) { P.poly([[-80, 20], [-86, -6], [-60, -30], [-26, -44], [14, -44], [48, -30], [80, -8], [74, 20]], {fill: "#fbfbfb", c: "#9aa6b0"}); },
  tree_big(P) { P.poly([[-18, 120], [-12, 0], [12, 0], [20, 120]], {fill: "#9b6b43", c: "#6b4a2e"}).poly([[-110, 0], [-120, -60], [-80, -120], [-10, -140], [60, -125], [115, -75], [110, -10], [60, 20], [-40, 24]], {fill: "#9cc27e", c: C.green}); },
  // ───── 《逍遥游》专用涂鸦 ─────
  kun(P) {      // 鲲：深蓝色的巨鱼（朝右），±90
    P.poly([[-62, 0], [-44, -22], [-6, -32], [36, -26], [62, -8], [66, 6], [44, 22], [0, 28], [-40, 20]], {fill: "#3d5a80", c: "#22364f", w: 3.4})
     .poly([[-60, 0], [-92, -30], [-84, 0], [-92, 30]], {fill: "#3d5a80", c: "#22364f"})
     .curve([-30, 14], [0, 26], [30, 22], [56, 10], {c: "#9cc0e0", w: 3})
     .poly([[-6, -30], [6, -52], [22, -30]], {fill: "#2e4868", c: "#22364f", w: 2.5})
     .poly([[4, 12], [-14, 34], [18, 20]], {fill: "#2e4868", c: "#22364f", w: 2.5})
     .circle(44, -8, 7, {fill: "#fff", c: "#22364f", w: 2}).circle(46, -8, 3, {fill: C.ink, c: C.ink, w: 1})
     .curve([52, 8], [56, 12], [60, 10], [63, 6], {c: "#22364f", w: 2.5})
     .curve([-20, -18], [-16, -10], [-16, 0], [-20, 8], {c: "#5e7ea4", w: 2}).curve([-4, -20], [0, -10], [0, 2], [-4, 12], {c: "#5e7ea4", w: 2});
  },
  peng(P) {     // 鹏：展翅的大鸟（朝右），±110
    P.poly([[-6, -4], [-40, -60], [-90, -104], [-70, -70], [-104, -84], [-72, -48], [-100, -50], [-56, -22], [-30, 2]], {fill: "#6d86a8", c: "#2f4460", w: 3.2})
     .poly([[-40, 6], [-92, -6], [-80, 8], [-98, 20], [-76, 22], [-86, 36], [-40, 22]], {fill: "#58708f", c: "#2f4460", w: 3})
     .ellipse(0, 10, 46, 22, {fill: "#58708f", c: "#2f4460", w: 3.4})
     .curve([-20, 22], [0, 30], [20, 28], [36, 18], {c: "#c9d8ea", w: 3})
     .circle(46, -10, 17, {fill: "#58708f", c: "#2f4460", w: 3})
     .poly([[60, -16], [86, -8], [60, -2]], {fill: "#e8b83a", c: "#9a7410", w: 2.5})
     .circle(50, -14, 4, {fill: "#fff", c: C.ink, w: 1.5}).circle(51, -14, 2, {fill: C.ink, c: C.ink, w: 1})
     .poly([[4, 0], [-18, -58], [-44, -110], [-30, -70], [-14, -96], [-8, -60], [8, -80], [14, -12]], {fill: "#8aa2c2", c: "#2f4460", w: 3});
  },
  cicada(P) {   // 蝉（朝右，笑哈哈），±50
    P.ellipse(-14, -10, 34, 12, {fill: "#d8e8f0", c: "#6c7a86", w: 2, alpha: .75}).ellipse(-10, 4, 30, 10, {fill: "#d8e8f0", c: "#6c7a86", w: 2, alpha: .75})
     .ellipse(0, 0, 28, 14, {fill: "#7d9a5a", c: "#3f5226", w: 3}).circle(28, -2, 14, {fill: "#8fb068", c: "#3f5226", w: 3})
     .line([[26, -6], [30, -10], [34, -6]], {w: 2.5}).curve([24, 4], [28, 12], [34, 12], [38, 4], {fill: "#6b2a2a", c: C.ink, w: 2})
     .line([[-6, 12], [-12, 24]], {w: 2}).line([[6, 12], [8, 24]], {w: 2});
  },
  dove(P) {     // 小斑鸠（朝右，笑），±50
    P.ellipse(0, 6, 32, 24, {fill: "#b9b3ae", c: "#5a524c", w: 3}).poly([[-26, 0], [-50, -8], [-46, 10], [-26, 14]], {fill: "#9a938d", c: "#5a524c", w: 2.5})
     .circle(28, -16, 16, {fill: "#c9c3be", c: "#5a524c", w: 3}).poly([[42, -18], [54, -13], [42, -9]], {fill: "#e0a040", c: "#8a5a1a", w: 2})
     .line([[28, -22], [32, -26], [36, -22]], {w: 2.5}).curve([30, -8], [33, -3], [38, -3], [41, -8], {c: C.ink, w: 2.2})
     .curve([-14, -2], [-4, -10], [8, -8], [14, 4], {c: "#5a524c", w: 2.5}).line([[-4, 30], [-6, 42]], {c: "#c0703a", w: 2.5}).line([[8, 30], [10, 42]], {c: "#c0703a", w: 2.5})
     .circle(20, 0, 3, {fill: "#8a7fa0", c: "#8a7fa0", w: 1}).circle(12, 6, 3, {fill: "#8a7fa0", c: "#8a7fa0", w: 1});
  },
  sparrow(P) {  // 斥鴳：蓬蒿里的小雀，±45
    P.ellipse(0, 6, 26, 18, {fill: "#b98b5e", c: "#6b4a2e", w: 3}).poly([[-20, 0], [-44, -12], [-40, 6], [-22, 12]], {fill: "#8a6a3a", c: "#6b4a2e", w: 2.5})
     .circle(22, -10, 13, {fill: "#c9a07a", c: "#6b4a2e", w: 3}).poly([[34, -12], [44, -8], [34, -5]], {fill: "#e0a040", c: "#8a5a1a", w: 2})
     .line([[20, -15], [24, -19], [28, -15]], {w: 2.5}).curve([-10, 0], [-2, -6], [8, -4], [12, 6], {c: "#6b4a2e", w: 2})
     .line([[-2, 24], [-4, 34]], {c: "#c0703a", w: 2.5}).line([[8, 24], [10, 34]], {c: "#c0703a", w: 2.5});
  },
  mushroom(P) { // 朝菌
    P.poly([[-12, 40], [-9, 0], [9, 0], [12, 40]], {fill: "#f4ead8", c: "#8a6a3a", w: 2.5})
     .poly(bez([-44, 4], [-40, -44], [40, -44], [44, 4], 18), {fill: "#e79a6a", c: "#9a5a2a", w: 3})
     .circle(-16, -14, 6, {fill: "#fbf0e0", c: "#fbf0e0", w: 1}).circle(12, -22, 5, {fill: "#fbf0e0", c: "#fbf0e0", w: 1}).circle(24, -6, 4, {fill: "#fbf0e0", c: "#fbf0e0", w: 1})
     .line([[-50, 40], [50, 40]], {c: "#8a7a5a", w: 2.5});
  },
  tree_old(P) { // 大椿：古老的大树
    P.poly([[-40, 130], [-26, 20], [-50, -10], [-20, -6], [0, -30], [16, -4], [48, -16], [24, 24], [40, 130]], {fill: "#8a5a3a", c: "#5a3a22", w: 3.5})
     .curve([-20, 60], [-10, 40], [-14, 20], [-4, 0], {c: "#5a3a22", w: 2}).curve([16, 100], [22, 70], [10, 50], [16, 30], {c: "#5a3a22", w: 2})
     .poly([[-150, -20], [-162, -80], [-120, -150], [-50, -186], [30, -190], [110, -160], [160, -96], [150, -24], [90, 6], [0, -4], [-90, 10]], {fill: "#7fae68", c: "#4e7a3a", w: 3.4})
     .curve([-110, -60], [-80, -90], [-40, -80], [-20, -110], {c: "#4e7a3a", w: 2.4}).curve([30, -130], [60, -110], [90, -120], [110, -80], {c: "#4e7a3a", w: 2.4})
     .line([[-160, 130], [160, 130]], {c: "#8a7a5a", w: 3});
  },
  tree_ming(P) { // 冥灵：一棵中等的灵树
    P.poly([[-14, 80], [-10, 0], [10, 0], [16, 80]], {fill: "#9b6b43", c: "#6b4a2e", w: 3})
     .poly([[-80, 0], [-86, -50], [-50, -96], [0, -110], [54, -92], [86, -46], [78, 0], [30, 12], [-30, 12]], {fill: "#a8c98a", c: "#5f8a45", w: 3})
     .line([[-90, 80], [90, 80]], {c: "#8a7a5a", w: 3});
  },
  puddle(P) {   // 堂前的小坑
    P.ellipse(0, 0, 110, 26, {fill: "#bfe0f2", c: "#5b9bd5", w: 3}).curve([-60, 0], [-40, -6], [-20, 6], [0, 0], {c: "#8ec0e4", w: 2}).curve([20, 4], [40, -4], [60, 6], [80, 0], {c: "#8ec0e4", w: 2});
  },
  grass_boat(P) { // 一根小草当船
    P.curve([-30, 0], [-10, 10], [10, 10], [30, -4], {c: C.green, w: 4}).curve([-20, 2], [-10, -14], [0, -20], [6, -28], {c: C.green, w: 3});
  },
  cup(P) {      // 杯子
    P.poly([[-26, -30], [26, -30], [18, 22], [-18, 22]], {fill: "#e6d2a8", c: "#7b5a3a", w: 3}).ellipse(0, -30, 26, 7, {fill: "#bfe0f2", c: "#7b5a3a", w: 2.5})
     .line([[-12, 22], [-16, 32], [16, 32], [12, 22]], {c: "#7b5a3a", w: 3});
  },
  wind(P) {     // 风
    P.curve([-80, -30], [-20, -40], [30, -30], [50, -40], {c: "#7d8fa0", w: 3.5}).arc(50, -52, 12, Math.PI / 2, Math.PI * 2.2, {c: "#7d8fa0", w: 3.5})
     .curve([-100, 0], [-30, -8], [30, 6], [80, -4], {c: "#7d8fa0", w: 3.5}).arc(80, -16, 12, Math.PI / 2, Math.PI * 2.2, {c: "#7d8fa0", w: 3.5})
     .curve([-70, 32], [-20, 24], [20, 40], [40, 30], {c: "#7d8fa0", w: 3.5});
  },
  gourd(P) {    // 大葫芦
    P.circle(0, 24, 40, {fill: "#e3c867", c: "#8a7420", w: 3.4}).circle(0, -34, 26, {fill: "#e3c867", c: "#8a7420", w: 3.4})
     .line([[0, -60], [4, -76]], {c: "#6b4a2e", w: 4}).curve([4, -72], [20, -84], [30, -70], [22, -62], {c: C.green, w: 3})
     .curve([-20, 10], [-26, 30], [-18, 48], [-8, 56], {c: "#fff5c8", w: 3}).line([[-18, -8], [18, -8]], {c: "#8a7420", w: 2.5});
  },
  gourd_broken(P) { // 砸碎的葫芦
    P.poly([[-60, 20], [-44, -6], [-20, -2], [-24, 22]], {fill: "#e3c867", c: "#8a7420", w: 3}).poly([[-8, 22], [4, -14], [24, -4], [18, 22]], {fill: "#e3c867", c: "#8a7420", w: 3})
     .poly([[30, 22], [44, 0], [62, 8], [58, 22]], {fill: "#e3c867", c: "#8a7420", w: 3}).line([[-70, 24], [70, 24]], {c: "#8a7a5a", w: 2.5})
     .line([[-30, -30], [-40, -44]], {c: "#8a7420", w: 2}).line([[0, -34], [0, -50]], {c: "#8a7420", w: 2}).line([[30, -30], [40, -44]], {c: "#8a7420", w: 2});
  },
  jar(P) {      // 不龟手之药
    P.poly([[-30, -20], [30, -20], [36, 36], [-36, 36]], {fill: "#c9d8e0", c: "#4a5a66", w: 3}).poly([[-20, -36], [20, -36], [24, -20], [-24, -20]], {fill: "#9b6b43", c: "#4a5a66", w: 2.5})
     .poly([[-16, -6], [16, -6], [16, 24], [-16, 24]], {fill: "#fbf6e8", c: C.red, w: 2}).text("药", 0, 10, 26, C.red, KAI);
  },
  silk(P) {     // 一盆棉絮
    P.poly([[-60, 0], [60, 0], [48, 34], [-48, 34]], {fill: "#b98b5e", c: "#6b4a2e", w: 3}).ellipse(0, 0, 60, 10, {fill: "#bfe0f2", c: "#6b4a2e", w: 2.5})
     .circle(-24, -6, 16, {fill: "#fff", c: "#9aa6b0", w: 2}).circle(0, -12, 18, {fill: "#fff", c: "#9aa6b0", w: 2}).circle(24, -6, 15, {fill: "#fff", c: "#9aa6b0", w: 2});
  },
  axe(P) { P.line([[-6, 60], [8, -60]], {c: "#8a5a3a", w: 7}).poly([[4, -60], [40, -70], [44, -30], [8, -36]], {fill: "#9aa3aa", c: C.ink, w: 2.5}); },
  gnarled(P) {  // 樗：疙疙瘩瘩、弯弯曲曲的大树
    P.poly([[-50, 130], [-40, 60], [-58, 20], [-30, -10], [-44, -50], [-10, -40], [10, -70], [24, -30], [56, -46], [40, 0], [58, 40], [36, 80], [50, 130]], {fill: "#8a6448", c: "#4e3526", w: 3.6})
     .circle(-20, 40, 14, {fill: "#6e4c34", c: "#4e3526", w: 2.5}).circle(20, 86, 12, {fill: "#6e4c34", c: "#4e3526", w: 2.5}).circle(14, 0, 9, {fill: "#6e4c34", c: "#4e3526", w: 2.5})
     .curve([-30, -30], [-90, -60], [-60, -110], [-110, -130], {c: "#4e3526", w: 7}).arc(-110, -142, 12, 0, Math.PI * 1.6, {c: "#4e3526", w: 5})
     .curve([10, -60], [40, -130], [-10, -150], [30, -190], {c: "#4e3526", w: 7}).arc(40, -196, 12, Math.PI, Math.PI * 2.8, {c: "#4e3526", w: 5})
     .curve([48, -30], [110, -40], [90, -110], [140, -120], {c: "#4e3526", w: 7}).arc(148, -130, 12, 2, Math.PI * 2.5, {c: "#4e3526", w: 5})
     .ellipse(-120, -150, 50, 32, {fill: "#8fb46e", c: "#56803e", w: 3}).ellipse(30, -210, 60, 36, {fill: "#8fb46e", c: "#56803e", w: 3}).ellipse(150, -140, 50, 32, {fill: "#8fb46e", c: "#56803e", w: 3})
     .ellipse(-40, -110, 36, 24, {fill: "#9cc27e", c: "#56803e", w: 2.5}).ellipse(90, -90, 36, 24, {fill: "#9cc27e", c: "#56803e", w: 2.5})
     .line([[-170, 130], [170, 130]], {c: "#8a7a5a", w: 3});
  },
  weasel(P) {   // 狸狌（朝右）
    P.poly([[-50, 10], [-30, -8], [20, -10], [40, -4], [36, 12], [-40, 16]], {fill: "#c98f52", c: "#7b4a22", w: 3})
     .circle(44, -12, 14, {fill: "#d9a068", c: "#7b4a22", w: 3}).poly([[40, -26], [44, -38], [50, -24]], {fill: "#d9a068", c: "#7b4a22", w: 2})
     .circle(49, -14, 2.5, {fill: C.ink, c: C.ink, w: 1}).curve([-50, 8], [-80, 0], [-90, -30], [-70, -40], {c: "#7b4a22", w: 7})
     .line([[-30, 14], [-36, 30]], {c: "#7b4a22", w: 4}).line([[24, 10], [30, 28]], {c: "#7b4a22", w: 4});
  },
  trap(P) {     // 机关网罗
    P.arc(0, 30, 60, Math.PI, Math.PI * 2, {fill: "#e6dccb", c: "#6b5a44", w: 3}).line([[-64, 30], [64, 30]], {c: "#6b5a44", w: 3.5});
    for (let i = -2; i <= 2; i++) P.line([[i * 22, 30], [i * 14, -26]], {c: "#6b5a44", w: 1.8});
    P.curve([-54, 6], [-20, -4], [20, -4], [54, 6], {c: "#6b5a44", w: 1.8}).curve([-40, -16], [-14, -24], [14, -24], [40, -16], {c: "#6b5a44", w: 1.8});
  },
  river(P, o) { // 河（横向，len）
    const L = o.len || 600;
    P.poly([[-L / 2, -20], [L / 2, -26], [L / 2, 26], [-L / 2, 22]], {fill: "#cfe6f4", c: "#5b9bd5", w: 2.6});
    for (let x = -L / 2 + 40; x < L / 2 - 40; x += 120) P.curve([x, 2], [x + 20, -8], [x + 40, 10], [x + 60, 0], {c: "#8ec0e4", w: 2.4});
  },
  hut(P) {      // 茅草小屋（船夫的家），±90
    P.poly([[-80, 0], [80, 0], [80, 70], [-80, 70]], {fill: "#e3cfa8", c: "#7b5a3a"})
     .poly([[-108, 6], [0, -82], [108, 6]], {fill: "#d9b45a", c: "#8a6a3a"})
     .poly([[-20, 70], [-20, 22], [20, 22], [20, 70]], {fill: "#9b6b43", c: "#6b4a2e", w: 2.5});
    for (let i = -3; i <= 3; i++) P.line([[i * 22, -60 + Math.abs(i) * 14], [i * 30, 4]], {c: "#a8863a", w: 1.6, amp: .8});
  },
  cage_dead(P) { // 金色鸟笼，里面的鸟死了（仰面、× 眼）
    P.line([[0, -96], [0, -70]], {c: "#b98710", w: 3}).circle(0, -100, 6, {c: "#b98710", w: 3})
     .poly(bez([-50, 60], [-56, -70], [56, -70], [50, 60], 20), {c: "#c99a10", w: 3.5})
     .line([[-56, 60], [56, 60]], {c: "#b98710", w: 6});
    for (let i = -2; i <= 2; i++) P.line([[i * 20, -62 + Math.abs(i) * 4], [i * 22, 58]], {c: "#d9b33a", w: 2});
    P.ellipse(0, 46, 22, 12, {fill: "#f0c64c", c: "#8a6a1a", w: 2.5}).circle(22, 42, 9, {fill: "#f0c64c", c: "#8a6a1a", w: 2.5})
     .line([[18, 38], [26, 46]], {w: 2}).line([[26, 38], [18, 46]], {w: 2})
     .line([[-6, 34], [-10, 22]], {c: "#c0703a", w: 2.5}).line([[6, 34], [8, 22]], {c: "#c0703a", w: 2.5});
  },
  wren(P) {     // 鹪鹩：一根树枝上的小鸟窝
    P.line([[-70, 20], [70, 6]], {c: "#6b4a2e", w: 6}).line([[30, 10], [60, -14]], {c: "#6b4a2e", w: 4}).ellipse(62, -18, 12, 7, {fill: "#9cc27e", c: C.green, w: 2})
     .poly(bez([-36, 14], [-38, 34], [8, 34], [6, 12], 12), {fill: "#b98b5e", c: "#6b4a2e", w: 2.5})
     .circle(-14, -4, 14, {fill: "#c9a07a", c: "#6b4a2e", w: 2.5}).circle(-4, -20, 9, {fill: "#c9a07a", c: "#6b4a2e", w: 2.5})
     .poly([[4, -22], [12, -19], [4, -16]], {fill: "#e0a040", c: "#8a5a1a", w: 1.5}).circle(-2, -22, 1.8, {fill: C.ink, c: C.ink, w: 1});
  },
  mole(P) {     // 偃鼠：在河边喝水（朝右）
    P.ellipse(-6, 6, 34, 20, {fill: "#8a7a6a", c: "#4a3e34", w: 3}).circle(26, 10, 13, {fill: "#9a8a7a", c: "#4a3e34", w: 3})
     .circle(38, 16, 4, {fill: "#e79a92", c: "#b0605a", w: 1.5}).line([[22, 4], [27, 1]], {w: 2}).curve([-40, 10], [-54, 14], [-58, 4], [-62, 8], {c: "#4a3e34", w: 2.5})
     .ellipse(4, 16, 24, 12, {fill: "#f1c9a6", c: "#4a3e34", w: 2});
  },
  torch(P) {    // 小火把（爝火）
    P.line([[0, 50], [0, -10]], {c: "#8a5a3a", w: 7}).poly(bez([0, -60], [26, -30], [16, -8], [0, -8], 10).concat(bez([0, -8], [-16, -8], [-26, -30], [0, -60], 10)), {fill: "#f5a33c", c: "#c2621e", w: 2.5});
  },
  yak(P) {      // 斄牛：大得像天边的云
    P.poly([[-80, -20], [-60, -50], [30, -54], [70, -30], [74, 20], [-76, 24]], {fill: "#5a4a40", c: "#2d2420", w: 3.4})
     .circle(84, -16, 22, {fill: "#6a5a50", c: "#2d2420", w: 3}).curve([80, -36], [96, -60], [110, -56], [106, -40], {c: "#e8dcc4", w: 4})
     .line([[-60, 24], [-60, 60]], {c: "#2d2420", w: 8}).line([[50, 24], [50, 60]], {c: "#2d2420", w: 8});
    for (let i = 0; i < 6; i++) P.line([[-60 + i * 24, 20], [-64 + i * 24, 36]], {c: "#3a2e28", w: 3});
  },
  seal(P, o) {  // 红色印章字
    P.poly([[-50, -50], [50, -50], [50, 50], [-50, 50]], {fill: "#c23b2e", c: "#8e2a20", w: 3});
    const tx = o.text || "逍遥"; [...tx].forEach((ch, i) => P.text(ch, 0, (i - (tx.length - 1) / 2) * 42, 40, "#fff5ea", KAI));
  },
  wave_big(P, o) { // 大浪（横向，len）
    const L = o.len || 1200;
    const top = []; for (let x = -L / 2; x <= L / 2; x += 30) top.push([x, Math.sin(x * .018) * 14]);
    P.poly(top.concat([[L / 2, 160], [-L / 2, 160]]), {fill: "#3f6d96", c: "#244a6e", w: 3});
    for (let x = -L / 2 + 60; x < L / 2 - 60; x += 180) P.curve([x, 50], [x + 30, 34], [x + 60, 64], [x + 90, 48], {c: "#8ab2d6", w: 3});
    for (let x = -L / 2 + 150; x < L / 2 - 60; x += 220) P.curve([x, 110], [x + 30, 94], [x + 60, 124], [x + 90, 108], {c: "#6f98bf", w: 3});
  },
  splash(P) {   // 水花
    for (let i = 0; i < 7; i++) { const a = -Math.PI * (.1 + i * .8 / 6); P.line([[Math.cos(a) * 30, Math.sin(a) * 20], [Math.cos(a) * 80, Math.sin(a) * 70]], {c: "#5b9bd5", w: 4}); }
    P.circle(-60, -60, 8, {fill: "#bfe0f2", c: "#5b9bd5", w: 2}).circle(50, -80, 7, {fill: "#bfe0f2", c: "#5b9bd5", w: 2}).circle(0, -90, 6, {fill: "#bfe0f2", c: "#5b9bd5", w: 2});
  },
  whirl(P) {    // 扶摇：盘旋上升的旋风
    const pts = []; for (let i = 0; i <= 120; i++) { const u = i / 120, a = u * Math.PI * 7, r = 20 + u * 70; pts.push([Math.cos(a) * r, 80 - u * 190 + Math.sin(a) * r * .25]); }
    P.line(pts, {c: "#7d8fa0", w: 3.5});
  },
  // 时间线事件的小图标
  village(P) { P.poly([[-40, 10], [-20, -12], [0, 10]], {fill: "#e9c9a0", c: "#8a6a3a"}).poly([[-36, 10], [-4, 10], [-4, 36], [-36, 36]], {fill: "#f4e4c8", c: "#8a6a3a"})
    .poly([[4, 16], [22, -4], [40, 16]], {fill: "#e9c9a0", c: "#8a6a3a"}).poly([[8, 16], [36, 16], [36, 36], [8, 36]], {fill: "#f4e4c8", c: "#8a6a3a"}).line([[-50, 38], [50, 38]], {c: C.green}); },
  war(P) { P.line([[-30, -30], [30, 30]], {w: 5, c: "#666"}).line([[30, -30], [-30, 30]], {w: 5, c: "#666"}).line([[-38, 18], [-18, 38]], {w: 4, c: "#7a5230"}).line([[38, 18], [18, 38]], {w: 4, c: "#7a5230"}); },
  crown(P) { P.poly([[-34, 20], [-38, -20], [-16, 0], [0, -30], [16, 0], [38, -20], [34, 20]], {fill: "#f0c64c", c: "#b98710"}); },
  train(P) { P.poly([[-44, -20], [20, -20], [20, 20], [-44, 20]], {fill: "#c4cfd8", c: C.ink}).poly([[20, -6], [44, -6], [44, 20], [20, 20]], {fill: "#c4cfd8", c: C.ink})
    .line([[30, -6], [30, -26]], {w: 5}).circle(-30, 26, 8, {fill: "#555"}).circle(0, 26, 8, {fill: "#555"}).circle(32, 26, 8, {fill: "#555"}); },
  drop(P) { P.poly(bez([0, -36], [30, 0], [24, 30], [0, 30], 14).concat(bez([0, 30], [-24, 30], [-30, 0], [0, -36], 14)), {fill: "#e79a92", c: C.red}); },
  cloud(P) { P.poly([[-44, 6], [-40, -14], [-20, -26], [4, -30], [26, -20], [42, -2], [30, 12], [-30, 12]], {fill: "#d5dde3", c: "#6c7a86"});
    for (let i = 0; i < 4; i++) P.line([[-26 + i * 16, 20], [-32 + i * 16, 38]], {c: C.blue, w: 2.5}); },
  ruin(P) { P.poly([[-36, 30], [-36, -6], [-10, -26], [4, -12], [10, 30]], {fill: "#d9cfbf", c: "#7b6f60"}).line([[-20, 4], [-6, 14]], {c: "#7b6f60", w: 2})
    .line([[18, 30], [26, 12], [36, 30]], {c: "#7b6f60", w: 2}); },
  spiral(P) { const pts = []; for (let i = 0; i <= 80; i++) { const a = i / 80 * Math.PI * 5, r = 4 + i * .45; pts.push([r * Math.cos(a), r * Math.sin(a)]); } P.line(pts, {c: "#6c7a86", w: 3}); },
  // ── 古代 / 罗马 / 战争（坐标 ±60 左右；人物 hold 也能用） ──
  wolf(P, o) {   // 母狼喂双胞胎（朝右），±120
    const fur = o.color || "#a08c70", dk = "#5e4f3e";
    P.poly([[-72, -22], [-96, -16], [-118, 6], [-112, 24], [-96, 12], [-78, -4]], {fill: fur, c: dk});            // 蓬松的尾巴
    for (const [x0, x1] of [[-58, -62], [-38, -34], [36, 40], [54, 60]]) P.poly([[x0 - 6, 4], [x0 + 6, 4], [x1 + 6, 52], [x1 + 12, 58], [x1 - 6, 58]], {fill: fur, c: dk, w: 2.5});
    P.poly([[-82, -16], [-66, -40], [-30, -50], [20, -48], [52, -40], [64, -22], [58, 4], [30, 12], [-20, 14], [-64, 10]], {fill: fur, c: dk});
    P.poly([[48, -36], [58, -60], [62, -78], [72, -60], [82, -64], [90, -80], [92, -58], [104, -52], [130, -42], [128, -30], [102, -24], [76, -20], [58, -16]], {fill: fur, c: dk});
    P.circle(98, -48, 3, {fill: C.ink, c: C.ink, w: 1.5}).circle(128, -38, 3.5, {fill: C.ink, c: C.ink, w: 1.5});
    for (let i = 0; i < 5; i++) P.line([[-50 + i * 18, -44], [-44 + i * 18, -36]], {c: dk, w: 1.6});
    for (const [x, f] of [[-22, 1], [12, -1]]) {   // 两个婴儿，仰着头
      P.ellipse(x - 12 * f, 40, 17, 10, {fill: "#f2c9a8", c: C.ink, w: 2.2, noHatch: true}).circle(x + 4 * f, 24, 11, {fill: "#f2c9a8", c: C.ink, w: 2.2, noHatch: true})
       .line([[x + 2 * f, 34], [x + 8 * f, 14]], {c: C.ink, w: 2.4}).circle(x + 7 * f, 21, 1.6, {fill: C.ink, c: C.ink, w: 1});
    }
  },
  temple(P, o) {   // 罗马神庙 / 元老院正面，±100
    const m = o.color || "#efe8da";
    P.poly([[-100, 52], [100, 52], [100, 62], [-100, 62]], {fill: m}).poly([[-90, 40], [90, 40], [90, 52], [-90, 52]], {fill: m});
    for (let i = 0; i < 6; i++) { const x = -66 + i * 26.4; P.poly([[x - 6, -46], [x + 6, -46], [x + 6, 40], [x - 6, 40]], {fill: m, w: 2.2}); }
    P.poly([[-84, -60], [84, -60], [84, -46], [-84, -46]], {fill: m}).poly([[-90, -60], [0, -96], [90, -60]], {fill: m});
    if (o.text) P.text(o.text, 0, -53, 13, C.ink, LATIN);
  },
  colosseum(P) {   // 斗兽场（右边高、左上角残缺），±100
    const s = "#e2cfa6";
    P.poly([[-96, 60], [-96, -14], [-80, -30], [-50, -40], [-30, -30], [-10, -46], [30, -54], [70, -50], [96, -40], [96, 60]], {fill: s});
    for (const y of [-12, 14, 38]) P.line([[-96, y], [96, y - 4]], {w: 2});
    for (let r = 0; r < 3; r++) for (let i = 0; i < 8; i++) { const x = -84 + i * 23, y = 32 - r * 25;
      if (r === 2 && i < 2) continue; P.arc(x, y, 7, Math.PI, 0, {w: 1.8}).line([[x - 7, y], [x - 7, y + 12]], {w: 1.6}).line([[x + 7, y], [x + 7, y + 12]], {w: 1.6}); }
  },
  arch(P) { P.poly([[-70, 60], [-70, -50], [70, -50], [70, 60], [32, 60], [32, 0], [0, -30], [-32, 0], [-32, 60]], {fill: "#e9e0cc"}).poly([[-78, -50], [78, -50], [78, -70], [-78, -70]], {fill: "#e9e0cc"}).text("SPQR", 0, -60, 14, C.ink, LATIN); },
  aqueduct(P) {
    P.poly([[-110, -50], [110, -50], [110, -38], [-110, -38]], {fill: "#e3d3b0"});
    for (let i = 0; i < 5; i++) { const x = -88 + i * 44; P.poly([[x - 22, -38], [x + 22, -38], [x + 22, 60], [x + 12, 60], [x + 12, 0], [x, -14], [x - 12, 0], [x - 12, 60], [x - 22, 60]], {fill: "#e3d3b0", w: 2.2}); }
  },
  galley(P, o) {   // 战船（朝右）：桨、方帆、撞角，±100
    for (let i = 0; i < 8; i++) P.line([[-60 + i * 17, 14], [-74 + i * 17, 44]], {c: "#6b4a2e", w: 3});
    P.poly([[-96, 6], [-80, 0], [84, 0], [106, -10], [98, 10], [70, 24], [-66, 24]], {fill: o.color || "#8a5a3a", c: C.ink});
    P.line([[-70, 8], [78, 8]], {c: "#5a3a22", w: 2}).circle(84, 8, 3, {fill: C.ink, w: 1});
    P.line([[0, 0], [0, -84]], {w: 4, c: "#6b4a2e"}).poly([[-40, -78], [40, -78], [36, -16], [-36, -16]], {fill: o.sail || "#f2e6c8", c: C.ink, w: 2.5});
    P.line([[-38, -47], [38, -47]], {c: o.stripe || C.red, w: 6});
  },
  elephant(P, o) {   // 战象（朝右），±100
    const g = o.color || "#a3a29a", dk = "#6c6b64";
    for (const [x, d] of [[-50, 0], [-26, 2], [26, 0], [48, 2]]) P.poly([[x - 11, 10], [x + 11, 10], [x + 12 + d, 56], [x - 12 + d, 56]], {fill: g, c: dk});
    P.line([[-76, -18], [-92, 8]], {c: dk, w: 4});
    P.poly([[-80, -10], [-70, -46], [-20, -60], [40, -56], [70, -40], [74, -6], [56, 22], [-60, 22]], {fill: g, c: dk});
    P.poly([[56, -46], [78, -66], [100, -50], [104, -20], [96, 10], [104, 40], [114, 44], [100, 52], [88, 30], [80, -6]], {fill: g, c: dk});
    P.poly([[56, -42], [44, -12], [62, 4], [74, -30]], {fill: "#b9b8b0", c: dk, w: 2.5});
    P.curve([92, -4], [110, 0], [118, -6], [116, -16], {c: "#f6f2e6", w: 5}).circle(90, -40, 3, {fill: C.ink, w: 1.5});
    if (o.tower !== false) P.poly([[-40, -60], [20, -60], [24, -92], [-44, -92]], {fill: "#9b6b43", c: C.ink, w: 2.5}).line([[-44, -82], [24, -82]], {c: "#6b4a2e", w: 2});
  },
  alps(P) {   // 雪山（阿尔卑斯），±120
    P.poly([[-130, 50], [-60, -50], [-10, 10], [40, -80], [130, 50]], {fill: "#b8c3c9", c: "#5e6c76"});
    P.poly([[-60, -50], [-80, -20], [-62, -26], [-48, -16], [-36, -24]], {fill: "#fbfbfb", c: "#5e6c76", w: 2});
    P.poly([[40, -80], [14, -40], [34, -46], [48, -32], [64, -44]], {fill: "#fbfbfb", c: "#5e6c76", w: 2});
  },
  tent(P) { P.poly([[-80, 50], [0, -60], [80, 50]], {fill: "#d9c39a"}).poly([[0, -60], [-22, 50], [22, 50]], {fill: "#7a5a3a"}).line([[0, -60], [0, -84]], {w: 3}).poly([[0, -84], [26, -78], [0, -70]], {fill: C.red, w: 2}); },
  stone_wall(P, o) {   // 石头长城（哈德良长城），len
    const L = o.len || 300;
    P.poly([[-L / 2, 40], [-L / 2, -20], [L / 2, -20], [L / 2, 40]], {fill: "#c9bfae"});
    for (let x = -L / 2; x < L / 2 - 10; x += 36) P.poly([[x + 4, -20], [x + 4, -36], [x + 22, -36], [x + 22, -20]], {fill: "#c9bfae", w: 2});
    for (let y = -6; y < 40; y += 14) for (let x = -L / 2 + ((y / 14) % 2 ? 12 : 0); x < L / 2 - 10; x += 26) P.line([[x, y], [x + 22, y]], {w: 1.2, c: C.soft});
  },
  fire(P, o) {   // 火焰（配 ICON_POS 的 squash / wobble 会跳动），±60
    const s = o.size || 1;
    P.poly([[-40 * s, 50], [-50 * s, 0], [-24 * s, -30], [-20 * s, 0], [0, -70 * s], [16 * s, -14], [36 * s, -40], [50 * s, 10], [40 * s, 50]], {fill: "#e8582a", c: "#a8321a", noHatch: true});
    P.poly([[-24 * s, 50], [-26 * s, 14], [-8 * s, -14], [4 * s, 12], [20 * s, -6], [28 * s, 50]], {fill: "#f5b13a", c: "#d07a20", w: 2});
  },
  palace(P) {   // 金宫
    P.poly([[-100, 50], [-100, -10], [100, -10], [100, 50]], {fill: "#e9c75a"}).poly([[-40, -10], [-30, -50], [0, -64], [30, -50], [40, -10]], {fill: "#f0d36e"});
    for (let i = 0; i < 7; i++) P.line([[-84 + i * 28, -4], [-84 + i * 28, 50]], {c: "#a8831e", w: 3});
    P.poly([[-110, 50], [110, 50], [110, 60], [-110, 60]], {fill: "#d9b44a"});
  },
  throne(P) { P.poly([[-30, -80], [30, -80], [30, 10], [-30, 10]], {fill: "#7a3b6e"}).poly([[-40, 10], [40, 10], [40, 24], [-40, 24]], {fill: "#d9a52b"}).line([[-34, 24], [-34, 60]], {w: 6, c: "#a8831e"}).line([[34, 24], [34, 60]], {w: 6, c: "#a8831e"}); },
  bridge(P) {
    P.line([[-130, 40], [130, 40]], {c: C.blue, w: 3}).line([[-110, 52], [-40, 52]], {c: C.blue, w: 2}).line([[20, 52], [110, 52]], {c: C.blue, w: 2});
    P.poly([[-130, -10], [130, -10], [130, 40], [80, 40], [80, 26], [50, 0], [20, 26], [20, 40], [-20, 40], [-20, 26], [-50, 0], [-80, 26], [-80, 40], [-130, 40]], {fill: "#d6c9b0"});
  },
  city(P, o) {   // 地图上的城市：小城堡 + 名字（cap）
    P.poly([[-26, 18], [-26, -10], [-30, -10], [-30, -22], [-20, -22], [-20, -14], [-6, -14], [-6, -26], [6, -26], [6, -14], [20, -14], [20, -22], [30, -22], [30, -10], [26, -10], [26, 18]], {fill: o.color || "#e9dcc0", c: C.ink, w: 3});
    P.poly([[-6, 18], [-6, 4], [6, 4], [6, 18]], {fill: C.ink, w: 2});
  },
  battle(P) {   // 地图上的战役：交叉的剑 + 红色爆炸
    const st = []; for (let i = 0; i < 16; i++) { const a = i / 16 * Math.PI * 2, r = i % 2 ? 22 : 40; st.push([Math.cos(a) * r, Math.sin(a) * r]); }
    P.poly(st, {fill: "#f2c14e", c: C.red, w: 2.5, noHatch: true});
    P.line([[-30, 30], [30, -30]], {w: 6, c: "#5b5f66"}).line([[30, 30], [-30, -30]], {w: 6, c: "#5b5f66"})
     .line([[-36, 20], [-20, 36]], {w: 5, c: "#7a5230"}).line([[36, 20], [20, 36]], {w: 5, c: "#7a5230"});
  },
  chirho(P) {   // ☧ 凯乐符号 + 光环
    P.circle(0, 0, 62, {c: "#e8b83a", w: 4}).circle(0, 0, 52, {c: "#e8b83a", w: 1.5});
    P.line([[-30, -30], [30, 30]], {c: "#d9a52b", w: 7}).line([[30, -30], [-30, 30]], {c: "#d9a52b", w: 7}).line([[0, -46], [0, 46]], {c: "#d9a52b", w: 7})
     .curve([0, -46], [36, -48], [36, -12], [0, -14], {c: "#d9a52b", w: 7});
  },
  cannon(P) {   // 巨型青铜炮（朝右），±110
    P.poly([[-100, -26], [70, -36], [96, -32], [96, 6], [70, 10], [-100, 10]], {fill: "#a87d3a", c: C.ink});
    for (const x of [-60, -10, 40]) P.line([[x, -32], [x, 8]], {c: "#7a5520", w: 3});
    P.ellipse(96, -13, 6, 19, {fill: "#3a2a1a", c: C.ink, w: 2});
    P.poly([[-90, 10], [60, 10], [70, 40], [-96, 40]], {fill: "#7a5230"}).circle(-50, 44, 16, {fill: "#5a3a22"}).circle(30, 44, 16, {fill: "#5a3a22"});
  },
  cannonball(P) { P.circle(0, 0, 22, {fill: "#4a4440", c: C.ink}); },
  lyre(P) { P.curve([-26, 40], [-56, -10], [-30, -50], [-20, -56], {c: "#d9a52b", w: 6}).curve([26, 40], [56, -10], [30, -50], [20, -56], {c: "#d9a52b", w: 6})
             .line([[-30, -36], [30, -36]], {c: "#a8831e", w: 5}).poly([[-30, 34], [30, 34], [26, 48], [-26, 48]], {fill: "#c08a2a"});
             for (let i = 0; i < 4; i++) P.line([[-12 + i * 8, -36], [-12 + i * 8, 34]], {c: "#6b4a2e", w: 1.2}); },
  dagger(P) { P.poly([[-6, -4], [56, -1], [62, 0], [56, 1], [-6, 4]], {fill: "#c9ced2", c: C.ink, w: 2}).line([[-8, -14], [-8, 14]], {w: 5, c: "#b98710"}).line([[-8, 0], [-32, 0]], {w: 7, c: "#7a3b2a"}); },
  scutum(P, o) { P.poly([[-32, -52], [32, -52], [36, 52], [-36, 52]], {fill: o.color || "#a8322a"}).circle(0, 0, 10, {fill: "#d9a52b", w: 2}).line([[0, -44], [0, -12]], {c: "#d9a52b", w: 3}).line([[0, 12], [0, 44]], {c: "#d9a52b", w: 3}); },
  eagle(P) {   // 罗马军团鹰旗（竖着的长杆，顶上金鹰），±90
    P.line([[0, -40], [0, 90]], {w: 5, c: "#6b4a2e"});
    P.poly([[-18, 0], [18, 0], [18, 24], [-18, 24]], {fill: C.red, w: 2}).text("SPQR", 0, 12, 10, "#f2d27a", LATIN);
    P.poly([[0, -46], [-44, -80], [-30, -54], [-48, -58], [-10, -36]], {fill: "#e2b23c", c: "#9a7410", w: 2})
     .poly([[0, -46], [44, -80], [30, -54], [48, -58], [10, -36]], {fill: "#e2b23c", c: "#9a7410", w: 2})
     .circle(0, -52, 8, {fill: "#e2b23c", c: "#9a7410", w: 2}).poly([[-10, -36], [10, -36], [6, -24], [-6, -24]], {fill: "#e2b23c", c: "#9a7410", w: 2});
  },
  dice(P) { P.poly([[-30, -30], [30, -30], [30, 30], [-30, 30]], {fill: "#f6f0e2"}); for (const [x, y] of [[-15, -15], [15, 15], [0, 0], [15, -15], [-15, 15]]) P.circle(x, y, 4, {fill: C.ink, w: 1}); },
  laurel_big(P, o) {   // 大桂冠（圆环叶子）
    const col = o.color || "#6f8a3a";
    for (let i = 0; i < 22; i++) { const a = Math.PI * .62 + i / 21 * Math.PI * 1.76, x = Math.cos(a) * 50, y = Math.sin(a) * 50;
      P.ellipse(x + Math.cos(a + 1.2) * 8, y + Math.sin(a + 1.2) * 8, 9, 5, {fill: col, c: "#4a5e26", w: 1.6}); }
  },
  crowns(P, o) {   // 一排小皇冠（皇帝的人数）：{n}
    const n = o.n || 8, per = Math.ceil(n / 2);
    for (let i = 0; i < n; i++) { const row = i < per ? 0 : 1, k = row ? i - per : i, x = -(per - 1) * 22 + k * 44 + row * 22, y = -18 + row * 40;
      P.poly([[x - 15, y + 10], [x - 17, y - 10], [x - 7, y], [x, y - 14], [x + 7, y], [x + 17, y - 10], [x + 15, y + 10]], {fill: "#f0c64c", c: "#b98710", w: 2}); }
  },
  crowns_x(P, o) {   // 给上面的皇冠逐个打叉
    const n = o.n || 8, per = Math.ceil(n / 2);
    for (let i = 0; i < n; i++) { const row = i < per ? 0 : 1, k = row ? i - per : i, x = -(per - 1) * 22 + k * 44 + row * 22, y = -18 + row * 40;
      P.line([[x - 13, y - 12], [x + 13, y + 12]], {c: C.red, w: 3.5, amp: .5}).line([[x + 13, y - 12], [x - 13, y + 12]], {c: C.red, w: 3.5, amp: .5}); }
  },
  coin_silver(P, o) { P.circle(0, 0, 40, {fill: o.color || "#d7dade", c: "#7d848c"}).circle(0, 0, 30, {c: "#9aa1a8", w: 1.5})
    .curve([-8, -20], [14, -22], [16, 10], [-2, 20], {c: "#7d848c", w: 2.5}).circle(4, -6, 9, {c: "#7d848c", w: 2}); if (o.text) P.text(o.text, 0, 62, 24, C.soft, KAI); },
  coin_bronze(P, o) { P.circle(0, 0, 34, {fill: "#b98a52", c: "#7a5530"}).circle(0, 0, 25, {c: "#8a6538", w: 1.5}).line([[-10, -30], [-2, -10], [-12, 4]], {c: "#5a3a1a", w: 2})
    .curve([-6, -16], [12, -18], [14, 8], [-2, 16], {c: "#7a5530", w: 2.2}); if (o.text) P.text(o.text, 0, 56, 24, C.soft, KAI); },
  army_r(P, o) {   // 罗马军团方阵（俯视的小方块）
    const cols = o.cols || 6, rows = o.rows || 3, c = o.color || "#b8322a";
    for (let i = 0; i < cols; i++) for (let j = 0; j < rows; j++) P.poly([[-cols * 13 + i * 26 + 3, -rows * 13 + j * 26 + 3], [-cols * 13 + i * 26 + 23, -rows * 13 + j * 26 + 3], [-cols * 13 + i * 26 + 23, -rows * 13 + j * 26 + 23], [-cols * 13 + i * 26 + 3, -rows * 13 + j * 26 + 23]], {fill: c, c: C.ink, w: 1.6, noHatch: true});
  },
  army_c(P, o) {   // 迦太基的新月阵：中间凸、两翼前伸
    const c = o.color || "#6a2c5c", n = o.n || 13, R = o.r || 150;
    for (let i = 0; i < n; i++) { const a = Math.PI * .2 + i / (n - 1) * Math.PI * .6, x = Math.cos(a) * R, y = -Math.sin(a) * R * .6 + R * .25;
      P.poly([[x - 11, y - 9], [x + 11, y - 9], [x + 11, y + 9], [x - 11, y + 9]], {fill: c, c: C.ink, w: 1.6, noHatch: true}); }
  },
  alesia(P) {   // 阿莱西亚：山上的城 + 内外两圈围墙，±130
    P.ellipse(0, 6, 60, 34, {fill: "#d9cba6"});
    P.poly([[-22, 10], [-22, -12], [-10, -20], [8, -20], [22, -12], [22, 10]], {fill: "#e9dcc0", w: 2.4});
    P.ellipse(0, 6, 92, 58, {c: C.red, w: 4, dash: [10, 6]}).ellipse(0, 6, 128, 82, {c: C.red, w: 4, dash: [10, 6]});
  },
  calendar(P) { P.poly([[-44, -40], [44, -40], [44, 46], [-44, 46]], {fill: "#fbf6ea"}).poly([[-44, -40], [44, -40], [44, -20], [-44, -20]], {fill: C.red});
    for (let i = 0; i < 4; i++) for (let j = 0; j < 3; j++) P.poly([[-34 + i * 18, -12 + j * 18], [-24 + i * 18, -12 + j * 18], [-24 + i * 18, -4 + j * 18], [-34 + i * 18, -4 + j * 18]], {fill: "#d9cfbf", w: 1, noHatch: true}); },
  abc(P) { P.text("ABC", 0, 0, 60, C.ink, LATIN); P.line([[-56, 30], [56, 28]], {c: C.red, w: 3}); },
  scales(P) {   // 天平（法律）
    P.line([[0, -50], [0, 44]], {w: 4}).line([[-56, -36], [56, -36]], {w: 4}).line([[-30, 50], [30, 50]], {w: 6});
    for (const x of [-48, 48]) P.line([[x, -36], [x - 18, 0]], {w: 1.6}).line([[x, -36], [x + 18, 0]], {w: 1.6}).arc(x, 0, 18, 0, Math.PI, {fill: "#e2b23c", c: "#9a7410", w: 2.5});
  },
  bricks(P, o) {   // 一摞砖（n 层）
    const n = o.n || 4;
    for (let r = 0; r < n; r++) for (let i = 0; i < 3 - (r % 2); i++) { const x = -60 + i * 40 + (r % 2) * 20, y = 40 - r * 22; P.poly([[x, y], [x + 38, y], [x + 38, y - 20], [x, y - 20]], {fill: "#c46a4a", w: 2}); }
  },
  walls(P, o) {   // 城墙 + 塔楼（君士坦丁堡），len；broken 有缺口
    const L = o.len || 400;
    P.poly([[-L / 2, 60], [-L / 2, -30], [L / 2, -30], [L / 2, 60]], {fill: "#d8ccb4"});
    for (let x = -L / 2; x < L / 2 - 8; x += 30) P.poly([[x + 2, -30], [x + 2, -44], [x + 18, -44], [x + 18, -30]], {fill: "#d8ccb4", w: 2});
    for (const x of [-L / 2 + 40, 0, L / 2 - 40]) P.poly([[x - 28, 60], [x - 28, -70], [x + 28, -70], [x + 28, 60]], {fill: "#cbbd9f"}).poly([[x - 32, -70], [x, -96], [x + 32, -70]], {fill: "#9e3a2a"});
    if (o.broken) P.poly([[60, -46], [90, -10], [74, 20], [110, 60], [150, 60], [140, 10], [160, -30], [150, -46]], {fill: C.paper, c: C.ink, w: 3, noHatch: true});
  },
  galley_fire(P) { ICONS.galley(P, {sail: "#d9c9a8", stripe: "#6a2c5c"}); ICONS.fire(P, {size: .7}); },
  sword_up(P) { P.line([[0, 60], [0, -50]], {w: 7, c: "#9aa3aa"}).line([[-16, 36], [16, 36]], {w: 6, c: "#b98710"}).line([[0, 36], [0, 60]], {w: 7, c: "#7a5230"}); },
  helmet(P) { P.arc(0, 10, 40, Math.PI, 0, {fill: "#b7b0a2", w: 3}).poly([[-20, -28], [0, -60], [20, -28]], {fill: C.red, w: 2}).line([[-44, 10], [44, 10]], {w: 4}); },
  persia(P) {   // 波斯：头戴高冠的国王剪影图标
    P.poly([[-20, -50], [20, -50], [16, -20], [-16, -20]], {fill: "#e2b23c", c: "#9a7410"}).circle(0, -2, 22, {fill: "#d8b48c"}).poly([[-24, 6], [24, 6], [16, 50], [-16, 50]], {fill: "#2c4a66"});
  },
};
Object.assign(ICONS, WD.ICONS || {});   // world.js 里可以用 ICONS = {名字(P, o) {…}} 补充本片专用的涂鸦

// ───────────── 场景元素（预计算几何） ─────────────
const EL = {};   // id -> {kind, bbox, draw(t, st)}
const NAMEF = fT;
const penFont = it => it.font === HAND ? fH(it.size) : it.font === KAI ? fL(it.size) : it.font === TITLE ? fT(it.size) : `${it.size}px ${it.font}`;

function cardGeom(id) {
  const n = NODES[id], big = n.kind !== "out", k = n.s || 1;
  const fs = (big ? 38 : 32) * k, tf = (big ? 22 : 20) * k;
  const w = Math.max((big ? 250 : 220) * k, textW(n.name, NAMEF(fs)) + 60 * k, textW(n.tag || "", fL(tf)) + 50 * k);
  const h = (big ? 108 : 94) * k;
  return {x0: n.x - w / 2, y0: n.y - h / 2, w, h, fs, tf};
}
// 卡片外形：round 圆角 / tablet 切角石碑 / seal 方角宣纸
function cardShape(x, y, w, h, inset = 0) {
  const st = TH.card.style || "round", r = TH.card.radius ?? 16;
  x += inset; y += inset; w -= inset * 2; h -= inset * 2;
  if (st === "tablet") { const c = Math.max(6, r + 8 - inset * .6);
    return [[x + c, y], [x + w - c, y], [x + w, y + c], [x + w, y + h - c], [x + w - c, y + h], [x + c, y + h], [x, y + h - c], [x, y + c]]; }
  if (st === "seal" || st === "index") return [[x, y], [x + w, y], [x + w, y + h], [x, y + h]];
  return roundRectPts(x, y, w, h, Math.max(4, r - inset * .7));
}
function buildCards() {
  for (const id in NODES) {
    const n = NODES[id], g = cardGeom(id), seed = hashStr(id);
    const rr = cardShape(g.x0, g.y0, g.w, g.h);
    const s1 = withLen(sketch(rr.concat([rr[0], rr[1]]), seed, 1.8));
    const s2 = TH.card.style === "tablet" ? withLen(sketch(cardShape(g.x0, g.y0, g.w, g.h, 7).concat([cardShape(g.x0, g.y0, g.w, g.h, 7)[0]]), seed + 7, 1))
                                          : withLen(sketch(rr.concat([rr[0]]), seed + 7, 2.6));
    const cross = [withLen(sketch([[g.x0 + g.w - 26, g.y0 + 12], [g.x0 + g.w - 26, g.y0 + 42]], seed + 3, .8)),
                   withLen(sketch([[g.x0 + g.w - 37, g.y0 + 22], [g.x0 + g.w - 15, g.y0 + 22]], seed + 4, .8))];
    const inner = withLen(sketch(roundRectPts(g.x0 + 7, g.y0 + 7, g.w - 14, g.h - 14, 11).concat([[g.x0 + 7, g.y0 + 18]]), seed + 9, 1.2));
    EL[id] = {kind: "card", n, g, bbox: {x0: g.x0, y0: g.y0, x1: g.x0 + g.w, y1: g.y0 + g.h}, s1, s2, cross, inner, fill: rr, cx: n.x, cy: n.y};
  }
}
function rectExit(g, cx, cy, tx, ty, m = 6) {   // 从卡片中心朝 (tx,ty) 出发，与卡片边框的交点
  const dx = tx - cx, dy = ty - cy, hw = g.w / 2 + m, hh = g.h / 2 + m;
  const k = Math.min(Math.abs(dx) > 1e-6 ? hw / Math.abs(dx) : 1e9, Math.abs(dy) > 1e-6 ? hh / Math.abs(dy) : 1e9);
  return [cx + dx * k, cy + dy * k];
}
function anchorOf(id) {
  if (EL[id] && EL[id].kind === "card") return EL[id];
  const b = EL[id].bbox; return {cx: (b.x0 + b.x1) / 2, cy: (b.y0 + b.y1) / 2, g: {w: b.x1 - b.x0, h: b.y1 - b.y0}};
}
function coupleMid(a, b) {
  const A = anchorOf(a), B = anchorOf(b);
  const pa = rectExit(A.g, A.cx, A.cy, B.cx, B.cy, 2), pb = rectExit(B.g, B.cx, B.cy, A.cx, A.cy, 2);
  return [(pa[0] + pb[0]) / 2, (pa[1] + pb[1]) / 2];
}
const EDGE_STYLE = {
  marry: {c: C.ink, w: 3, dur: .7}, child: {c: C.ink, w: 2.6, dur: .9, arrow: true}, lover: {c: C.soft, w: 2.6, dash: [12, 9], dur: .8},
  love: {c: C.love, w: 3.5, dash: [14, 9], dur: 1.1, heart: true}, reject: {c: C.out, w: 2.4, dash: [8, 8], dur: .8, x: true},
  adopt: {c: C.soft, w: 2.4, dash: [3, 8], dur: 1, arrow: true}, blood: {c: C.red, w: 6, dur: 3.2}, special: {c: C.red, w: 4, dur: 2.2, arrow: true, dash: [16, 10]},
  dotted: {c: "#8a6a3a", w: 2.6, dash: [3, 9], dur: 1.2, arrow: true},
};
function buildEdges() {
  for (const id in EDGES) {
    const e = EDGES[id], st = EDGE_STYLE[e.type], seed = hashStr(id);
    let pts;
    if (e.type === "child" || e.type === "adopt") {
      const s = coupleMid(e.from[0], e.from[1]), T = EL[e.to];
      const end = [T.cx, T.g ? T.cy - T.g.h / 2 - 6 : T.cy];
      const k = (end[1] - s[1]) * .5;
      pts = bez(s, [s[0], s[1] + k], [end[0], end[1] - k], end, 50);
    } else if (e.type === "blood") {
      const A = EL[e.a], B = EL[e.b];
      const p0 = [A.cx + 40, A.cy - A.g.h / 2 - 4], p1 = [B.cx - 40, B.cy + B.g.h / 2 + 4];
      pts = [];
      for (let i = 0; i <= 120; i++) {   // 蜿蜒的血流：先在村子里拐几个弯，再流向母亲
        const u = i / 120, x = lerp(p0[0], p1[0], u), y = lerp(p0[1], p1[1], u) + Math.sin(u * Math.PI) * -120;
        pts.push([x + Math.sin(u * 17) * 26 * Math.sin(u * Math.PI), y + Math.cos(u * 11) * 20 * Math.sin(u * Math.PI)]);
      }
    } else {
      const A = anchorOf(e.a), B = anchorOf(e.b);
      const pa = rectExit(A.g, A.cx, A.cy, B.cx, B.cy), pb = rectExit(B.g, B.cx, B.cy, A.cx, A.cy);
      const L = Math.hypot(pb[0] - pa[0], pb[1] - pa[1]), bend = e.bend ?? (e.type === "lover" ? .08 : 0);
      const nx = -(pb[1] - pa[1]) / L, ny = (pb[0] - pa[0]) / L;
      const c = [(pa[0] + pb[0]) / 2 + nx * L * bend, (pa[1] + pb[1]) / 2 + ny * L * bend];
      pts = quad(pa, c, pb, 50);
    }
    const main = withLen(sketch(pts, seed, e.type === "blood" ? 3 : 1.8));
    const extra = [];
    if (e.type === "marry") {   // 夫妻：双线
      const off = pts.map(([x, y], i) => { const j = Math.min(i + 1, pts.length - 1), k = Math.max(i - 1, 0);
        const dx = pts[j][0] - pts[k][0], dy = pts[j][1] - pts[k][1], l = Math.hypot(dx, dy) || 1; return [x - dy / l * 7, y + dx / l * 7]; });
      extra.push(withLen(sketch(off, seed + 1, 1.8)));
    }
    let arrow = null;
    if (st.arrow) {
      const n = pts.length, [x1, y1] = pts[n - 1], [x0, y0] = pts[n - 5];
      const a = Math.atan2(y1 - y0, x1 - x0), L = 16;
      arrow = withLen(sketch([[x1 - L * Math.cos(a - .45), y1 - L * Math.sin(a - .45)], [x1, y1], [x1 - L * Math.cos(a + .45), y1 - L * Math.sin(a + .45)]], seed + 2, .6));
    }
    const mid = pts[Math.floor(pts.length / 2)];
    let x0 = 1e9, y0 = 1e9, x1 = -1e9, y1 = -1e9;
    for (const [x, y] of pts) { x0 = Math.min(x0, x); y0 = Math.min(y0, y); x1 = Math.max(x1, x); y1 = Math.max(y1, y); }
    EL[id] = {kind: "edge", e, st, main: Object.assign(main, {c: st.c, w: st.w, dash: st.dash}), extra: extra.map(s => Object.assign(s, {c: st.c, w: st.w})),
              arrow: arrow && Object.assign(arrow, {c: st.c, w: st.w}), mid, bbox: {x0: x0 - 20, y0: y0 - 20, x1: x1 + 20, y1: y1 + 20}, ends: [e.a || e.from?.[0], e.b || e.to]};
  }
}
function buildIcons() {
  for (const id in ICON_POS) {
    const p = ICON_POS[id], P = new Pen(hashStr(id));
    const draw = ICONS[p.icon || id];
    if (!draw) { console.warn("没有这个涂鸦", p.icon || id); continue; }
    draw(P, p);
    if (p.flip) for (const it of P.items) { if (it.kind === "s") { it.pts = it.pts.map(([x, y]) => [-x, y]); if (it.fillPts) it.fillPts = it.fillPts.map(([x, y]) => [-x, y]); } else it.x = -it.x; }
    if (p.cap) P.text(p.cap, 0, p.capY ?? 78, p.capSize ?? 30, p.capColor || C.soft, HAND);
    P.transform(p.x, p.y, p.s ?? 1);
    EL[id] = {kind: "icon", P, bbox: P.bbox(), cx: p.x, cy: p.y};
  }
}
function buildEvents() {
  const axis = withLen(sketch([[TL_X, TLM.y0], [TL_X, TLM.y1]], 77, 2));
  EL.__axis = {kind: "axis", axis: Object.assign(axis, {c: C.ink, w: 3.5}), bbox: {x0: TL_X - 60, x1: TL_X + 60, y0: TLM.y0 - 70, y1: TLM.y1}};
  for (const id in EVENTS) {
    const e = EVENTS[id], seed = hashStr(id);
    const w = Math.max(330, textW(e.sub, fL(24)) + 140), h = 96, x0 = TL_X + 46, y0 = e.y - h / 2;
    const rr = roundRectPts(x0, y0, w, h, 14);
    const P = new Pen(seed); (ICONS[e.icon] || ICONS.star)(P, {}); P.transform(x0 + 52, e.y, .62);
    EL[id] = {kind: "ev", e, x0, y0, w, h, P, rr, s1: withLen(sketch(rr.concat([rr[0], rr[1]]), seed, 1.6)),
              dot: withLen(sketch(ellipsePts(TL_X, e.y, 11, 11), seed + 1, .8)), tick: withLen(sketch([[TL_X + 11, e.y], [x0, e.y]], seed + 2, .8)),
              bbox: {x0: TL_X - 20, y0, x1: x0 + w, y1: y0 + h}, cx: x0 + w / 2, cy: e.y};
  }
}
function buildGroups() {
  for (const id in GROUPS) {
    const g = GROUPS[id], rr = roundRectPts(g.x0, g.y0, g.x1 - g.x0, g.y1 - g.y0, 26);
    EL[id] = {kind: "group", g, rr, s: Object.assign(withLen(sketch(rr.concat([rr[0]]), hashStr(id), 2.5)), {c: C.out, w: 2.4, dash: [14, 10]}),
              bbox: {x0: g.x0, y0: g.y0 - 60, x1: g.x1, y1: g.y1}};
  }
}
function buildNotes() {
  for (const id in NOTES) {
    const n = NOTES[id], lines = n.text.split("\n"), font = fH(n.size);
    const w = Math.max(...lines.map(l => textW(l, font))), h = lines.length * n.size * 1.15;
    const el = {kind: "note", n, lines, font, w, h, bbox: {x0: n.x - w / 2 - 30, y0: n.y - h / 2 - 26, x1: n.x + w / 2 + 30, y1: n.y + h / 2 + 26}, cx: n.x, cy: n.y};
    if (n.style === "legend") { el.bbox = {x0: n.x - 40, y0: n.y - 30, x1: n.x + w + 20, y1: n.y + 30};
      el.sw = Object.assign(withLen(sketch(roundRectPts(n.x - 36, n.y - 16, 32, 32, 7).concat([[n.x - 36, n.y - 9]]), hashStr(id), 1.2)), {c: n.color, w: 3}); }
    if (n.style === "title") el.under = Object.assign(withLen(sketch(bez([n.x - w / 2, n.y + n.size * .6], [n.x - w / 6, n.y + n.size * .5], [n.x + w / 6, n.y + n.size * .75], [n.x + w / 2, n.y + n.size * .58]), 5, 3)), {c: C.red, w: 6});
    if (n.style === "circle") el.loop = Object.assign(withLen(sketch(ellipsePts(n.x, n.y, w / 2 + 40, n.size * .9, -1.2, 4.6), 8, 3)), {c: n.color, w: 3});
    if (n.style === "sticky") el.rot = ((hashStr(id) % 7) - 3) * .012;
    EL[id] = el;
  }
}

// ───────────── 地图（MAPS）与行军路线（ROUTES） ─────────────
// MAPS.id = {x, y, k, lon0, lat0, box: [lon0, lat0, lon1, lat1], title, mountains: [[[lon,lat],…]], rivers: [名字…],
//            eras: {id: {label, sub, center: [lon,lat], regions: [{color, polys: [[[lon,lat]…]…], isles: [[lon,lat]…], alpha}]}}}
// 地图数据来自 web/geo.js（src/make_geo.py 生成）。指令：map ID（画出地图）、era 地图ID 时期ID（版图从 center 向外晕开；era 地图ID none 清空）
// 版图多边形只在大陆上生效（海里画多了没关系）；岛屿的中心落在多边形里就整块涂上，或者在 isles 里点名。
const MAPS = WD.MAPS || {}, ROUTES = WD.ROUTES || {};
const GEO = window.GEO || {land: [], lakes: [], rivers: {}};
function projector(m) { const cl = Math.cos((m.lat0 || 0) * Math.PI / 180); return (lon, lat) => [m.x + (lon - m.lon0) * m.k * cl, m.y - (lat - m.lat0) * m.k]; }
function pathOf(rings, closed = true) {
  const P = new Path2D();
  for (const r of rings) { r.forEach((p, i) => i ? P.lineTo(p[0], p[1]) : P.moveTo(p[0], p[1])); if (closed) P.closePath(); }
  return P;
}
function inPoly(pt, poly) {   // 射线法
  let c = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const [xi, yi] = poly[i], [xj, yj] = poly[j];
    if ((yi > pt[1]) !== (yj > pt[1]) && pt[0] < (xj - xi) * (pt[1] - yi) / (yj - yi) + xi) c = !c;
  }
  return c;
}
function ringArea(r) { let a = 0; for (let i = 0, j = r.length - 1; i < r.length; j = i++) a += (r[j][0] + r[i][0]) * (r[j][1] - r[i][1]); return Math.abs(a / 2); }
function ringCentroid(r) { let x = 0, y = 0; for (const p of r) { x += p[0]; y += p[1]; } return [x / r.length, y / r.length]; }
const HATCH = {};
function hatchPattern(col) {   // 斜线排线的小图块（给版图上色用）
  if (HATCH[col]) return HATCH[col];
  const c = document.createElement("canvas"); c.width = c.height = 24; const g = c.getContext("2d");
  g.strokeStyle = col; g.lineWidth = 2.2; g.lineCap = "square"; g.beginPath();
  for (const o of [-24, 0, 24]) { g.moveTo(o, 24); g.lineTo(o + 24, 0); }
  g.stroke();
  return HATCH[col] = ctx.createPattern(c, "repeat");
}
function buildMaps() {
  for (const id in MAPS) {
    const m = MAPS[id], P = projector(m), seed = hashStr(id);
    const box = m.box || [-12, 22, 50, 57];
    const [fx0, fy1] = P(box[0], box[1]), [fx1, fy0] = P(box[2], box[3]);
    const main = [], isl = [], all = [];
    GEO.land.forEach((ring, i) => {
      const pts = sketch(ring.map(([lo, la]) => P(lo, la)), seed + i, .9, true);
      all.push(pts);
      if (ringArea(ring) > 60) main.push(pts); else isl.push({pts, c: ringCentroid(ring), path: pathOf([pts])});
    });
    const lakes = GEO.lakes.map(r => r.map(([lo, la]) => P(lo, la)));
    const rivers = [];
    for (const k in GEO.rivers) if (!m.rivers || m.rivers.includes(k)) for (const ln of GEO.rivers[k]) rivers.push(sketch(ln.map(([lo, la]) => P(lo, la)), seed + rivers.length * 7, .6));
    // 山脉：沿折线每隔一段画一个小山尖
    const peaks = [];
    (m.mountains || []).forEach((line, li) => {
      const pts = line.map(([lo, la]) => P(lo, la)), r = rng(seed + 100 + li);
      for (let i = 0; i < pts.length - 1; i++) {
        const [x0, y0] = pts[i], [x1, y1] = pts[i + 1], L = Math.hypot(x1 - x0, y1 - y0), n = Math.max(1, Math.round(L / 34));
        for (let k = 0; k < n; k++) { const u = (k + r()) / n; peaks.push([lerp(x0, x1, u) + (r() - .5) * 14, lerp(y0, y1, u) + (r() - .5) * 14, 11 + r() * 8]); }
      }
    });
    peaks.sort((a, b) => a[1] - b[1]);
    const eras = {};
    for (const eid in m.eras || {}) {
      const e = m.eras[eid];
      const regions = (e.regions || []).map(rg => {
        const polys = rg.polys.map(pl => pl.map(([lo, la]) => P(lo, la)));
        const isles = isl.filter(it => rg.polys.some(pl => inPoly(it.c, pl)) || (rg.isles || []).some(([lo, la]) => Math.hypot(lo - it.c[0], la - it.c[1]) < 1.2));
        return {...rg, path: pathOf(polys), isles};
      });
      eras[eid] = {...e, regions, c: P(...(e.center || [12.5, 41.9]))};
    }
    const diag = Math.hypot(fx1 - fx0, fy1 - fy0);
    EL[id] = {kind: "map", m, P, frame: [fx0, fy0, fx1, fy1], landPath: pathOf(all), mainPath: pathOf(main), isl, lakes: pathOf(lakes),
              rivers: pathOf(rivers, false), peaks, eras, diag, cx: (fx0 + fx1) / 2, cy: (fy0 + fy1) / 2,
              border: withLen(sketch(roundRectPts(fx0 - 14, fy0 - 14, fx1 - fx0 + 28, fy1 - fy0 + 28, 4).concat([[fx0 - 14, fy0 - 4]]), seed + 5, 1.6)),
              bbox: {x0: fx0 - 30, y0: fy0 - 30, x1: fx1 + 30, y1: fy1 + 30}};
  }
}
function eraAt(id, t) {
  let cur = null, prev = null;
  for (const e of S.eras[id] || []) { if (e.t > t) break; prev = cur; cur = e; }
  return {cur, prev};
}
function drawRegions(el, eid, a, reveal) {
  const e = el.eras[eid]; if (!e || a <= 0) return;
  for (const rg of e.regions) {
    const col = rg.color || C.red;
    ctx.save();
    if (reveal != null) { ctx.beginPath(); ctx.arc(e.c[0], e.c[1], Math.max(1, reveal), 0, 7); ctx.clip(); }
    const paint = path => {
      ctx.globalAlpha = a * (rg.alpha ?? .34); ctx.fillStyle = col; ctx.fill(path);
      ctx.globalAlpha = a * .45; ctx.fillStyle = hatchPattern(col); ctx.fill(path);
    };
    ctx.save(); ctx.clip(el.mainPath); paint(rg.path);
    ctx.globalAlpha = a * .9; ctx.strokeStyle = col; ctx.lineWidth = 4 * LW; ctx.setLineDash([16, 8]); ctx.stroke(rg.path); ctx.setLineDash([]);
    ctx.restore();
    for (const it of rg.isles) paint(it.path);
    ctx.restore();
  }
  ctx.globalAlpha = 1;
}
function drawMap(id, t) {
  const el = EL[id], t0 = S.show[id]; if (t0 == null || t < t0) return;
  const a = elemAlpha(id, t); if (a <= .003) return;
  const {m, frame: [x0, y0, x1, y1]} = el, ta = t - t0;
  const k = ease(ta / 1.8);
  ctx.save();
  // 纸张阴影 + 海
  fillPoly([[x0 - 8, y0 - 6], [x1 + 14, y0 - 2], [x1 + 18, y1 + 16], [x0 - 4, y1 + 12]], "#000", a * .12 * k);
  ctx.save();
  ctx.beginPath(); ctx.arc(el.cx, el.cy, el.diag * .55 * k + 1, 0, 7); ctx.clip();       // 从中间晕开
  ctx.beginPath(); ctx.rect(x0, y0, x1 - x0, y1 - y0); ctx.clip();
  ctx.globalAlpha = a; ctx.fillStyle = C.sea; ctx.fillRect(x0, y0, x1 - x0, y1 - y0);
  // 海岸的水纹（古地图的晕线）
  const seaD = mix(C.sea, C.ink, .35);
  ctx.lineJoin = "round";
  for (const [w, al] of [[34, .07], [20, .1], [9, .16]]) { ctx.globalAlpha = a * al; ctx.strokeStyle = seaD; ctx.lineWidth = w; ctx.stroke(el.landPath); }
  ctx.globalAlpha = a; ctx.fillStyle = C.land; ctx.fill(el.landPath);
  ctx.globalAlpha = a * .9; ctx.fillStyle = C.sea; ctx.fill(el.lakes);
  // 版图
  const {cur, prev} = eraAt(id, t);
  if (prev && prev.era !== "none") drawRegions(el, prev.era, a * (1 - clamp((t - cur.t - .8) / 1.6)), null);
  if (cur && cur.era !== "none") drawRegions(el, cur.era, a, ease((t - cur.t) / 2.6) * el.diag);
  // 河、山、海岸线
  ctx.globalAlpha = a * .8; ctx.strokeStyle = mix(C.blue, C.sea, .1); ctx.lineWidth = 2.6; ctx.lineCap = "round"; ctx.stroke(el.rivers);
  ctx.globalAlpha = a * .85; ctx.strokeStyle = C.soft; ctx.fillStyle = mix(C.land, C.ink, .16); ctx.lineWidth = 2;
  for (const [x, y, h] of el.peaks) {
    ctx.beginPath(); ctx.moveTo(x, y - h * .55); ctx.lineTo(x + h * .8, y + h * .45); ctx.lineTo(x + h * .1, y + h * .45); ctx.closePath(); ctx.fill();
    ctx.beginPath(); ctx.moveTo(x - h * .8, y + h * .45); ctx.lineTo(x, y - h * .55); ctx.lineTo(x + h * .8, y + h * .45); ctx.stroke();
  }
  ctx.globalAlpha = a; ctx.strokeStyle = C.ink; ctx.lineWidth = 2.4 * LW; ctx.stroke(el.landPath);
  ctx.globalAlpha = a * .9; ctx.lineWidth = 1.2; ctx.stroke(el.lakes);
  ctx.restore();
  // 画框、标题、指北针
  drawStroke(Object.assign(el.border, {c: C.ink, w: 4}), ta / 1.4, a);
  ctx.globalAlpha = a * clamp((ta - 1) / .5); ctx.strokeStyle = C.ink; ctx.lineWidth = 1.4; ctx.strokeRect(x0 - 4, y0 - 4, x1 - x0 + 8, y1 - y0 + 8); ctx.globalAlpha = 1;
  if (m.title) revealText(m.title, (x0 + x1) / 2, y0 - 60, `italic 600 ${m.titleSize || 58}px ${LATIN}`, C.ink, (ta - .6) / 1, "center", a);
  const cxr = x1 - 110, cyr = y1 - 130;
  if (ta > 1.2) { ctx.save(); ctx.globalAlpha = a * clamp((ta - 1.2) / .6); ctx.strokeStyle = C.ink; ctx.fillStyle = C.red; ctx.lineWidth = 2;
    ctx.beginPath(); ctx.arc(cxr, cyr, 44, 0, 7); ctx.stroke(); ctx.beginPath(); ctx.moveTo(cxr, cyr - 64); ctx.lineTo(cxr + 12, cyr); ctx.lineTo(cxr, cyr + 64); ctx.lineTo(cxr - 12, cyr); ctx.closePath(); ctx.stroke();
    ctx.beginPath(); ctx.moveTo(cxr, cyr - 64); ctx.lineTo(cxr + 12, cyr); ctx.lineTo(cxr - 12, cyr); ctx.closePath(); ctx.fill();
    ctx.font = `600 34px ${LATIN}`; ctx.fillStyle = C.ink; ctx.textAlign = "center"; ctx.textBaseline = "middle"; ctx.fillText("N", cxr, cyr - 88); ctx.restore(); }
  // 当前时期：左下角的标签牌
  if (cur && cur.era !== "none" && el.eras[cur.era]) {
    const e = el.eras[cur.era], ka = clamp((t - cur.t) / .6) * a, sz = m.labelSize || 70;
    const w = Math.max(textW(e.label, fT(sz)), e.sub ? textW(e.sub, fL(sz * .5)) : 0) + 60, h = sz * (e.sub ? 2.05 : 1.45);
    const lx = x0 + 30, ly = y1 - 30 - h;
    const shp = cardShape(lx, ly, w, h);
    fillPoly(shp, C.panel, ka * .94);
    ctx.globalAlpha = ka; ctx.strokeStyle = C.red; ctx.lineWidth = 3; ctx.beginPath(); shp.forEach((p, i) => i ? ctx.lineTo(p[0], p[1]) : ctx.moveTo(p[0], p[1])); ctx.closePath(); ctx.stroke(); ctx.globalAlpha = 1;
    revealText(e.label, lx + 30, ly + sz * .78, fT(sz), C.red, (t - cur.t) / .7, "left", a);
    if (e.sub) revealText(e.sub, lx + 30, ly + sz * 1.62, fL(sz * .5), C.soft, (t - cur.t - .3) / .7, "left", a);
  }
  ctx.restore();
}
// 行军 / 迁徙路线：ROUTES.id = {pts: [[x,y]…], color, w, dash, dur, token: 涂鸦名, tokenS, tokenDy, keepToken, label, labelAt: 0~1, labelDx, labelDy, top}
function catmull(pts, n = 10) {
  if (pts.length < 3) return pts;
  const o = [];
  for (let i = 0; i < pts.length - 1; i++) {
    const p0 = pts[Math.max(0, i - 1)], p1 = pts[i], p2 = pts[i + 1], p3 = pts[Math.min(pts.length - 1, i + 2)];
    for (let k = 0; k < n; k++) { const t = k / n, t2 = t * t, t3 = t2 * t;
      o.push([0, 1].map(j => .5 * (2 * p1[j] + (-p0[j] + p2[j]) * t + (2 * p0[j] - 5 * p1[j] + 4 * p2[j] - p3[j]) * t2 + (-p0[j] + 3 * p1[j] - 3 * p2[j] + p3[j]) * t3))); }
  }
  o.push(pts[pts.length - 1]);
  return o;
}
function buildRoutes() {
  for (const id in ROUTES) {
    const r = ROUTES[id], pts = catmull(r.pts, 12);
    const s = withLen(sketch(pts, hashStr(id), r.amp ?? 1.4));
    let x0 = 1e9, y0 = 1e9, x1 = -1e9, y1 = -1e9;
    for (const [x, y] of pts) { x0 = Math.min(x0, x); y0 = Math.min(y0, y); x1 = Math.max(x1, x); y1 = Math.max(y1, y); }
    EL[id] = {kind: "route", r, s: Object.assign(s, {c: r.color || C.red, w: r.w || 6, dash: r.dash === false ? null : (r.dash || [22, 12])}),
              bbox: {x0: x0 - 40, y0: y0 - 40, x1: x1 + 40, y1: y1 + 40}, cx: (x0 + x1) / 2, cy: (y0 + y1) / 2};
  }
}
function pointAt(s, p) {
  const target = s.len * clamp(p); let i = s.cum.findIndex(c => c >= target); if (i <= 0) i = 1;
  const f = (target - s.cum[i - 1]) / (s.cum[i] - s.cum[i - 1] || 1);
  const [ax, ay] = s.pts[i - 1], [bx, by] = s.pts[i];
  return {x: lerp(ax, bx, f), y: lerp(ay, by, f), ang: Math.atan2(by - ay, bx - ax)};
}
function drawRoute(id, t) {
  const el = EL[id], t0 = S.show[id]; if (t0 == null || t < t0) return;
  const r = el.r, p = (t - t0) / (r.dur || 3);
  withXform(id, t, el.cx, el.cy, a => {
    drawStroke(el.s, p, a);
    const h = pointAt(el.s, Math.min(p, 1)), L = 26 + el.s.w * 2;
    if (p > .02 && !r.noArrow) {   // 箭头
      ctx.globalAlpha = a; ctx.fillStyle = el.s.c; ctx.beginPath();
      ctx.moveTo(h.x + Math.cos(h.ang) * L * .6, h.y + Math.sin(h.ang) * L * .6);
      ctx.lineTo(h.x + Math.cos(h.ang + 2.5) * L * .7, h.y + Math.sin(h.ang + 2.5) * L * .7);
      ctx.lineTo(h.x + Math.cos(h.ang - 2.5) * L * .7, h.y + Math.sin(h.ang - 2.5) * L * .7); ctx.closePath(); ctx.fill(); ctx.globalAlpha = 1;
    }
    if (r.token && ICONS[r.token] && (p < 1 || r.keepToken)) {   // 路线头上跟着走的小涂鸦（大象、船…）
      const bob = Math.sin(t * 9) * 4, flip = Math.cos(h.ang) < 0 ? -1 : 1;
      ctx.save(); ctx.globalAlpha = a; ctx.translate(h.x, h.y - (r.tokenDy ?? 40) + bob); ctx.scale(flip, 1); drawIconAt(r.token, 0, 0, r.tokenS || .8, 0); ctx.restore();
    }
    if (r.label) { const q = pointAt(el.s, r.labelAt ?? .5);
      revealText(r.label, q.x + (r.labelDx ?? 0), q.y + (r.labelDy ?? -40), fT(r.labelSize || 40), el.s.c, (p - (r.labelAt ?? .5)) / .3, "center", a); }
  });
}

// ───────────── 时间轴 → 状态 ─────────────
const S = {show: {}, dead: {}, hl: [], fade: {}, fadeUp: {}, moves: {}, swap: {}, cams: [], huds: [], rain: [], wind: null, restore: null, fxAxis: null, eras: {}, grades: []};
function prepTimeline() {
  for (const ev of TLN.events) {
    const [verb, ...args] = ev.act.split(/\s+/), t = ev.t;
    const show = id => { if (S.show[id] == null) S.show[id] = t; };
    if (verb === "card" || verb === "icon" || verb === "note" || verb === "group" || verb === "map" || verb === "route") show(args[0]);
    else if (verb === "era") (S.eras[args[0]] ??= []).push({t, era: args[1] || "none"});
    else if (verb === "grade") S.grades.push({t, name: args[0] || "none"});
    else if (verb === "edge") { show(args[0]); const w = EDGES[args[0]]?.with; if (w) show(w); }
    else if (verb === "ev") { show(args[0]); if (S.fxAxis == null) S.fxAxis = t - .2; }
    else if (verb === "dead") S.dead[args[0]] ??= t;
    else if (verb === "hl") S.hl.push({id: args[0], t});
    else if (verb === "fade") { S.fade[args[0]] ??= t; if (args[1] === "up") S.fadeUp[args[0]] = true; }
    else if (verb === "swap") args.forEach(a => S.swap[a] = t);
    else if (verb === "cam" || verb === "camz") S.cams.push({t, zoom: verb === "camz" ? +args[0] : null, ids: verb === "camz" ? args.slice(1) : args});
    else if (verb === "hud") S.huds.push({t, text: args.join(" ")});
    else if (verb === "rain") S.rain.push({t, on: args[0] === "on"});
    else if (verb === "wind") S.wind = t;
    else if (verb === "restore") S.restore = t;
    else if (["move", "walk", "run", "jump"].includes(verb) && ICON_POS[args[0]]) {
      const p = ICON_POS[args[0]], L = (S.moves[args[0]] ??= []);
      const prev = L.length ? L[L.length - 1] : {dx: 0, dy: 0};
      const dx = +args[1] - p.x, dy = args[2] != null ? +args[2] - p.y : prev.dy;
      const dist = Math.hypot(dx - prev.dx, dy - prev.dy);
      L.push({t, dx, dy, dur: Math.max(.35, dist / (verb === "run" ? 900 : verb === "walk" ? 300 : 600)), jump: verb === "jump" ? 90 : 0});
    }
  }
  S.firstCard = Math.min(...Object.keys(NODES).map(k => S.show[k] ?? 1e9));
  // 事件卡出现时，时间轴本身先画出来
  if (S.fxAxis != null) S.show.__axis = S.fxAxis;
  // 预先算好每个镜头的目标
  if (HD.Actors) HD.Actors.prep(TLN.events, WD);
  if (!AREAS.all) {   // 没写 all 就用全部元素的外框
    let x0 = 1e9, y0 = 1e9, x1 = -1e9, y1 = -1e9;
    const boxes = Object.entries(EL).filter(([id]) => id !== "__axis" || S.fxAxis != null).map(([, e]) => e.bbox);
    if (HD.Actors) for (const id of HD.Actors.ids()) boxes.push(HD.Actors.bbox(id, null));
    for (const b of boxes) { if (!b) continue; x0 = Math.min(x0, b.x0); y0 = Math.min(y0, b.y0); x1 = Math.max(x1, b.x1); y1 = Math.max(y1, b.y1); }
    AREAS.all = {x0, y0, x1, y1};
  }
  S.cams.forEach(c => { c.target = fitIds(c.ids, c.zoom, c.t); });
}
function moveOffset(id, t) {   // 道具（涂鸦）的移动
  const L = S.moves[id]; if (!L) return [0, 0];
  let dx = 0, dy = 0, px = 0, py = 0;
  for (const m of L) {
    if (t < m.t) break;
    const k = ease((t - m.t) / m.dur);
    dx = lerp(px, m.dx, k); dy = lerp(py, m.dy, k) - (m.jump ? Math.sin(Math.PI * clamp((t - m.t) / m.dur)) * m.jump : 0);
    px = m.dx; py = m.dy;
  }
  return [dx, dy];
}
function bboxOf(id, t) {
  if (AREAS[id]) return AREAS[id];
  if (HD.Actors && HD.Actors.has(id)) return HD.Actors.bbox(id, t);
  const e = EL[id]; if (!e) { console.warn("no element", id); return null; }
  return e.bbox;
}
function fitIds(ids, zoom, t) {
  let x0 = 1e9, y0 = 1e9, x1 = -1e9, y1 = -1e9;
  for (const id of ids) { const b = bboxOf(id, t != null ? t + 1.2 : null); if (!b) continue; x0 = Math.min(x0, b.x0); y0 = Math.min(y0, b.y0); x1 = Math.max(x1, b.x1); y1 = Math.max(y1, b.y1); }
  const pad = 70; x0 -= pad; x1 += pad; y0 -= pad; y1 += pad;
  let z = zoom ?? Math.min(W * .88 / (x1 - x0), H * .76 / (y1 - y0));
  z = clamp(z, .26, 1.25);
  return {x: (x0 + x1) / 2, y: (y0 + y1) / 2 + H * .015 / z, z};
}
// 注意：camAt 依次累积，每个镜头从“上一个镜头在它开始时的状态”出发
function camAtExact(t) {
  let state = S.cams.length ? {...S.cams[0].target} : {x: 0, y: -800, z: .8};
  let from = state, start = -1e9, target = state;
  for (const c of S.cams) {
    if (c.t > t) break;
    // 计算上一段在 c.t 时的位置
    const k = ease((c.t - start) / 1.7);
    const cur = {x: lerp(from.x, target.x, k), y: lerp(from.y, target.y, k), z: Math.exp(lerp(Math.log(from.z), Math.log(target.z), k))};
    from = cur; target = c.target; start = c.t;
  }
  const k = ease((t - start) / 1.7);
  return {x: lerp(from.x, target.x, k), y: lerp(from.y, target.y, k), z: Math.exp(lerp(Math.log(from.z), Math.log(target.z), k))};
}

// 飓风：每个元素被吹走的位移
function windOf(id, t) {
  if (S.wind == null || t < S.wind || WIND_KEEP.has(id)) return null;
  const r = rng(hashStr(id) + 99), delay = r() * 1.6, ang = -.5 + r() * 1.4, dist = 1600 + r() * 1400, spin = (r() - .5) * 2.4;
  let f = ease((t - S.wind - delay) / 3.2);
  if (S.restore != null && t >= S.restore) {
    const f0 = ease((S.restore - S.wind - delay) / 3.2);
    f = f0 * (1 - ease((t - S.restore - delay * .4) / 1.8));
  }
  if (f <= 0) return null;
  return {dx: Math.cos(ang) * dist * f, dy: Math.sin(ang) * dist * f - 200 * f, rot: spin * f, alpha: 1 - clamp(f * 1.3), f};
}
function elemAlpha(id, t) {
  let a = 1;
  if (S.fade[id] != null) a *= 1 - clamp((t - S.fade[id]) / (S.fadeUp[id] ? 2.2 : 1.2));
  return a;
}

// ───────────── 各类元素的绘制 ─────────────
function withXform(id, t, cx, cy, fn) {
  const w = windOf(id, t);
  let a = elemAlpha(id, t);
  let dy = 0, dx0 = 0;
  if (S.fadeUp[id] && S.fade[id] != null) dy = -380 * ease((t - S.fade[id]) / 2.4);
  const mv = moveOffset(id, t); dx0 = mv[0]; dy += mv[1];
  if (w) a *= w.alpha;
  if (a <= .003) return;
  ctx.save();
  if (w || dy || dx0) { ctx.translate(cx + (w?.dx || 0) + dx0, cy + (w?.dy || 0) + dy); if (w) ctx.rotate(w.rot); ctx.translate(-cx, -cy); }
  fn(a);
  ctx.restore();
}
function drawCard(id, t) {
  const el = EL[id], t0 = S.show[id]; if (t0 == null || t < t0) return;
  const ta = t - t0, {g, n} = el, col = kindColor(n.kind);
  withXform(id, t, el.cx, el.cy, a => {
    const dead = S.dead[id] != null && t >= S.dead[id] ? clamp((t - S.dead[id]) / .8) : 0;
    const pop = 1 + .05 * Math.sin(Math.PI * clamp(ta / .45));
    ctx.save(); ctx.translate(el.cx, el.cy); ctx.scale(pop, pop); ctx.translate(-el.cx, -el.cy);
    // 阴影 + 底色
    const index = TH.card.style === "index";
    if (index) {   // 索引卡：微微歪一点，像随手放在桌上
      const rot = ((hashStr(id) % 100) / 100 - .5) * .045; ctx.translate(el.cx, el.cy); ctx.rotate(rot); ctx.translate(-el.cx, -el.cy);
    }
    fillPoly(el.fill.map(([x, y]) => [x + 5, y + 6]), "#000", (index ? .16 : .06) * a * clamp(ta / .6));
    fillPoly(el.fill, index ? C.panel : C.paper, a * clamp((ta - .1) / .4));
    if (!index) fillPoly(el.fill, col, a * .13 * clamp((ta - .1) / .4) * (1 - .6 * dead));
    if (index) {   // 顶部色条 + 红线 + 蓝色横格
      const k = clamp((ta - .15) / .5) * a;
      fillPoly([[g.x0, g.y0], [g.x0 + g.w, g.y0], [g.x0 + g.w, g.y0 + 9], [g.x0, g.y0 + 9]], col, k * .9 * (1 - .6 * dead));
      ctx.save(); ctx.globalAlpha = k; ctx.lineWidth = 2; ctx.strokeStyle = "#d9534f";
      const ry = g.y0 + g.h * .58; ctx.beginPath(); ctx.moveTo(g.x0 + 6, ry); ctx.lineTo(g.x0 + g.w - 6, ry); ctx.stroke();
      ctx.strokeStyle = "rgba(90,140,200,.45)"; ctx.lineWidth = 1.3;
      for (const f of [.88]) { const yy = g.y0 + g.h * f; ctx.beginPath(); ctx.moveTo(g.x0 + 6, yy); ctx.lineTo(g.x0 + g.w - 6, yy); ctx.stroke(); }
      ctx.restore();
    }
    let bc = index ? mix(C.ink, col, .25) : col;
    const tablet = TH.card.style === "tablet";
    drawStroke(Object.assign(el.s1, {c: dead ? mix(col, "#9a948b", dead * .7) : bc, w: index ? 2.2 : 3.4}), ta / .75, a);
    if (!index) drawStroke(Object.assign(el.s2, {c: bc, w: tablet ? 1.6 : 1.4, alpha: tablet ? .8 : .45}), (ta - .2) / .8, a);
    if (tablet && ta > .5) {   // 石碑四角的小圆钉
      ctx.globalAlpha = a * clamp((ta - .5) / .3); ctx.fillStyle = bc;
      for (const [x, y] of [[g.x0 + 14, g.y0 + 14], [g.x0 + g.w - 14, g.y0 + 14], [g.x0 + 14, g.y0 + g.h - 14], [g.x0 + g.w - 14, g.y0 + g.h - 14]]) { ctx.beginPath(); ctx.arc(x, y, 3.2, 0, 7); ctx.fill(); }
      ctx.globalAlpha = 1;
    }
    if (TH.card.style === "seal" && ta > .8) {   // 宣纸卡：右上角盖一枚朱印
      const k = easeOut((ta - .8) / .25), sz = 34 * (n.s || 1), sx = g.x0 + g.w - sz * .6, sy = g.y0 - sz * .35;
      ctx.save(); ctx.translate(sx, sy); ctx.rotate(.06); ctx.scale(1.4 - .4 * k, 1.4 - .4 * k);
      ctx.globalAlpha = a * k * .92; ctx.fillStyle = C.red; ctx.fillRect(-sz / 2, -sz / 2, sz, sz);
      ctx.fillStyle = C.paper; ctx.font = `${sz * .62}px ${TITLE}`; ctx.textAlign = "center"; ctx.textBaseline = "middle";
      ctx.fillText(n.seal || n.name[0], 0, 1); ctx.restore(); ctx.globalAlpha = 1;
    }
    if (S.swap[id] != null && t >= S.swap[id]) {   // 双胞胎：名字和性子对调 → 内框换成对方的颜色
      const other = kindColor(n.swapTo || (n.kind === "ja" ? "au" : "ja"));
      drawStroke(Object.assign(el.inner, {c: other, w: 3.2, dash: [10, 6]}), (t - S.swap[id]) / 1, a);
    }
    const nameCol = dead ? mix(C.ink, "#9a948b", dead * .8) : C.ink;
    revealText(n.name, el.cx, g.y0 + g.h * .38, NAMEF(g.fs), nameCol, (ta - .3) / .7, "center", a);
    revealText(n.tag, el.cx, g.y0 + g.h * .76, fL(g.tf), dead ? "#a39c92" : C.soft, (ta - .7) / .6, "center", a);
    if (n.num) revealText(n.num, g.x0 + 12, g.y0 + 24, `600 ${Math.round(20 * (n.s || 1))}px Consolas, monospace`, "#c0392b", (ta - .5) / .3, "left", a);
    if (n.gen) revealText(`G${n.gen}`, g.x0 + 12, g.y0 + 16, fL(18), hexA(col, .9), (ta - .6) / .3, "left", a);
    if (dead) {
      fillPoly(el.fill, C.paper, a * .35 * dead);
      el.cross.forEach((s, i) => drawStroke(Object.assign(s, {c: "#4a4440", w: 3.2}), (t - S.dead[id] - i * .18) / .3, a));
    }
    ctx.restore();
  });
}
function drawEdge(id, t) {
  const el = EL[id], t0 = S.show[id]; if (t0 == null || t < t0) return;
  const p = (t - t0) / el.st.dur;
  // 边的透明度跟随两端（例如加斯通离开、俏姑娘升天时线也淡去）
  let a = 1; for (const e of el.ends) if (e && S.fade[e] != null) a = Math.min(a, elemAlpha(e, t));
  const [mx, my] = el.mid;
  withXform(id, t, mx, my, a2 => {
    const A = a * a2;
    drawStroke(el.main, p, A);
    el.extra.forEach(s => drawStroke(s, p, A));
    if (el.arrow) drawStroke(el.arrow, (p - .95) / .2, A);
    if (el.e.type === "blood") {   // 血流一路“流”过去，画一个流动的头
      if (p > 0 && p < 1.05) { const {pts, cum, len} = el.main; const target = len * clamp(p); let i = cum.findIndex(c => c >= target); if (i < 0) i = pts.length - 1;
        ctx.globalAlpha = A; ctx.fillStyle = C.red; ctx.beginPath(); ctx.arc(pts[i][0], pts[i][1], 9, 0, 7); ctx.fill(); ctx.globalAlpha = 1; }
    }
    if (el.e.caption) revealText(el.e.caption, mx + (el.e.capDx ?? -60), my + (el.e.capDy ?? 48), fH(30), el.st.c, (p - .5) / .6, "center", A);
    const lab = el.e.label;
    const lp = clamp((p - .6) / .5);
    if (lab && lp > 0) {
      ctx.font = fL(22); const w = ctx.measureText(lab).width + 22;
      fillPoly(roundRectPts(mx - w / 2, my - 17, w, 34, 10), C.paper, A * lp);
      ctx.globalAlpha = A * lp * .8; ctx.strokeStyle = el.st.c; ctx.lineWidth = 1.5; ctx.beginPath();
      roundRectPts(mx - w / 2, my - 17, w, 34, 10).forEach((q, i) => i ? ctx.lineTo(q[0], q[1]) : ctx.moveTo(q[0], q[1])); ctx.closePath(); ctx.stroke(); ctx.globalAlpha = 1;
      revealText(lab, mx, my + 1, fL(22), el.st.c === C.ink ? C.soft : el.st.c, lp, "center", A);
    }
    if (el.st.heart && p > .5) { ctx.globalAlpha = A * clamp((p - .5) * 3); ctx.fillStyle = C.love; ctx.font = `34px serif`; ctx.textAlign = "center"; ctx.fillText("♥", mx, my - 34); ctx.globalAlpha = 1; }
    if (el.st.x && p > .5) revealText("✕", mx, my - 32, fL(30), C.red, (p - .5) * 3, "center", A);
  });
}
function drawIcon(id, t) {
  const el = EL[id], t0 = S.show[id]; if (t0 == null || t < t0) return;
  const dur = ICON_POS[id].dur ?? 1.1;
  withXform(id, t, el.cx, el.cy, a => {
    let bob = 0;
    const ip = ICON_POS[id];
    if (ip.bob) bob = -ip.bob * Math.sin(clamp((t - t0) / 3) * Math.PI / 2);
    if (ip.hover) bob += Math.sin(t * (ip.hoverSpeed || 2.2) + (hashStr(id) % 7)) * ip.hover;
    const wob = ip.wobble ? Math.sin(t * (ip.wobbleSpeed || 3) + (hashStr(id) % 5)) * ip.wobble : 0;
    const sq = ip.squash ? 1 + Math.sin(t * (ip.squashSpeed || 7)) * ip.squash : 1;
    const xf = bob || wob || sq !== 1;
    if (xf) { ctx.save(); ctx.translate(el.cx, el.cy + bob); ctx.rotate(wob); ctx.scale(1, sq); ctx.translate(-el.cx, -el.cy); }
    el.P.draw((t - t0) / dur, a);
    if (xf) ctx.restore();
  });
}
function drawEv(id, t) {
  const el = EL[id], t0 = S.show[id]; if (t0 == null || t < t0) return;
  const ta = t - t0;
  withXform(id, t, el.cx, el.cy, a => {
    drawStroke(Object.assign(el.dot, {c: C.ink, w: 3}), ta / .3, a);
    if (ta > .2) { ctx.globalAlpha = a * clamp((ta - .2) / .3); ctx.fillStyle = C.red; ctx.beginPath(); ctx.arc(TL_X, el.e.y, 7, 0, 7); ctx.fill(); ctx.globalAlpha = 1; }
    drawStroke(Object.assign(el.tick, {c: C.ink, w: 2}), (ta - .2) / .3, a);
    fillPoly(el.rr, C.panel, a * clamp((ta - .3) / .4));
    drawStroke(Object.assign(el.s1, {c: C.ink, w: 2.8}), (ta - .3) / .7, a);
    el.P.draw((ta - .5) / .9, a);
    revealText(el.e.title, el.x0 + 100, el.e.y - 17, fH(36), C.ink, (ta - .6) / .6, "left", a);
    revealText(el.e.sub, el.x0 + 100, el.e.y + 23, fL(24), C.soft, (ta - .9) / .6, "left", a);
  });
}
function drawAxis(t) {
  const t0 = S.show.__axis; if (t0 == null || t < t0) return;
  const el = EL.__axis, ta = t - t0;
  withXform("__axis", t, TL_X, (TLM.y0 + TLM.y1) / 2, a => {
    drawStroke(el.axis, ta / 1.8, a);
    revealText(TLM.title, TL_X, TLM.y0 - 70, fH(54), C.ink, (ta - .2) / .8, "center", a);
    TLM.ticks.forEach((tk, g) => revealText(tk.label, TL_X - 30, tk.y, fL(24), C.out, (ta - .3 - g * .15) / .4, "right", a));
  });
}
function drawGroup(id, t) {
  const el = EL[id], t0 = S.show[id]; if (t0 == null || t < t0) return;
  const ta = t - t0;
  withXform(id, t, (el.g.x0 + el.g.x1) / 2, (el.g.y0 + el.g.y1) / 2, a => {
    fillPoly(el.rr, mix(C.paper, C.ink, .05), a * .55 * clamp(ta / .8));
    drawStroke(el.s, ta / 1.2, a);
    revealText(el.g.label, el.g.x0 + 20, el.g.y0 - 30, fH(44), C.out, (ta - .3) / .6, "left", a);
  });
}
function drawNote(id, t) {
  const el = EL[id], t0 = S.show[id]; if (t0 == null || t < t0) return;
  const {n} = el, ta = t - t0;
  withXform(id, t, el.cx, el.cy, a => {
    if (n.style === "sticky") {
      ctx.save(); ctx.translate(el.cx, el.cy); ctx.rotate(el.rot); ctx.translate(-el.cx, -el.cy);
      const b = el.bbox, rr = [[b.x0, b.y0], [b.x1, b.y0], [b.x1, b.y1], [b.x0, b.y1]];
      const k = easeOut(ta / .35);
      fillPoly(rr.map(([x, y]) => [x + 6, y + 8]), "#000", .08 * a * k);
      fillPoly(rr, (TH.sticky.bg || "#fff0a6"), a * k);
      fillPoly([[el.cx - 40, b.y0 - 12], [el.cx + 40, b.y0 - 8], [el.cx + 38, b.y0 + 14], [el.cx - 42, b.y0 + 10]], (TH.sticky.tape || "#d9d2c0"), a * .75 * k);
      el.lines.forEach((l, i) => revealText(l, el.cx, el.cy - el.h / 2 + n.size * .58 + i * n.size * 1.15, el.font, n.color, (ta - .25 - i * .4) / .6, "center", a));
      ctx.restore();
    } else if (n.style === "bubble") {
      const b = el.bbox, k = easeOut(ta / .3), [tx, ty] = n.tail || [n.x - 60, b.y1 + 60];
      ctx.save(); ctx.translate(el.cx, el.cy); ctx.scale(.6 + .4 * k, .6 + .4 * k); ctx.translate(-el.cx, -el.cy);
      if (!el.bub) {
        const pts = roundRectPts(b.x0, b.y0, b.x1 - b.x0, b.y1 - b.y0, 22);
        const bx = clamp(tx, b.x0 + 40, b.x1 - 40), below = ty > b.y1, edgeY = below ? b.y1 : b.y0;
        const i0 = pts.findIndex(p => Math.abs(p[1] - edgeY) < .5 && (below ? p[0] <= bx : p[0] >= bx));
        const tail = below ? [[bx + 16, edgeY], [tx, ty], [bx - 12, edgeY]] : [[bx - 16, edgeY], [tx, ty], [bx + 12, edgeY]];
        const full = i0 > 0 ? pts.slice(0, i0).concat(tail, pts.slice(i0)) : pts.concat(tail);
        el.bubFill = full; el.bub = Object.assign(withLen(sketch(full.concat([full[0]]), hashStr(id), 1.6)), {c: C.ink, w: 3.2});
      }
      fillPoly(el.bubFill, (TH.bubble.fill || C.panel), a * k);
      drawStroke(el.bub, ta / .35, a);
      el.lines.forEach((l, i) => revealText(l, el.cx, el.cy - el.h / 2 + n.size * .58 + i * n.size * 1.15, el.font, n.color, (ta - .15 - i * .25) / .4, "center", a));
      ctx.restore();
    } else if (n.style === "legend") {
      drawStroke(el.sw, ta / .5, a);
      fillPoly(roundRectPts(n.x - 36, n.y - 16, 32, 32, 7), n.color, a * .85 * clamp((ta - .3) / .3));
      revealText(n.text, n.x + 12, n.y, el.font, n.color, (ta - .2) / .8, "left", a);
    } else {
      el.lines.forEach((l, i) => revealText(l, n.x, n.y - el.h / 2 + n.size * .58 + i * n.size * 1.15, el.font, n.color, (ta - i * .4) / (n.style === "title" ? 1.3 : .9), "center", a));
      if (el.under) drawStroke(el.under, (ta - 1.1) / .6, a);
      if (el.loop) drawStroke(el.loop, (ta - .4) / 1.2, a);
    }
  });
}
function drawHl(h, t) {
  if (t < h.t || t > h.t + 4.2) return;
  const b = bboxOf(h.id, h.t); if (!b) return;
  const w = windOf(h.id, t); if (w && w.alpha < .5) return;
  const cx = (b.x0 + b.x1) / 2, cy = (b.y0 + b.y1) / 2, rx = (b.x1 - b.x0) / 2 + 30, ry = (b.y1 - b.y0) / 2 + 26;
  if (!h.s) h.s = Object.assign(withLen(sketch(ellipsePts(cx, cy, rx, ry, -2.2, -2.2 + Math.PI * 2.15, 64), hashStr(h.id) + ((h.t * 10) | 0), 5)), {c: C.red, w: 4.5});
  const a = 1 - clamp((t - h.t - 3.6) / .6);
  drawStroke(h.s, (t - h.t) / .6, a * .9);
}

// ───────────── 背景、天气、HUD、字幕 ─────────────
// 盖在最上层的纸纹（屏幕坐标，按画风生成；grain 比画面大一圈，每帧错位成“胶片颗粒”）
const TEXK = TH.tex.kind || "paper";
const paperTex = (() => {
  const pad = TEXK === "grain" ? 64 : 0, c = document.createElement("canvas"); c.width = W + pad; c.height = H + pad; const g = c.getContext("2d");
  const r = rng(1234), img = g.createImageData(c.width, c.height);
  const nAmp = {paper: 26, parchment: 30, rice: 18, chalk: 34, grain: 70}[TEXK] ?? 26, nA = {paper: 18, parchment: 24, rice: 14, chalk: 16, grain: 34}[TEXK] ?? 18;
  for (let i = 0; i < img.data.length; i += 4) {
    const v = (TEXK === "chalk" ? 190 : 128) + (r() - .5) * nAmp; img.data[i] = img.data[i + 1] = img.data[i + 2] = v; img.data[i + 3] = nA;
  }
  g.putImageData(img, 0, 0);
  const fib = (n, col, a, l0, l1, curvy) => {
    g.strokeStyle = col;
    for (let i = 0; i < n; i++) {
      const x = r() * c.width, y = r() * c.height, an = r() * 6.28, l = l0 + r() * (l1 - l0);
      g.globalAlpha = a * (.4 + r() * .6); g.lineWidth = .5 + r() * .5; g.beginPath(); g.moveTo(x, y);
      if (curvy) g.quadraticCurveTo(x + Math.cos(an + 1) * l * .6, y + Math.sin(an + 1) * l * .6, x + Math.cos(an) * l, y + Math.sin(an) * l);
      else g.lineTo(x + Math.cos(an) * l, y + Math.sin(an) * l);
      g.stroke();
    }
  };
  if (TEXK === "paper") fib(400, "#8a7a5a", .05, 10, 50, false);
  if (TEXK === "rice") fib(900, "#7d705c", .09, 20, 120, true);
  if (TEXK === "parchment") {
    fib(500, "#6b4a28", .06, 8, 46, false);
    for (let i = 0; i < 9; i++) {       // 几块淡淡的水渍
      const x = r() * W, y = r() * H, rad = 30 + r() * 120, gr = g.createRadialGradient(x, y, rad * .3, x, y, rad);
      gr.addColorStop(0, "rgba(120,72,30,0)"); gr.addColorStop(.85, `rgba(120,72,30,${.012 + r() * .02})`); gr.addColorStop(1, "rgba(120,72,30,0)");
      g.globalAlpha = 1; g.fillStyle = gr; g.fillRect(x - rad, y - rad, rad * 2, rad * 2);
    }
  }
  if (TEXK === "chalk") {
    g.fillStyle = "#ffffff";
    for (let i = 0; i < 3000; i++) { g.globalAlpha = .05 + r() * .12; g.fillRect(r() * W, r() * H, 1 + r() * 2, 1 + r() * 1.5); }
  }
  if (TEXK === "grain") {       // 胶片划痕
    g.strokeStyle = "#000"; for (let i = 0; i < 14; i++) { const x = r() * c.width; g.globalAlpha = .05 + r() * .08; g.lineWidth = .8; g.beginPath(); g.moveTo(x, 0); g.lineTo(x + (r() - .5) * 30, c.height); g.stroke(); }
  }
  const vc = TH.tex.vignette || "rgba(60,40,20,0.20)";
  const v = g.createRadialGradient(W / 2, H / 2, H * (TEXK === "parchment" || TEXK === "grain" ? .3 : .35), W / 2, H / 2, H * 1.05);
  v.addColorStop(0, "rgba(0,0,0,0)"); v.addColorStop(1, vc);
  g.globalAlpha = 1; g.fillStyle = v; g.fillRect(0, 0, c.width, c.height);
  if (TEXK === "parchment") {    // 边缘烧焦
    const e = g.createLinearGradient(0, 0, 0, H); e.addColorStop(0, "rgba(90,45,12,.16)"); e.addColorStop(.08, "rgba(90,45,12,0)"); e.addColorStop(.92, "rgba(90,45,12,0)"); e.addColorStop(1, "rgba(90,45,12,.18)");
    g.fillStyle = e; g.fillRect(0, 0, W, H);
  }
  return c;
})();
// 跟着镜头走的大块底纹（灰泥斑驳 / 宣纸纤维 / 粉笔灰 / 水彩晕染），无缝平铺
const BG_TILE = (() => {
  const kind = TH.bg.tile; if (!kind) return null;
  const S = 1024, c = document.createElement("canvas"); c.width = c.height = S; const g = c.getContext("2d"), r = rng(4321);
  const wrap = (x, y, rad, fn) => { for (const dx of [-S, 0, S]) for (const dy of [-S, 0, S]) if (x + dx > -rad && x + dx < S + rad && y + dy > -rad && y + dy < S + rad) fn(x + dx, y + dy); };
  const blob = (x, y, rad, col, a, rim) => wrap(x, y, rad, (X, Y) => {
    const gr = g.createRadialGradient(X, Y, 0, X, Y, rad);
    if (rim) { gr.addColorStop(0, hexA(col, a * .5)); gr.addColorStop(.85, hexA(col, a)); gr.addColorStop(1, hexA(col, 0)); }
    else { gr.addColorStop(0, hexA(col, a)); gr.addColorStop(1, hexA(col, 0)); }
    g.fillStyle = gr; g.fillRect(X - rad, Y - rad, rad * 2, rad * 2);
  });
  if (kind === "mottle") {
    for (let i = 0; i < 14; i++) {   // 一团一团的斑驳：每团由很多小斑点组成，不会出现规则的圆
      const cx = r() * S, cy = r() * S, R = 120 + r() * 200, col = r() < .6 ? C.ink : "#ffffff";
      for (let k = 0; k < 40; k++) { const a = r() * 6.28, d = Math.sqrt(r()) * R; blob(cx + Math.cos(a) * d, cy + Math.sin(a) * d * .7, 18 + r() * 60, col, .008 + r() * .016); }
    }
    g.strokeStyle = C.ink;            // 灰泥上的细裂纹
    for (let i = 0; i < 16; i++) { let x = r() * S, y = r() * S; g.globalAlpha = .05 + r() * .05; g.lineWidth = .8; g.beginPath(); g.moveTo(x, y);
      for (let k = 0; k < 9; k++) { x += (r() - .5) * 60; y += (r() - .3) * 40; g.lineTo(x, y); } g.stroke(); }
  } else if (kind === "rice") {
    for (let i = 0; i < 40; i++) blob(r() * S, r() * S, 80 + r() * 200, C.ink, .012 + r() * .015);
    g.strokeStyle = C.ink;
    for (let i = 0; i < 260; i++) { const x = r() * S, y = r() * S, a = r() * 6.28, l = 30 + r() * 140; g.globalAlpha = .035 + r() * .05; g.lineWidth = .6;
      g.beginPath(); g.moveTo(x, y); g.quadraticCurveTo(x + Math.cos(a + .8) * l * .5, y + Math.sin(a + .8) * l * .5, x + Math.cos(a) * l, y + Math.sin(a) * l); g.stroke(); }
  } else if (kind === "chalk") {
    for (let i = 0; i < 26; i++) { const x = r() * S, y = r() * S; wrap(x, y, 260, (X, Y) => { g.save(); g.translate(X, Y); g.scale(2.6, .7); g.rotate((r() - .5) * .3);
      const gr = g.createRadialGradient(0, 0, 0, 0, 0, 100); gr.addColorStop(0, "rgba(255,255,255,.05)"); gr.addColorStop(1, "rgba(255,255,255,0)"); g.fillStyle = gr; g.fillRect(-100, -100, 200, 200); g.restore(); }); }
  } else if (kind === "wash") {
    const cols = [C.sky, C.gold, C.love, C.green, C.blue];
    for (let i = 0; i < 12; i++) blob(r() * S, r() * S, 160 + r() * 260, cols[i % cols.length], .035 + r() * .035, true);
  }
  g.globalAlpha = 1;
  return c;
})();
let bgPattern = null;
function drawBackground(cam) {
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.fillStyle = C.paper; ctx.fillRect(0, 0, W, H);
  const kind = TH.bg.kind || "dots";
  const sx0 = cam.x - W / 2 / cam.z, sy0 = cam.y - H / 2 / cam.z;
  if (BG_TILE) {   // 底纹：比镜头慢一点（0.6 倍视差），缩放跟着走
    bgPattern = bgPattern || ctx.createPattern(BG_TILE, "repeat");
    const k = cam.z * .9 + .25;
    bgPattern.setTransform(new DOMMatrix([k, 0, 0, k, (-cam.x * .6 * k) % 1024, (-cam.y * .6 * k) % 1024]));
    ctx.globalAlpha = TH.bg.tileAlpha ?? 1; ctx.fillStyle = bgPattern; ctx.fillRect(0, 0, W, H); ctx.globalAlpha = 1;
  }
  if (kind === "dots") {   // 画布点阵（跟随镜头）
    let step = 50; while (step * cam.z < 26) step *= 2;
    const gx0 = Math.floor(sx0 / step) * step, gy0 = Math.floor(sy0 / step) * step;
    ctx.fillStyle = C.dot;
    for (let gx = gx0; gx < sx0 + W / cam.z + step; gx += step)
      for (let gy = gy0; gy < sy0 + H / cam.z + step; gy += step) {
        const X = (gx - cam.x) * cam.z + W / 2, Y = (gy - cam.y) * cam.z + H / 2;
        ctx.fillRect(X - 1.6, Y - 1.6, 3.2, 3.2);
      }
  } else if (kind === "grid" || kind === "lines") {   // 网格 / 横线本
    let step = 50; while (step * cam.z < 22) step *= 2;
    ctx.strokeStyle = C.dot; ctx.beginPath();
    const gy0 = Math.floor(sy0 / step) * step, gx0 = Math.floor(sx0 / step) * step;
    for (let gy = gy0; gy < sy0 + H / cam.z + step; gy += step) {
      const Y = (gy - cam.y) * cam.z + H / 2; ctx.lineWidth = Math.round(gy / step) % 5 === 0 ? 1.8 : .9; ctx.beginPath(); ctx.moveTo(0, Y); ctx.lineTo(W, Y); ctx.stroke();
    }
    if (kind === "grid") for (let gx = gx0; gx < sx0 + W / cam.z + step; gx += step) {
      const X = (gx - cam.x) * cam.z + W / 2; ctx.lineWidth = Math.round(gx / step) % 5 === 0 ? 1.8 : .9; ctx.beginPath(); ctx.moveTo(X, 0); ctx.lineTo(X, H); ctx.stroke();
    }
  }
}
// 画面边框（屏幕坐标，预先画好）
const FRAME = (() => {
  const kind = TH.frame; if (!kind) return null;
  const c = document.createElement("canvas"); c.width = W; c.height = H; const g = c.getContext("2d"), r = rng(99);
  if (kind === "meander") {      // 古希腊回纹：上下两条细带 + 细框
    g.strokeStyle = C.red; g.lineCap = "square";
    const band = (y0, h) => {
      g.globalAlpha = .55; g.lineWidth = 1.6; g.beginPath(); g.moveTo(14, y0); g.lineTo(W - 14, y0); g.moveTo(14, y0 + h); g.lineTo(W - 14, y0 + h); g.stroke();
      const u = h / 4; g.lineWidth = 1.4; g.globalAlpha = .5;
      for (let x = 20; x < W - 20 - u * 4; x += u * 4) {
        const yb = y0 + h - u * .6, yt = y0 + u * .6;
        g.beginPath(); g.moveTo(x, yb); g.lineTo(x, yt); g.lineTo(x + u * 3, yt); g.lineTo(x + u * 3, yb - u * 1.6); g.lineTo(x + u * 1.2, yb - u * 1.6); g.lineTo(x + u * 1.2, yt + u * 1.2); g.stroke();
        g.beginPath(); g.moveTo(x, yb); g.lineTo(x + u * 4, yb); g.stroke();
      }
    };
    band(8, 20); band(H - 28, 20);
    g.globalAlpha = .35; g.lineWidth = 1.2; g.strokeRect(10, 6, W - 20, H - 12);
  } else if (kind === "film") {
    g.fillStyle = "#0b0b0b"; g.fillRect(0, 0, W, 40); g.fillRect(0, H - 40, W, 40);
  } else if (kind === "brush") {   // 墨笔框：四边几道干擦的墨痕
    g.strokeStyle = C.ink; g.lineCap = "round";
    const side = (x0, y0, x1, y1) => { for (let k = 0; k < 3; k++) { g.globalAlpha = .1 + r() * .14; g.lineWidth = 3 + r() * 9; g.beginPath();
      g.moveTo(x0 + (r() - .5) * 20, y0 + (r() - .5) * 8); g.lineTo(x1 + (r() - .5) * 20, y1 + (r() - .5) * 8); g.stroke(); } };
    side(30, 14, W - 60, 12); side(60, H - 12, W - 30, H - 16); side(12, 40, 16, H - 70); side(W - 14, 60, W - 12, H - 40);
  }
  g.globalAlpha = 1;
  return c;
})();
// 调色（`grade 名字`）：每层 [合成方式, 颜色, 浓度]
const GRADES = Object.assign({
  none: [],
  warm: [["soft-light", "#ffb060", .35]],
  gold: [["soft-light", "#ffc04a", .5], ["multiply", "#fff0c8", .25]],
  dusk: [["multiply", "#e0a080", .35], ["soft-light", "#6a3a6a", .3]],
  cold: [["soft-light", "#4a7aa8", .45], ["saturation", "#808080", .25]],
  ash: [["saturation", "#808080", .7], ["multiply", "#d8d2c8", .35]],
  sepia: [["saturation", "#808080", .8], ["multiply", "#e6c89a", .55]],
  night: [["multiply", "#5a6a8a", .55], ["saturation", "#808080", .35]],
  blood: [["multiply", "#d07060", .35], ["soft-light", "#a01010", .3]],
}, TH.grades || {});
function drawGrade(t) {
  let cur = TH.grade || "none", prev = cur, t0 = -99;
  for (const gEv of S.grades) { if (gEv.t > t) break; prev = cur; cur = gEv.name; t0 = gEv.t; }
  const k = ease((t - t0) / 2.4);
  const layer = (name, a) => { for (const [op, col, al] of GRADES[name] || []) { ctx.globalCompositeOperation = op; ctx.globalAlpha = al * a; ctx.fillStyle = col; ctx.fillRect(0, 0, W, H); } };
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  if (k < 1 && prev !== cur) layer(prev, 1 - k);
  layer(cur, k < 1 && prev !== cur ? k : 1);
  ctx.globalCompositeOperation = "source-over"; ctx.globalAlpha = 1;
}
function rainLevel(t) {
  let lvl = 0;
  for (let i = 0; i < S.rain.length; i++) {
    const r = S.rain[i]; if (t < r.t) break;
    lvl = r.on ? clamp((t - r.t) / 1.2) : 1 - clamp((t - r.t) / 1.5);
  }
  return lvl;
}
function drawRain(t) {
  const lvl = rainLevel(t); if (lvl <= 0) return;
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.fillStyle = `rgba(70,90,110,${.16 * lvl})`; ctx.fillRect(0, 0, W, H);
  const r = rng(42); ctx.strokeStyle = `rgba(80,110,140,${.55 * lvl})`; ctx.lineWidth = 2; ctx.lineCap = "round";
  ctx.beginPath();
  for (let i = 0; i < 260; i++) {
    const x0 = r() * (W + 300), sp = 900 + r() * 500, len = 26 + r() * 30, ph = r();
    const y = ((t * sp + ph * (H + 200)) % (H + 200)) - 100, x = x0 - (y + 100) * .25;
    ctx.moveTo(x, y); ctx.lineTo(x - len * .25, y + len);
  }
  ctx.stroke();
}
function drawWindFx(t) {
  if (S.wind == null || t < S.wind) return;
  let k = clamp((t - S.wind) / 1.5) * (1 - clamp((t - S.wind - 6) / 2));
  if (S.restore != null && t > S.restore) k = 0;
  if (k <= 0) return;
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  const r = rng(7); ctx.strokeStyle = `rgba(110,120,130,${.4 * k})`; ctx.lineWidth = 2.2; ctx.lineCap = "round";
  for (let i = 0; i < 70; i++) {
    const y = r() * H, sp = 1400 + r() * 1200, len = 160 + r() * 260, ph = r();
    const x = ((t * sp + ph * (W + 600)) % (W + 600)) - 300, curl = (r() - .5) * 60;
    ctx.beginPath(); ctx.moveTo(x, y); ctx.bezierCurveTo(x + len * .3, y + curl, x + len * .7, y - curl, x + len, y + curl * .3); ctx.stroke();
  }
}
function hudAt(t) {
  let cur = null, prev = null;
  for (const h of S.huds) { if (h.t > t) break; prev = cur; cur = h; }
  return {cur, prev};
}
function drawHud(t) {
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  if (window.COVER_MODE) return;                           // 截封面时不画章节标签和图例
  const {cur, prev} = hudAt(t);
  const st = TH.hud.style || "tab";
  const box = (h, a, dy) => {
    if (!h || a <= 0) return;
    ctx.save(); ctx.translate(0, dy + TOPY);
    const p = (t - h.t) / .7;
    if (st === "banner") {           // 绶带：赭红底、金边、燕尾
      const font = fT(38), w = textW(h.text, font) + 80, x0 = 30, y0 = 34, hh = 64;
      const pts = [[x0, y0], [x0 + w, y0], [x0 + w - 18, y0 + hh / 2], [x0 + w, y0 + hh], [x0, y0 + hh], [x0 + 14, y0 + hh / 2]];
      fillPoly(pts.map(([x, y]) => [x + 4, y + 5]), "#000", a * .18);
      fillPoly(pts, C.red, a * .95);
      ctx.globalAlpha = a; ctx.strokeStyle = C.gold; ctx.lineWidth = 2;
      ctx.beginPath(); ctx.moveTo(x0 + 18, y0 + 6); ctx.lineTo(x0 + w - 12, y0 + 6); ctx.moveTo(x0 + 18, y0 + hh - 6); ctx.lineTo(x0 + w - 12, y0 + hh - 6); ctx.stroke();
      ctx.globalAlpha = 1;
      revealText(h.text, x0 + 34, y0 + hh / 2 + 2, font, C.panel, p, "left", a);
    } else if (st === "seal") {     // 竖排朱印
      const chars = [...h.text.replace(/[、，·\s]/g, "")], fs = 42, w = 66, hh = chars.length * (fs + 6) + 26, x0 = 34, y0 = 30;
      ctx.save(); ctx.translate(x0 + w / 2, y0 + hh / 2); ctx.rotate(-.02); ctx.translate(-(x0 + w / 2), -(y0 + hh / 2));
      fillPoly([[x0, y0], [x0 + w, y0 + 2], [x0 + w - 2, y0 + hh], [x0 + 2, y0 + hh - 2]], C.red, a * .9 * clamp(p * 2));
      chars.forEach((ch, i) => revealText(ch, x0 + w / 2, y0 + 20 + fs / 2 + i * (fs + 6), fT(fs), C.panel, (p - i * .12) * 2, "center", a));
      ctx.restore();
    } else if (st === "chalk") {
      const font = fH(46), w = textW(h.text, font);
      revealText(h.text, 44, 62, font, C.ink, p, "left", a);
      drawStroke(Object.assign(withLen(sketch([[40, 96], [44 + w * .5, 100], [48 + w, 94]], 3, 2)), {c: C.gold, w: 4}), p - .2, a);
    } else {
      const font = fH(40), w = textW(h.text, font) + 60;
      fillPoly([[34, 30], [34 + w, 26], [38 + w, 88], [30, 92]], C.panel, a * .92);
      ctx.globalAlpha = a; ctx.fillStyle = C.red; ctx.fillRect(34, 30, 8, 62); ctx.globalAlpha = 1;
      revealText(h.text, 62, 60, font, C.ink, p, "left", a);
    }
    ctx.restore();
  };
  if (prev && t - cur.t < .4) box(prev, 1 - (t - cur.t) / .4, -(t - cur.t) * 60);
  if (cur) box(cur, clamp((t - cur.t) / .3), 0);
  // 右上角的小图例（进入家谱以后常驻）
  const legend = Object.values(KINDS).filter(k => k.label);
  const la = !legend.length ? 0 : clamp((t - S.firstCard - .5) / .8) * (S.wind != null && t > S.wind && (S.restore == null || t < S.restore) ? 1 - clamp((t - S.wind) / 1) : 1);
  if (la > 0) {
    const items = legend.map(k => [k.label, k.color]);
    const x = W - 290, y = 44 + TOPY;
    const hh = items.length * 36 + 14;
    fillPoly([[x - 16, y - 8], [W - 30, y - 10], [W - 28, y + hh], [x - 18, y + hh + 2]], C.panel, la * .85);
    items.forEach(([s, c], i) => { ctx.globalAlpha = la; ctx.fillStyle = c; ctx.fillRect(x, y + 12 + i * 36, 20, 20); ctx.globalAlpha = 1;
      revealText(s, x + 32, y + 23 + i * 36, fH(27), c, 1, "left", la); });
  }
}
function drawSubs(t) {
  if (window.COVER_MODE) return;                          // 截封面时不画字幕
  const s = TLN.subs.find(s => t >= s.t0 && t < s.t1); if (!s) return;
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  const a = Math.min(clamp((t - s.t0) / .12), clamp((s.t1 - t) / .12));
  const sb = TH.sub, st = sb.style || "bar";
  ctx.font = `${FT.subW || 600} 46px ${SUBFONT}`; ctx.textAlign = "center"; ctx.textBaseline = "middle";
  const w = ctx.measureText(s.text).width + 56, y = VERT ? Math.round(H * .79) : H - 86;
  if (st === "outline") {
    ctx.globalAlpha = a; ctx.lineJoin = "round"; ctx.lineWidth = 9; ctx.strokeStyle = sb.bg || C.paper; ctx.strokeText(s.text, W / 2, y + 2);
    ctx.fillStyle = sb.fg || C.ink; ctx.fillText(s.text, W / 2, y + 2); ctx.globalAlpha = 1; return;
  }
  ctx.globalAlpha = a * (sb.alpha ?? .78); ctx.fillStyle = sb.bg || "#2b2521";
  if (st === "banner") {
    const x0 = W / 2 - w / 2 - 10, x1 = W / 2 + w / 2 + 10, n = 22;
    ctx.beginPath(); ctx.moveTo(x0 - n, y - 36); ctx.lineTo(x1 + n, y - 36); ctx.lineTo(x1, y); ctx.lineTo(x1 + n, y + 36); ctx.lineTo(x0 - n, y + 36); ctx.lineTo(x0, y); ctx.closePath(); ctx.fill();
    ctx.globalAlpha = a * .55; ctx.strokeStyle = C.gold; ctx.lineWidth = 1.5;
    ctx.beginPath(); ctx.moveTo(x0 + 4, y - 29); ctx.lineTo(x1 - 4, y - 29); ctx.moveTo(x0 + 4, y + 29); ctx.lineTo(x1 - 4, y + 29); ctx.stroke();
  } else { ctx.beginPath(); ctx.roundRect(W / 2 - w / 2, y - 36, w, 72, 16); ctx.fill(); }
  ctx.globalAlpha = a; ctx.fillStyle = sb.fg || "#fffaf0"; ctx.fillText(s.text, W / 2, y + 2); ctx.globalAlpha = 1;
}
function drawOutro(t) {
  const d = TLN.duration, k = clamp((t - (d - 1.6)) / 1.4);
  if (k <= 0) return;
  ctx.setTransform(1, 0, 0, 1, 0, 0); ctx.globalAlpha = k; ctx.fillStyle = TH.outro || C.paper; ctx.fillRect(0, 0, W, H); ctx.globalAlpha = 1;
}

// ───────────── 主渲染 ─────────────
function render(t) {
  const cam = camAtExact(t);
  cam.z *= 1 + .012 * Math.sin(t * .35);   // 轻微“呼吸”
  Z = cam.z;
  drawBackground(cam);
  if (window.COVER_BLANK) { ctx.setTransform(1, 0, 0, 1, 0, 0); drawGrade(t); return; }   // 封面用：只要这一画风的空白底子
  ctx.setTransform(cam.z, 0, 0, cam.z, W / 2 - cam.x * cam.z, H / 2 - cam.y * cam.z);
  for (const id in MAPS) drawMap(id, t);
  for (const id in GROUPS) drawGroup(id, t);
  drawAxis(t);
  const TOP_EDGE = e => e.type === "special" || e.type === "blood" || e.top;
  for (const id in EDGES) if (!TOP_EDGE(EDGES[id])) drawEdge(id, t);
  for (const id in ROUTES) if (!ROUTES[id].top) drawRoute(id, t);
  for (const id in EVENTS) drawEv(id, t);
  for (const id in NODES) drawCard(id, t);
  for (const id in ICON_POS) if (!ICON_POS[id].top) drawIcon(id, t);
  if (HD.Actors) for (const id of HD.Actors.ids()) HD.Actors.draw(id, t);
  for (const id in ICON_POS) if (ICON_POS[id].top) drawIcon(id, t);
  for (const id in ROUTES) if (ROUTES[id].top) drawRoute(id, t);
  for (const id in NOTES) drawNote(id, t);
  for (const id in EDGES) if (TOP_EDGE(EDGES[id])) drawEdge(id, t);
  for (const h of S.hl) drawHl(h, t);
  if (HD.Actors) for (const id of HD.Actors.ids()) HD.Actors.overlays(id, t);
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  drawGrade(t);
  if (window.COVER_MODE) {}   // 封面不要暗角和纸纹：画面要挪位置，暗角会露出接缝
  else if (TEXK === "grain") { const r = rng(Math.floor(t * 12)); ctx.drawImage(paperTex, -r() * 64, -r() * 64); }
  else ctx.drawImage(paperTex, 0, 0);
  if (FRAME && !window.COVER_MODE) ctx.drawImage(FRAME, 0, 0);
  drawRain(t);
  drawWindFx(t);
  drawHud(t);
  drawSubs(t);
  drawOutro(t);
}

// ───────────── 给 characters.js 用的工具 ─────────────
function shade(hex, k) {
  const n = parseInt(hex.slice(1), 16); const f = v => Math.round(clamp(k < 0 ? v * (1 + k) : v + (255 - v) * k, 0, 255));
  return `rgb(${f(n >> 16 & 255)},${f(n >> 8 & 255)},${f(n & 255)})`;
}
const PROP_CACHE = {};
function drawIconAt(name, x, y, s, rotDeg = 0) {   // 人物手里拿的东西（用涂鸦库里的画法）
  if (!ICONS[name]) return;
  if (!PROP_CACHE[name]) { const P = new Pen(hashStr("prop" + name)); ICONS[name](P, {}); PROP_CACHE[name] = P; }
  ctx.save(); ctx.translate(x, y); ctx.rotate(rotDeg * Math.PI / 180); ctx.scale(s, s); PROP_CACHE[name].draw(1.2, ctx.globalAlpha || 1); ctx.restore();
}
function envAt(t) { const e = TLN.env; if (!e) return 0; const i = Math.floor(t * (TLN.envFps || 30)); return i >= 0 && i < e.length ? (e.charCodeAt(i) - 48) / 9 : 0; }
const HD = window.HD = {ctx, W, H, C, clamp, ease, easeOut, lerp, rng, hashStr, sketch, withLen, drawStroke, fillPoly, revealText, textW,
  roundRectPts, HAND, KAI, TITLE, LATIN, fH, fL, fT, TH, hatchPoly, shade, drawIconAt, envAt, Pen, ICONS, windOf};

window.fontsReady = (async () => {
  await document.fonts.load(`40px LongCang`, "百年孤独");
  await document.fonts.load(`20px KaiTi`, "楷");
  for (const f of [fH(40), fL(20), fT(40), `600 40px ${LATIN}`, `${FT.subW || 600} 40px ${SUBFONT}`])
    try { await document.fonts.load(f, "罗马帝国永恒之城ROMA"); } catch (e) {}
  await document.fonts.ready;
  buildMaps(); buildCards(); buildIcons(); buildEvents(); buildGroups(); buildNotes(); buildEdges(); buildRoutes();
  prepTimeline();
  window.render = render;
  window.DURATION = TLN.duration;
  render(0);
  return true;
})();
})();
