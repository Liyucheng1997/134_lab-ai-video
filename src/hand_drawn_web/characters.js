// 手绘动画人物引擎：Q 版“橡皮管”人物，姿势 / 表情 / 眨眼 / 呼吸 / 走跑跳 / 口型 / 气泡 / 情绪符号 / 手持道具。
// 线条每秒重抖 8 次（line boil），看起来像逐帧手绘。依赖 board.js 暴露的 window.HD。
(() => {
const HD = window.HD;
const {ctx, C, clamp, ease, easeOut, lerp, rng, hashStr, sketch, withLen, drawStroke, fillPoly, HAND, TH, fH} = HD;
const INK = (TH && TH.actor.ink) || "#2d2824", OUT = (TH && TH.actor.out) || 3.6, BOIL = (TH && TH.actor.boil) || 8;
const BUB = (TH && TH.bubble.fill) || "#fffdf6", BUBTXT = (TH && TH.bubble.text) || INK;
const easeBack = x => { x = clamp(x); const c1 = 1.7, c3 = c1 + 1; return 1 + c3 * Math.pow(x - 1, 3) + c1 * Math.pow(x - 1, 2); };
const D2R = Math.PI / 180;

// ───────────── 姿势库（角度，0 = 自然下垂，正 = 朝面向的方向抬） ─────────────
// b* = 后侧手/腿，f* = 前侧手/腿；ua 上臂、fa 前臂(相对上臂)；th 大腿、sh 小腿(相对大腿，负=膝盖向后弯)
const BASE = {lean: 0, tilt: 0, crouch: 0, rot: 0, bua: -10, bfa: 14, fua: 10, ffa: 16, bth: -4, bsh: 0, fth: 4, fsh: 0};
const POSES = {
  stand: {},
  wave: {fua: 128, ffa: 40, tilt: -5},
  point: {fua: 95, ffa: 0, lean: 4},
  present: {fua: 65, ffa: 35, bua: -18, bfa: 22},          // 讲解手势：摊手指向一边
  think: {fua: 35, ffa: 145, bua: 10, bfa: 95, tilt: -10},
  shrug: {fua: 28, ffa: 80, bua: -28, bfa: -80, tilt: 10},
  cheer: {fua: 138, ffa: 18, bua: -138, bfa: -18, lean: -4},
  hug: {fua: 80, ffa: 45, bua: 75, bfa: 50, lean: 6},
  cry: {fua: 38, ffa: 142, bua: 30, bfa: 145, tilt: 16, lean: 10},
  angry: {fua: 55, ffa: 105, bua: -25, bfa: 40, lean: 10},
  scared: {fua: 120, ffa: 60, bua: 105, bfa: 60, lean: -12, crouch: 10},
  sit: {crouch: 44, fth: 85, fsh: -85, bth: 80, bsh: -80, fua: 30, ffa: 40, bua: 20, bfa: 40},
  kneel: {crouch: 44, fth: 80, fsh: -82, bth: 8, bsh: -98, fua: 40, ffa: 110, bua: 35, bfa: 115},
  pray: {fua: 40, ffa: 112, bua: 35, bfa: 118, tilt: -6},
  bow: {lean: 42, tilt: 10, fua: -38, ffa: 6, bua: -44, bfa: 6},
  lie: {rot: -90, fua: 10, ffa: 10, bua: -5, bfa: 5, tilt: 0},
  dead: {rot: -90, fua: 40, ffa: 20, bua: -30, bfa: 10, tilt: 8},
  hands_up: {fua: 132, ffa: 28, bua: -132, bfa: -28},
  aim: {fua: 88, ffa: -4, bua: 72, bfa: 34, lean: 6, fth: 14, bth: -14},
  hold: {fua: 55, ffa: 42},
  read: {fua: 45, ffa: 72, bua: 40, bfa: 78, tilt: 14},
  tied: {fua: -38, ffa: -30, bua: -44, bfa: -30, tilt: 12},
  float: {fua: 100, ffa: 12, bua: -100, bfa: -12, fth: 12, fsh: -20, bth: -6, bsh: -10},
  facepalm: {fua: 40, ffa: 152, tilt: 20, lean: 8},
  dance: {fua: 125, ffa: 40, bua: -70, bfa: -50, fth: 30, fsh: -40},
  punch: {fua: 90, ffa: 0, bua: 30, bfa: 120, lean: 10, fth: 20, bth: -20},
  sleep: {rot: -90, fua: 20, ffa: 60, bua: 10, bfa: 50, tilt: 6},
  work: {fua: 70, ffa: 50, bua: 60, bfa: 55, lean: 18},      // 干活：握锄头 / 扫地
  write: {fua: 45, ffa: 80, lean: 14, tilt: 18},
  stab: {fua: 86, ffa: -6, bua: -24, bfa: 30, lean: 14, fth: 22, bth: -18},     // 往前刺（配 hold X dagger / sword）
  raise: {fua: 138, ffa: -6, bua: -15, bfa: 20, tilt: -6},                        // 把手里的东西高高举起（皇冠、鹰旗）
  salute: {fua: 112, ffa: 4, tilt: -4},                                           // 举手致敬
  fight: {fua: 136, ffa: 46, bua: 78, bfa: 30, lean: 6, fth: 18, bth: -16},       // 举剑 + 前伸盾牌
  defend: {fua: 30, ffa: 62, bua: 88, bfa: 12, lean: -8, crouch: 10},             // 举盾防守
  play: {fua: 48, ffa: 74, bua: 42, bfa: 84, tilt: 10},                           // 弹琴（配 hold X lyre）
};
const EXPRS = ["neutral", "happy", "laugh", "sad", "cry", "angry", "surprised", "scared", "love", "smug", "sleepy", "dead", "determined", "thinking", "shy"];

// ───────────── 时间轴 → 每个人物的事件 ─────────────
const A = {};   // id -> {def, ev: {...}}
function prep(events, WORLD, lineEnds) {
  for (const id in (WORLD.ACTORS || {})) A[id] = {id, def: WORLD.ACTORS[id], shows: [], leaves: [], poses: [], exprs: [], faces: [], moves: [],
    says: [], emotes: [], talks: [], holds: [], shakes: [], dead: null, seed: hashStr(id)};
  for (const e of events) {
    const parts = e.act.split(/\s+/), verb = parts[0], id = parts[1], a = A[id];
    if (!a) continue;
    const t = e.t, rest = parts.slice(2);
    if (verb === "actor") { const walkin = rest[0] === "walkin"; a.shows.push({t, walkin}); if (walkin) a.moves.push({t, mode: "walkin"}); }
    else if (verb === "pose") a.poses.push({t, name: rest[0] || "stand"});
    else if (verb === "expr") a.exprs.push({t, name: rest[0] || "neutral"});
    else if (verb === "face") a.faces.push({t, dir: rest[0] === "left" ? -1 : 1});
    else if (["walk", "run", "jump", "move", "float", "place"].includes(verb)) a.moves.push({t, mode: verb, x: +rest[0], y: rest[1] != null ? +rest[1] : null});
    else if (verb === "say" || verb === "think") a.says.push({t, kind: verb, text: rest.join(" ").replace(/\\n/g, "\n")});
    else if (verb === "emote") a.emotes.push({t, name: rest[0]});
    else if (verb === "talk") a.talks.push({t, end: rest[0] === "off" ? t : +(rest[0] || t + 4)});
    else if (verb === "hold") a.holds.push({t, icon: rest[0] === "none" ? null : rest[0]});
    else if (verb === "shake") a.shakes.push({t});
    else if (verb === "leave") { const mode = rest[0] || "walk"; a.leaves.push({t, mode}); if (mode === "walk") a.moves.push({t, mode: "leavewalk"}); }
    else if (verb === "dead") { a.dead = t; a.poses.push({t, name: "dead"}); a.exprs.push({t, name: "dead"}); }
    else if (verb === "fade") a.leaves.push({t, mode: rest[0] === "up" ? "up" : "fade"});
  }
  // 自动加上走进来的移动
  for (const id in A) {
    const a = A[id], d = a.def;
    a.moves.sort((p, q) => p.t - q.t); a.shows.sort((p, q) => p.t - q.t); a.leaves.sort((p, q) => p.t - q.t);
    a.faces.sort((p, q) => p.t - q.t);
    // 预先算好每段移动的起点、时长（可以多次登场 / 离场：walkin 从背后走回当前位置，leave 朝当时的面向走出去）
    let x = d.x, y = d.y, lastDir = d.face ?? 1, lastDirT = -1;
    for (const m of a.moves) {
      if (m.mode === "walkin") { const dir = (d.face === -1 ? 1 : -1); m.mode = "walk"; m.x0 = x + dir * 520; m.y0 = y; m.x = x; m.y = y; m.dur = 520 / 230; continue; }
      if (m.mode === "leavewalk") {
        const fe = lastBefore(a.faces, m.t), dir = fe && fe.t > lastDirT ? fe.dir : lastDir;
        m.mode = "walk"; m.x0 = x; m.y0 = y; m.x = x + dir * 700; m.y = y; m.dur = 3; x = m.x; continue;
      }
      m.x0 = x; m.y0 = y; if (m.y == null || isNaN(m.y)) m.y = y;
      if (m.mode !== "place" && Math.abs(m.x - x) > 1) { lastDir = m.x > x ? 1 : -1; lastDirT = m.t; }
      const dist = Math.hypot(m.x - x, m.y - y);
      m.dur = m.mode === "place" ? 1e-3 : m.mode === "move" ? Math.max(.5, dist / 700) : m.mode === "run" ? Math.max(.4, dist / 520) : m.mode === "jump" ? Math.max(.6, dist / 500) : m.mode === "float" ? Math.max(1.5, dist / 160) : Math.max(.5, dist / 230);
      x = m.x; y = m.y;
    }
  }
}

// ───────────── 某一时刻的人物状态 ─────────────
function lastBefore(list, t) { let r = null; for (const e of list) { if (e.t > t) break; r = e; } return r; }
function state(id, t) {
  const a = A[id]; if (!a) return null;
  const show = lastBefore(a.shows, t); if (!show) return null;
  const leave = lastBefore(a.leaves, t), lv = leave && leave.t >= show.t ? leave : null;
  const d = a.def;
  let x = d.x, y = d.y, moving = null, face = d.face ?? 1;
  for (const m of a.moves) {
    if (t < m.t) break;
    const k = clamp((t - m.t) / m.dur);
    const kk = m.mode === "walk" || m.mode === "run" ? k : ease(k);
    x = lerp(m.x0, m.x, kk); y = lerp(m.y0, m.y, kk);
    if (m.mode === "jump") y -= Math.sin(Math.PI * k) * 140;
    if (k < 1) moving = {m, k, dist: Math.hypot(m.x - m.x0, m.y - m.y0) * kk};
    if (m.mode !== "place" && Math.abs(m.x - m.x0) > 1) face = m.x > m.x0 ? 1 : -1;
  }
  const fe = lastBefore(a.faces, t); if (fe && (!moving || fe.t > moving.m.t)) face = fe.dir;
  let alpha = 1, rise = 0;
  if (lv) {
    const k = (t - lv.t);
    if (lv.mode === "fade") alpha = 1 - clamp(k / 1);
    else if (lv.mode === "up") { rise = 520 * ease(k / 3.2); alpha = 1 - clamp((k - 2.2) / 1); }
    else alpha = 1 - clamp((k - 2) / 1);
    if (alpha <= 0) return null;
  }
  return {a, d, x, y: y - rise, face, moving, alpha, s: d.s ?? 1, floating: rise > 0, show};
}
function poseAt(a, t, st) {
  const P = {...BASE};
  const i = a.poses.findIndex(p => p.t > t);
  const idx = (i < 0 ? a.poses.length : i) - 1;
  const cur = idx >= 0 ? POSES[a.poses[idx].name] || {} : {};
  const prev = idx >= 1 ? POSES[a.poses[idx - 1].name] || {} : {};
  const k = idx >= 0 ? easeBack((t - a.poses[idx].t) / .5) : 1;
  for (const key in BASE) P[key] = lerp(prev[key] ?? BASE[key], cur[key] ?? BASE[key], k);
  const name = idx >= 0 ? a.poses[idx].name : "stand";
  // 程序化的附加动作
  const ph = a.seed % 100 / 16;
  if (name === "wave") P.ffa += Math.sin(t * 9) * 22;
  if (name === "cheer" || name === "dance") P.crouch -= Math.abs(Math.sin(t * 6)) * 14;
  if (name === "dance") { P.fua += Math.sin(t * 6) * 25; P.bua -= Math.sin(t * 6) * 25; P.lean += Math.sin(t * 3) * 6; }
  if (name === "work") { P.fua += Math.sin(t * 5) * 22; P.bua += Math.sin(t * 5) * 22; P.lean += Math.sin(t * 5) * 4; }
  if (name === "write") P.ffa += Math.sin(t * 14) * 6;
  if (name === "play") { P.bfa += Math.sin(t * 11) * 9; P.tilt += Math.sin(t * 2.4) * 5; }
  if (name === "fight") { P.fua += Math.sin(t * 5) * 8; }
  if (name === "cry") P.tilt += Math.sin(t * 7) * 3;
  if (name === "laugh") P.lean -= 4;
  // 待机：呼吸、微微摆手
  if (!["lie", "dead", "sleep"].includes(name)) {
    P.crouch += Math.sin(t * 2.1 + ph) * 1.6;
    P.fua += Math.sin(t * 1.6 + ph) * 2.5; P.bua -= Math.sin(t * 1.4 + ph) * 2.5; P.tilt += Math.sin(t * 1.1 + ph) * 1.5;
  }
  // 走 / 跑
  if (st.moving && (st.moving.m.mode === "walk" || st.moving.m.mode === "run")) {
    const run = st.moving.m.mode === "run", stride = run ? 95 : 62;
    const p = st.moving.dist / stride * Math.PI;
    const A1 = run ? 42 : 28;
    P.fth = Math.sin(p) * A1; P.bth = -Math.sin(p) * A1;
    P.fsh = -Math.max(0, Math.cos(p)) * (run ? 70 : 38); P.bsh = -Math.max(0, -Math.cos(p)) * (run ? 70 : 38);
    P.fua = -Math.sin(p) * (run ? 45 : 24) + (run ? 10 : 6); P.bua = Math.sin(p) * (run ? 45 : 24) - 6;
    P.ffa = run ? 80 : 18; P.bfa = run ? 80 : 18;
    P.crouch = -Math.abs(Math.sin(p)) * (run ? 10 : 5) + (run ? 6 : 0); P.lean = run ? 14 : 3; P.rot = 0;
  }
  if (st.moving && st.moving.m.mode === "jump") { P.fth = 40; P.fsh = -60; P.bth = 20; P.bsh = -50; P.fua = 150; P.bua = -150; }
  if (st.floating) Object.assign(P, {fua: 110, ffa: 12, bua: -110, bfa: -12, fth: 12, fsh: -20, bth: -6, bsh: -10, rot: 0});
  const sh = a.shakes.find(s => t >= s.t && t < s.t + 1.6);
  if (sh) P.shake = Math.sin(t * 70) * 3 * (1 - (t - sh.t) / 1.6);
  if (name === "scared") P.shake = (P.shake || 0) + Math.sin(t * 60) * 1.6;
  P.name = name;
  return P;
}
function exprAt(a, t) { const e = lastBefore(a.exprs, t); return {name: e ? e.name : "neutral", t0: e ? e.t : -9}; }

// ───────────── 画法小工具（每帧重新抖动 = 手绘动画的“呼吸”） ─────────────
let SEED = 1, AMP = 1.1;
function ink(pts, fill, closed = true, lw = OUT, amp = AMP, color = INK) {
  const sp = sketch(pts, SEED++ * 7919 + 13, amp, closed);
  if (fill) fillPoly(closed ? pts : sp, fill, ctx.globalAlpha || 1);
  const s = withLen(sp); s.c = color; s.w = lw;
  drawStroke(s, 1, ctx.globalAlpha || 1);
}
function circ(cx, cy, rx, ry = rx, a0 = 0, a1 = Math.PI * 2, n = 36) {
  const o = []; for (let i = 0; i <= n; i++) { const a = lerp(a0, a1, i / n); o.push([cx + rx * Math.cos(a), cy + ry * Math.sin(a)]); } return o;
}
function limb(pts, color, w) {     // 橡皮管：先描黑边，再填颜色
  const sp = sketch(pts, SEED++ * 7919 + 5, .7, false);
  ctx.lineCap = "round"; ctx.lineJoin = "round";
  ctx.strokeStyle = INK; ctx.lineWidth = w + OUT * 2; ctx.beginPath(); sp.forEach((p, i) => i ? ctx.lineTo(p[0], p[1]) : ctx.moveTo(p[0], p[1])); ctx.stroke();
  ctx.strokeStyle = color; ctx.lineWidth = w; ctx.beginPath(); sp.forEach((p, i) => i ? ctx.lineTo(p[0], p[1]) : ctx.moveTo(p[0], p[1])); ctx.stroke();
}
function rot2(x, y, deg) { const r = deg * D2R; return [x * Math.cos(r) - y * Math.sin(r), x * Math.sin(r) + y * Math.cos(r)]; }
function dirv(deg, len) { return [Math.sin(deg * D2R) * len, Math.cos(deg * D2R) * len]; }   // 0° 向下，正角朝前

// ───────────── 画一个人 ─────────────
function drawActor(id, t, blinkOK = true) {
  const st = state(id, t); if (!st) return;
  const {a, d} = st, L = d.look || {};
  const P = poseAt(a, t, st), E = exprAt(a, t);
  const age = L.age || "adult", kid = age === "child", old = age === "old";
  const bodyS = kid ? .72 : 1, headR = kid ? 44 : 46;
  const skin = L.skin || "#f4d6bc", top = L.top || "#6f9bd1", bottom = L.bottom || "#5a5048", shoe = L.shoes || "#4a3b32";
  const deadK = a.dead != null && t >= a.dead ? clamp((t - a.dead) / .8) : 0;
  const showK = clamp((t - st.show.t) / .45);
  SEED = a.seed + Math.floor(t * BOIL) * 131;          // 每 1/BOIL 秒换一次抖动（手绘动画的“呼吸”）
  const wd = HD.windOf ? HD.windOf(id, t) : null;          // 飓风把人也吹走
  if (wd && wd.alpha <= .01) return;
  ctx.save();
  ctx.globalAlpha = st.alpha * (wd ? wd.alpha : 1);
  if (wd) { ctx.translate(st.x + wd.dx, st.y - 150 + wd.dy); ctx.rotate(wd.rot); ctx.translate(-st.x, -st.y + 150); }
  ctx.translate(st.x + (P.shake || 0), st.y);
  const pop = st.show.walkin ? 1 : easeBack(showK);
  ctx.scale(st.s * pop, st.s * (st.show.walkin ? 1 : lerp(.6, 1, easeBack(showK))));
  // 影子
  if (!st.floating) { ctx.globalAlpha = st.alpha * .16; ctx.fillStyle = "#000"; ctx.beginPath(); ctx.ellipse(P.rot ? -60 : 0, 4, P.rot ? 150 : 62, 11, 0, 0, 7); ctx.fill(); ctx.globalAlpha = st.alpha; }
  ctx.scale(st.face, 1);
  if (P.rot) { ctx.translate(0, -26 * Math.abs(Math.sin(P.rot * D2R))); ctx.rotate(P.rot * D2R); }
  if (old) P.lean += 9;

  // 骨架
  const hip = [0, -100 * bodyS + P.crouch];
  const neck = [hip[0] + dirv(180 + P.lean, 78 * bodyS)[0] * -1, hip[1] - Math.cos(P.lean * D2R) * 78 * bodyS];
  neck[0] = hip[0] + Math.sin(P.lean * D2R) * 78 * bodyS;
  const head = [neck[0] + Math.sin((P.lean + P.tilt) * D2R) * (headR + 6), neck[1] - Math.cos((P.lean + P.tilt) * D2R) * (headR + 6)];
  const sh = [lerp(hip[0], neck[0], .9), lerp(hip[1], neck[1], .9)];
  const arm = (ua, fa, off) => {
    const s0 = [sh[0] + off, sh[1]]; const a1 = P.lean + ua; const e = [s0[0] + dirv(a1, 40 * bodyS)[0], s0[1] + dirv(a1, 40 * bodyS)[1]];
    const a2 = a1 + fa; const h = [e[0] + dirv(a2, 38 * bodyS)[0], e[1] + dirv(a2, 38 * bodyS)[1]]; return [s0, e, h];
  };
  const leg = (th, shn, off) => {
    const h0 = [hip[0] + off, hip[1]]; const k = [h0[0] + dirv(th, 46 * bodyS)[0], h0[1] + dirv(th, 46 * bodyS)[1]];
    const f = [k[0] + dirv(th + shn, 46 * bodyS)[0], k[1] + dirv(th + shn, 46 * bodyS)[1]]; return [h0, k, f];
  };
  const bArm = arm(P.bua, P.bfa, -5), fArm = arm(P.fua, P.ffa, 5);
  const bLeg = leg(P.bth, P.bsh, -9), fLeg = leg(P.fth, P.fsh, 9);
  const dark = c => HD.shade(c, -.18);
  const sleeve = L.sleeve === "short" ? skin : top;
  const drawArm = (pts, back) => {
    limb([pts[0], pts[1]], back ? dark(top) : top, 17);
    limb([pts[1], pts[2]], back ? dark(sleeve) : sleeve, 15);
    ink(circ(pts[2][0], pts[2][1], 8.5), back ? dark(skin) : skin, true, 3);
    if (L.tattoo && !back) for (let i = 0; i < 3; i++) { const q = [lerp(pts[1][0], pts[2][0], .3 + i * .2), lerp(pts[1][1], pts[2][1], .3 + i * .2)]; ctx.fillStyle = "#355d8a"; ctx.fillRect(q[0] - 2, q[1] - 2, 4, 4); }
  };
  const drawLeg = (pts, back) => {
    const col = L.dress ? skin : bottom;
    limb([pts[0], pts[1], pts[2]], back ? dark(col) : col, 19);
    const f = pts[2];
    ink(circ(f[0] + 9, f[1] + 3, 16, 8.5), back ? dark(shoe) : shoe, true, 3);
  };

  const perp0 = [Math.cos(P.lean * D2R), Math.sin(P.lean * D2R)];
  if (L.cape && !P.rot) {   // 披风：从肩膀垂到膝盖，走动时往后飘
    const fl = (st.moving ? 26 + Math.sin(t * 12) * 8 : 0) + Math.sin(t * 2.2 + a.seed) * 4;
    ink([[neck[0] - perp0[0] * 22 - 4, neck[1] + 2], [neck[0] + perp0[0] * 20, neck[1] - 2], [hip[0] + 4, hip[1] + 20],
         [hip[0] - 4, hip[1] + 66], [hip[0] - 40 - fl, hip[1] + 62 - fl * .3], [hip[0] - 46 - fl * 1.2, hip[1] + 8 - fl * .5]], L.cape);
  }
  // 后侧手脚
  drawArm(bArm, true); drawLeg(bLeg, true);
  if (L.shield && !P.rot) {   // 盾牌（罗马大方盾）挂在后侧手上
    const [hx0, hy0] = bArm[2], sw = 30, sh = 52, rot = (P.bua + P.lean) * .25 * D2R;
    ctx.save(); ctx.translate(hx0 + 12, hy0 - 6); ctx.rotate(rot);
    ink([[-sw, -sh], [sw, -sh], [sw + 4, sh], [-sw - 4, sh]], L.shield, true, 3.2);
    ink([[-sw + 6, -sh + 6], [sw - 6, -sh + 6], [sw - 4, sh - 6], [-sw + 4, sh - 6]], null, true, 1.8, .6, L.shieldTrim || "#d9a52b");
    ink(circ(0, 0, 9), L.shieldTrim || "#d9a52b", true, 2.2);
    ink([[0, -sh + 8], [0, -10]], null, false, 2.2, .5, L.shieldTrim || "#d9a52b"); ink([[0, 10], [0, sh - 8]], null, false, 2.2, .5, L.shieldTrim || "#d9a52b");
    ctx.restore();
  }
  // 身体（上衣）+ 裙子
  const perp = [Math.cos(P.lean * D2R), Math.sin(P.lean * D2R)];
  const tw = 25 * bodyS, bw = 27 * bodyS;
  const torso = [[neck[0] - perp[0] * tw, neck[1] - perp[1] * tw + 4], [neck[0] + perp[0] * tw, neck[1] + perp[1] * tw + 4],
                 [hip[0] + perp[0] * bw, hip[1] + perp[1] * bw + 6], [hip[0] - perp[0] * bw, hip[1] - perp[1] * bw + 6]];
  drawLeg(fLeg, false);
  if (L.dress) {
    const sway = Math.sin(t * 2.3 + a.seed) * 4 + (st.moving ? Math.sin(t * 10) * 6 : 0);
    if (L.robe) {   // 古人的长袍：一直垂到脚面
      const hem = Math.max(hip[1] + 30, -12 - (P.crouch > 20 ? 0 : 0));
      ink([[hip[0] - 25, hip[1] - 16], [hip[0] + 25, hip[1] - 16], [hip[0] + 40 + sway, hem], [hip[0] + 10 + sway * .5, hem + 4], [hip[0] - 38 + sway, hem]], L.dressColor || top);
      ink([[hip[0] + 4, hip[1] - 10], [hip[0] + 8 + sway * .6, hem - 2]], null, false, 2.2);
    } else
    ink([[hip[0] - 24, hip[1] - 14], [hip[0] + 24, hip[1] - 14], [hip[0] + 46 + sway, hip[1] + 58], [hip[0] - 44 + sway, hip[1] + 60]], L.dressColor || top);
  }
  if (L.tunic && !L.dress) ink([[hip[0] - 26, hip[1] - 12], [hip[0] + 26, hip[1] - 12], [hip[0] + 33, hip[1] + 34], [hip[0] - 31, hip[1] + 34]], L.tunic);
  if (L.pteruges) for (let i = -2; i <= 2; i++) ink([[hip[0] + i * 11 - 4.5, hip[1] - 4], [hip[0] + i * 11 + 4.5, hip[1] - 4], [hip[0] + i * 12.5 + 4.5, hip[1] + 26], [hip[0] + i * 12.5 - 4.5, hip[1] + 26]], L.pteruges, true, 2);
  ink(torso, L.armor || top);
  if (L.armor) for (const u of [.3, .5, .7]) {   // 环片甲的横条
    const cx0 = lerp(neck[0], hip[0], u), cy0 = lerp(neck[1], hip[1], u) + 4, hw = lerp(tw, bw, u) + 2;
    ink([[cx0 - perp[0] * hw, cy0 - perp[1] * hw], [cx0 + perp[0] * hw, cy0 + perp[1] * hw]], null, false, 2.4, .6);
  }
  if (L.toga) {   // 托加：从前肩斜搭到后腰的一条宽布，可带紫边
    const A = [neck[0] + perp[0] * tw * .7, neck[1] + 4], B = [hip[0] - perp[0] * bw * .9, hip[1] + 4];
    const dx = B[0] - A[0], dy = B[1] - A[1], l = Math.hypot(dx, dy) || 1, nx = -dy / l, ny = dx / l;
    ink([[A[0] - nx * 9, A[1] - ny * 9], [A[0] + nx * 9, A[1] + ny * 9], [B[0] + nx * 13, B[1] + ny * 13], [B[0] - nx * 13, B[1] - ny * 13]], L.toga, true, 2.8);
    if (L.togaBorder) ink([[A[0] + nx * 6, A[1] + ny * 6], [B[0] + nx * 9, B[1] + ny * 9]], null, false, 4, .5, L.togaBorder);
  }
  if (L.fur) {    // 毛皮领子
    const o = []; for (let i = 0; i <= 10; i++) { const u = i / 10, x = lerp(neck[0] - perp[0] * 30, neck[0] + perp[0] * 30, u), y = lerp(neck[1] - perp[1] * 30, neck[1] + perp[1] * 30, u) + 4;
      o.push([x, y + (i % 2 ? 16 : 6)]); }
    ink([[neck[0] - perp[0] * 30, neck[1] - 6]].concat(o, [[neck[0] + perp[0] * 30, neck[1] - 6]]), L.fur, true, 2.6);
  }
  if (L.belt) ink([torso[3], torso[2], [torso[2][0], torso[2][1] - 8], [torso[3][0], torso[3][1] - 8]], L.belt, true, 2.5);
  if (L.apron) ink([[hip[0] - 4, neck[1] + 30], [hip[0] + 22, neck[1] + 30], [hip[0] + 26, hip[1] + 30], [hip[0] - 6, hip[1] + 30]], L.apron, true, 2.5);
  if (L.medal) { ink(circ(neck[0] + 10, neck[1] + 28, 7), "#e8b83a", true, 2.5); }
  if (L.buttons) for (let i = 0; i < 3; i++) { ctx.fillStyle = INK; ctx.beginPath(); ctx.arc(lerp(neck[0], hip[0], .25 + i * .22) + 8, lerp(neck[1], hip[1], .25 + i * .22), 2.6, 0, 7); ctx.fill(); }

  // 头
  ctx.save(); ctx.translate(head[0], head[1]); ctx.rotate((P.lean + P.tilt) * D2R * .6);
  drawHead(a, L, E, t, headR, skin, deadK, blinkOK, st);
  ctx.restore();
  // 前侧手（和手里的东西）
  drawArm(fArm, false);
  const held = lastBefore(a.holds, t);
  if (held && held.icon && HD.drawIconAt) HD.drawIconAt(held.icon, fArm[2][0], fArm[2][1] - 6, (L.propScale || .55), (P.lean + P.fua + P.ffa) * .15);
  if ((L.cane || old && L.cane !== false) && Math.abs(P.fua) < 70 && !P.rot) { limb([[fArm[2][0] + 2, fArm[2][1]], [fArm[2][0] + 14, 0]], "#8a5a3a", 5); }
  // 死亡：灰一下
  ctx.restore();
  a.lastHead = [st.x + st.face * (head[0]) * st.s, st.y + head[1] * st.s, headR * st.s, st.face, st.s];
  if (P.rot) a.lastHead = [st.x - st.face * 190 * st.s * Math.abs(Math.sin(P.rot * D2R)), st.y - 50 * st.s, headR * st.s, st.face, st.s];
  a.lastState = st;
}

function drawHead(a, L, E, t, r, skin, deadK, blinkOK, st) {
  ctx.translate(-r * .72, 0);             // 下面的形状以 (0.72r, 0) 为头心来画，这里挪回锚点
  const hair = L.hair || "short", hc = L.hairColor || "#3a2a22";
  // 后面的头发
  if (hair === "bob") ink([[r * .2, -r * .95], [-r * .5, -r * .3], [-r * .52, r * 1.0], [r * .7, r * 1.02], [r * .75, r * .3]], hc);
  if (hair === "long" || hair === "braid") ink([[r * .1, -r * .75], [-r * .42, -r * .1], [-r * .5, r * 1.3], [r * .05, r * 1.55], [r * .55, r * 1.35], [r * .75, r * .2]], hc);
  if (hair === "bun") ink(circ(-r * .12, -r * .72, r * .4), hc);
  if (hair === "braid") ink(circ(-r * .2, r * 1.75, 11, 16), hc);
  if (L.hat === "veil") ink([[-r * .38, -r * .9], [r * 1.32, -r * 1.12], [r * 1.25, r * .2], [r * .32, r * 1.75], [-r * .6, r * 1.45]], "#2b2b33");
  // 耳朵 + 脸
  ink(circ(r * .72, 0, r * .95, r * 1.0), skin, true, OUT);
  ink(circ(r * .02, r * .12, 9, 12), skin, true, 2.8); ink(circ(r * .04, r * .12, 3.5, 6, -1.2, 1.2, 8), null, false, 2);
  // 胡子
  if (L.beard === "full") ink([[-r * .15, r * .25], [r * .95, r * .35], [r * 1.25, r * .7], [r * .6, r * 1.25], [r * .05, r * .95]], L.beardColor || hc);
  if (L.beard === "goatee") ink([[r * .75, r * .75], [r * 1.05, r * .72], [r * .92, r * 1.12]], L.beardColor || hc);
  // 前面的头发
  const bump = n => { const o = []; for (let i = 0; i <= n; i++) { const a0 = lerp(Math.PI * 1.02, Math.PI * 2.06, i / n); const rr = r * (1.04 + (i % 2) * .09); o.push([r * .72 + rr * Math.cos(a0) * .98, rr * Math.sin(a0) * .98]); } return o; };
  if (hair === "short" || hair === "long" || hair === "braid" || hair === "bun") {
    const pts = circ(r * .72, 0, r * 1.02, r * 1.04, Math.PI * 1.02, Math.PI * 1.98, 18);
    pts.push([r * 1.62, -r * .3], [r * 1.1, -r * .5], [r * .7, -r * .32], [r * .3, -r * .5], [-r * .1, -r * .1], [-r * .3, r * .35], [-r * .28, 0]);
    ink(pts, hc);
  } else if (hair === "curly") ink(bump(14).concat([[r * 1.5, -r * .4], [r * .4, -r * .5], [-r * .3, r * .3]]), hc);
  else if (hair === "spiky") { const o = []; for (let i = 0; i <= 9; i++) { const a0 = lerp(Math.PI * 1.02, Math.PI * 1.98, i / 9), rr = r * (i % 2 ? 1.35 : 1.0); o.push([r * .72 + rr * Math.cos(a0), rr * Math.sin(a0)]); } o.push([r * .5, -r * .45], [-r * .2, 0]); ink(o, hc); }
  else if (hair === "bald") { ink([[-r * .15, -r * .1], [-r * .05, -r * .5], [r * .15, -r * .35], [r * .05, r * .1]], hc); }
  else if (hair === "bob") {   // 齐刘海波波头（埃及女王）
    const pts = circ(r * .72, 0, r * 1.06, r * 1.08, Math.PI * 1.0, Math.PI * 1.97, 18);
    pts.push([r * 1.72, -r * .28], [r * .75, -r * .3], [r * .42, -r * .3], [r * .45, r * 1.0], [-r * .15, r * 1.0], [-r * .34, r * .2]);
    ink(pts, hc);
  }
  else if (hair === "wild") { ink(bump(16).concat([[r * 1.6, -r * .1], [r * .9, -r * .55], [-r * .5, r * .5]]), hc); }
  // 脸部
  const fx = r * 1.05, fy = 0;
  drawFace(a, E, t, fx, fy, r, deadK, blinkOK, L);
  if (L.beard === "mustache" || L.beard === "full") ink([[fx - 10, fy + r * .36], [fx + 6, fy + r * .3], [fx + 22, fy + r * .38], [fx + 26, fy + r * .5], [fx + 8, fy + r * .44], [fx - 8, fy + r * .5]], L.beardColor || hc, true, 2.5);
  if (L.glasses) { ink(circ(fx + 4, fy - 4, 11), null, true, 2.5); ink(circ(fx - 22, fy - 4, 10), null, true, 2.5); }
  // 帽子
  const hat = L.hat;
  if (hat === "military") { ink([[-r * .2, -r * .55], [r * 1.5, -r * .72], [r * 1.35, -r * 1.35], [-r * .05, -r * 1.2]], L.hatColor || "#5c6b4a"); ink([[r * 1.2, -r * .7], [r * 2.0, -r * .6], [r * 1.9, -r * .5], [r * 1.2, -r * .55]], INK, true, 2); }
  if (hat === "sombrero") { ink(circ(r * .72, -r * .78, r * 1.9, r * .28), L.hatColor || "#d7b36a"); ink([[r * .05, -r * .8], [r * .25, -r * 1.55], [r * 1.25, -r * 1.55], [r * 1.4, -r * .8]], L.hatColor || "#d7b36a"); }
  if (hat === "crown") ink([[r * .1, -r * .8], [r * .05, -r * 1.45], [r * .45, -r * 1.1], [r * .72, -r * 1.55], [r * 1.0, -r * 1.1], [r * 1.4, -r * 1.45], [r * 1.35, -r * .8]], "#f0c64c");
  if (hat === "cap") { ink([[-r * .2, -r * .5], [r * .3, -r * 1.12], [r * 1.2, -r * 1.0], [r * 1.55, -r * .55]], L.hatColor || "#c23b2e"); ink([[r * 1.2, -r * .62], [r * 2.1, -r * .5], [r * 1.5, -r * .42]], L.hatColor || "#c23b2e", true, 2.5); }
  if (hat === "straw") { ink(circ(r * .72, -r * .7, r * 1.7, r * .3), "#e2c37a"); ink(circ(r * .72, -r * .95, r * .85, r * .45, Math.PI, Math.PI * 2), "#e2c37a"); }
  if (hat === "bow") { ink([[-r * .3, -r * 1.0], [-r * .75, -r * 1.35], [-r * .7, -r * .7]], L.hatColor || "#d6457a", true, 2.5); ink([[-r * .3, -r * 1.0], [r * .1, -r * 1.35], [r * .05, -r * .7]], L.hatColor || "#d6457a", true, 2.5); }
  if (hat === "guan") {   // 束发小冠 + 簪子
    ink(circ(r * .5, -r * 1.02, r * .34, r * .24), hc);
    ink([[r * .26, -r * 1.05], [r * .3, -r * 1.42], [r * .74, -r * 1.42], [r * .78, -r * 1.05]], L.hatColor || "#2d2824", true, 2.8);
    ink([[-r * .1, -r * 1.24], [r * 1.12, -r * 1.24]], null, false, 3.4, .6, L.pinColor || "#c9a13b");
  }
  if (hat === "mian") {   // 帝王的冕：平板 + 垂旒
    ink([[r * .22, -r * .86], [r * .28, -r * 1.3], [r * 1.12, -r * 1.3], [r * 1.18, -r * .86]], L.hatColor || "#2d2824", true, 2.8);
    ink([[-r * .5, -r * 1.3], [r * 1.9, -r * 1.42], [r * 1.9, -r * 1.56], [-r * .5, -r * 1.44]], L.hatColor || "#2d2824", true, 2.8);
    for (let i = 0; i < 4; i++) { const x = r * 1.5 + i * r * .12; ink([[x, -r * 1.4], [x, -r * .95]], null, false, 1.6, .3); ink(circ(x, -r * .92, 3.2), i % 2 ? "#c23b2e" : "#e8b83a", true, 1.2); }
    for (let i = 0; i < 2; i++) { const x = -r * .38 + i * r * .14; ink([[x, -r * 1.38], [x, -r * .98]], null, false, 1.6, .3); ink(circ(x, -r * .95, 3.2), "#e8b83a", true, 1.2); }
  }
  if (hat === "laurel") {   // 桂冠：从后脑绕到额头的一圈叶子，叶尖朝后
    const col = L.hatColor || "#6f8a3a", n = 9;
    for (let i = 0; i <= n; i++) {
      const u = i / n, x = lerp(-r * .32, r * 1.5, u), y = lerp(-r * .2, -r * .62, u) - Math.sin(u * Math.PI) * r * .42;
      const ang = Math.atan2(-r * .42 - Math.cos(u * Math.PI) * Math.PI * r * .42, r * 1.82) + Math.PI;
      for (const side of [-1, 1]) {
        const lx = x + Math.cos(ang + side * .55) * 11, ly = y + Math.sin(ang + side * .55) * 11;
        ink(circ(lx, ly, 9, 4.2).map(([px, py]) => { const dx = px - lx, dy = py - ly, c0 = Math.cos(ang + side * .55), s0 = Math.sin(ang + side * .55); return [lx + dx * c0 - dy * s0, ly + dx * s0 + dy * c0]; }), col, true, 1.6, .4);
      }
    }
  }
  if (hat === "galea") {   // 罗马军团头盔：铁盔 + 护颊 + 红色盔缨
    const mc = L.hatColor || "#b7b0a2", cr = L.crest || "#b8322a";
    ink([[r * .02, -r * 1.02], [r * .72, -r * 1.85], [r * 1.42, -r * 1.02], [r * 1.1, -r * .98], [r * .72, -r * 1.55], [r * .34, -r * .98]], cr, true, 2.4);
    for (let i = 0; i < 5; i++) ink([[r * (.3 + i * .21), -r * 1.05], [r * (.36 + i * .18), -r * (1.35 + (i % 2) * .15)]], null, false, 1.4, .3);
    const dome = circ(r * .72, -r * .08, r * 1.08, r * 1.1, Math.PI * .98, Math.PI * 2.02, 20);
    dome.push([r * 1.95, -r * .32], [r * 1.8, -r * .22], [r * .72, -r * .3], [-r * .42, r * .1], [-r * .55, r * .42], [-r * .62, r * .22]);
    ink(dome, mc, true, 3);
    ink([[r * .36, -r * .3], [r * .8, -r * .3], [r * .78, r * .62], [r * .5, r * .78], [r * .3, r * .5]], mc, true, 2.4);
    ink([[-r * .3, -r * .62], [r * 1.72, -r * .5]], null, false, 2, .4);
  }
  if (hat === "diadem" || hat === "uraeus") {   // 头箍（晚期罗马皇帝的珍珠头箍 / 埃及女王的金带 + 圣蛇）
    const g = L.hatColor || "#e2b23c";
    ink([[-r * .34, -r * .46], [r * 1.62, -r * .66], [r * 1.64, -r * .5], [-r * .3, -r * .3]], g, true, 2.2);
    if (hat === "diadem") for (let i = 0; i < 6; i++) ink(circ(lerp(-r * .2, r * 1.45, i / 5), lerp(-r * .38, -r * .58, i / 5), 3.4), "#fbf6e8", true, 1.2, .2);
    else { ink([[r * 1.5, -r * .62], [r * 1.62, -r * 1.02], [r * 1.78, -r * 1.0], [r * 1.66, -r * .6]], g, true, 2); ink(circ(r * 1.72, -r * 1.02, 4), "#c0392b", true, 1.2); }
    ink(circ(r * 1.5, -r * .6, 5.5), hat === "diadem" ? "#3a6fb5" : "#2e8b7a", true, 1.6);
  }
  if (hat === "flower") { for (let i = 0; i < 5; i++) { const aa = i / 5 * Math.PI * 2; ink(circ(r * .05 + Math.cos(aa) * 8, -r * .85 + Math.sin(aa) * 8, 6), "#f3a3b8", true, 2); } ink(circ(r * .05, -r * .85, 4), "#f0c64c", true, 1.5); }
}

function drawFace(a, E, t, fx, fy, r, deadK, blinkOK, L) {
  const e = E.name;
  // 眨眼：每个人自己的节奏
  const R = rng(a.seed + 5); const period = 2.6 + R() * 2.2, off = R() * 3;
  const blink = blinkOK && ((t + off) % period) < .13 && !["happy", "laugh", "sleepy", "dead", "cry", "love"].includes(e);
  const ex1 = fx + 6, ex2 = fx - 20, ey = fy - 6;
  ctx.lineCap = "round"; ctx.strokeStyle = INK; ctx.fillStyle = INK;
  const line = (pts, w = 3.4) => { ctx.lineWidth = w; ctx.beginPath(); pts.forEach((p, i) => i ? ctx.lineTo(p[0], p[1]) : ctx.moveTo(p[0], p[1])); ctx.stroke(); };
  const eye = (x, y, s = 1) => {
    if (blink) return line([[x - 6, y], [x + 6, y]]);
    if (e === "happy" || e === "laugh" || e === "love") return line([[x - 7, y + 2], [x, y - 5], [x + 7, y + 2]]);
    if (e === "sleepy") return line([[x - 7, y], [x + 7, y + 1]]);
    if (e === "dead") { line([[x - 6, y - 6], [x + 6, y + 6]]); return line([[x + 6, y - 6], [x - 6, y + 6]]); }
    if (e === "cry") return line([[x - 7, y - 2], [x, y + 4], [x + 7, y - 2]]);
    const big = e === "surprised" || e === "scared" ? 1.35 : 1;
    ctx.beginPath(); ctx.ellipse(x, y, 5.2 * big * s, 7 * big * s, 0, 0, 7); ctx.fill();
    ctx.fillStyle = "#fff"; ctx.beginPath(); ctx.arc(x + 1.8, y - 2.4, 1.9 * big, 0, 7); ctx.fill(); ctx.fillStyle = INK;
  };
  eye(ex1, ey); eye(ex2, ey, .9);
  if (L.eyepatch) { ctx.fillStyle = INK; ctx.beginPath(); ctx.ellipse(ex1, ey, 11, 10, 0, 0, 7); ctx.fill();
    line([[ex1 - 8, ey - 7], [fx - r * 1.1, fy - r * .55]], 3); line([[ex1 + 9, ey - 4], [fx + 6, fy - r * .48]], 3); ctx.fillStyle = INK; }
  // 眉毛
  const brow = {angry: [8, -4], determined: [5, -2], sad: [-7, 2], cry: [-8, 2], scared: [-6, -3], surprised: [0, -6], thinking: [4, -2], smug: [3, 0], shy: [-4, 0]}[e];
  if (brow) {
    line([[ex1 - 8, ey - 13 - brow[1] + (brow[0] > 0 ? -brow[0] / 2 : brow[0] / 2)], [ex1 + 8, ey - 13 - brow[1] + (brow[0] > 0 ? brow[0] / 2 : -brow[0] / 2)]], 3);
    line([[ex2 - 7, ey - 13 - brow[1] + (brow[0] > 0 ? brow[0] / 2 : -brow[0] / 2)], [ex2 + 7, ey - 13 - brow[1] + (brow[0] > 0 ? -brow[0] / 2 : brow[0] / 2)]], 3);
  } else if (!["happy", "laugh", "love", "sleepy", "dead"].includes(e)) {
    line([[ex1 - 7, ey - 14], [ex1 + 7, ey - 15]], 2.6); line([[ex2 - 6, ey - 15], [ex2 + 6, ey - 14]], 2.6);
  }
  // 鼻子
  line([[fx + 14, fy + 4], [fx + 19, fy + 10], [fx + 13, fy + 12]], 2.6);
  // 嘴：说话时跟着配音的响度张合
  const mx = fx + 2, my = fy + r * .5;
  const talk = a.talks.find(k => t >= k.t && t < k.end);
  const env = HD.envAt ? HD.envAt(t) : 0;
  if (talk && env > .05 && e !== "dead") {
    const o = 2 + env * 11;
    ctx.fillStyle = "#6b2a2a"; ctx.beginPath(); ctx.ellipse(mx, my + o * .3, 8 + env * 2, o, 0, 0, 7); ctx.fill();
    ctx.lineWidth = 2.6; ctx.stroke(); ctx.fillStyle = INK;
  } else if (e === "happy" || e === "love") { ctx.lineWidth = 3.2; ctx.beginPath(); ctx.arc(mx, my - 4, 10, .25, Math.PI - .25); ctx.stroke(); }
  else if (e === "laugh") { ctx.fillStyle = "#6b2a2a"; ctx.beginPath(); ctx.arc(mx, my - 3, 11, 0, Math.PI); ctx.closePath(); ctx.fill(); ctx.lineWidth = 2.6; ctx.stroke(); ctx.fillStyle = INK; }
  else if (e === "sad" || e === "cry") { ctx.lineWidth = 3.2; ctx.beginPath(); ctx.arc(mx, my + 8, 9, Math.PI + .35, -.35); ctx.stroke(); }
  else if (e === "surprised" || e === "scared") { ctx.lineWidth = 3; ctx.beginPath(); ctx.ellipse(mx, my, 6, 8, 0, 0, 7); ctx.stroke(); }
  else if (e === "angry" || e === "determined") line([[mx - 9, my + 2], [mx + 9, my]], 3.2);
  else if (e === "smug") line([[mx - 9, my + 1], [mx + 2, my + 2], [mx + 10, my - 4]], 3);
  else if (e === "dead" || e === "sleepy") line([[mx - 7, my + 1], [mx + 7, my + 1]], 2.6);
  else if (e === "thinking") line([[mx - 7, my + 2], [mx + 7, my - 1]], 2.8);
  else { ctx.lineWidth = 3; ctx.beginPath(); ctx.arc(mx, my - 2, 7, .35, Math.PI - .35); ctx.stroke(); }
  // 腮红 / 眼泪 / 害羞
  if (["love", "shy", "happy", "laugh"].includes(e) || L.blush) { ctx.fillStyle = "rgba(233,120,130,.45)"; ctx.beginPath(); ctx.ellipse(fx - 8, fy + 14, 9, 5, 0, 0, 7); ctx.fill(); ctx.beginPath(); ctx.ellipse(fx + 22, fy + 14, 6, 4, 0, 0, 7); ctx.fill(); }
  if (e === "cry") {
    ctx.fillStyle = "#7fb7e6";
    for (let i = 0; i < 3; i++) { const k = ((t * 1.4 + i / 3) % 1); ctx.globalAlpha *= 1; ctx.beginPath(); ctx.ellipse(ex1 + 2, ey + 8 + k * 46, 3.2, 5, 0, 0, 7); ctx.fill(); ctx.beginPath(); ctx.ellipse(ex2, ey + 8 + ((k + .5) % 1) * 40, 3, 4.6, 0, 0, 7); ctx.fill(); }
  }
  if (e === "angry") { ctx.strokeStyle = "#c23b2e"; line([[fx - 34, fy - 38], [fx - 26, fy - 30]], 3); line([[fx - 26, fy - 38], [fx - 34, fy - 30]], 3); }
}

// ───────────── 气泡 / 情绪符号（画在最上层，不跟着人物镜像） ─────────────
function wrap(text, n) {
  const out = [];
  for (const para of text.split("\n")) { let s = para; while (s.length > n) { out.push(s.slice(0, n)); s = s.slice(n); } out.push(s); }
  return out;
}
function drawOverlays(id, t) {
  const a = A[id]; const st = state(id, t); if (!st || !a.lastHead) return;
  if (HD.windOf && HD.windOf(id, t)) return;
  const [hx, hy, hr, face, s] = a.lastHead;
  ctx.save(); ctx.globalAlpha = st.alpha;
  // 对话气泡：显示到下一句为止，最长 ~ 字数×0.22+1.6 秒
  const i = a.says.findIndex(q => q.t > t); const idx = (i < 0 ? a.says.length : i) - 1;
  if (idx >= 0) {
    const q = a.says[idx], next = a.says[idx + 1];
    const life = Math.min(next ? next.t - q.t : 99, 1.6 + q.text.length * .22);
    const k = t - q.t;
    if (k < life) {
      const inK = easeBack(k / .3), outK = 1 - clamp((k - life + .25) / .25);
      const lines = wrap(q.text, 9), fs = 38, lh = fs * 1.2;
      ctx.font = fH(fs);
      const w = Math.max(...lines.map(l => ctx.measureText(l).width)) + 44, h = lines.length * lh + 26;
      const bx = hx + face * (hr * .4 + w * .3), by = hy - hr * 1.35 - h / 2 - 18 * s;
      ctx.save(); ctx.translate(bx, by + h / 2); ctx.scale(inK * outK, inK * outK); ctx.translate(-bx, -(by + h / 2));
      SEED = a.seed + idx * 17 + Math.floor(t * 8) * 7;
      if (q.kind === "think") {
        const cl = []; const n = 14; for (let j = 0; j <= n; j++) { const aa = j / n * Math.PI * 2; const rr = 1 + .1 * Math.sin(aa * 7); cl.push([bx + Math.cos(aa) * (w / 2 + 12) * rr, by + h / 2 + Math.sin(aa) * (h / 2 + 12) * rr]); }
        ink(cl, BUB, true, 3);
        ink(circ(hx + face * hr * .5, hy - hr * 1.1, 8), BUB, true, 2.5); ink(circ(hx + face * hr * .75, hy - hr * 1.45, 12), BUB, true, 2.5);
      } else {
        const rr = []; const x0 = bx - w / 2, y0 = by, R2 = 18;
        const pts = HD.roundRectPts(x0, y0, w, h, R2);
        // 尾巴：在底边插入一个尖角指向头
        const tx = clamp(hx + face * hr * .3, x0 + 30, x0 + w - 30);
        const bottomIdx = pts.findIndex(p => p[1] >= y0 + h - .5 && p[0] <= tx);
        const tail = [[tx + 14, y0 + h], [hx + face * hr * .2, hy - hr * 1.05], [tx - 8, y0 + h]];
        const full = pts.slice(0, Math.max(1, bottomIdx)).concat(tail, pts.slice(Math.max(1, bottomIdx)));
        ink(full, BUB, true, 3.2);
      }
      lines.forEach((l, j) => { ctx.fillStyle = BUBTXT; ctx.textAlign = "center"; ctx.textBaseline = "middle"; ctx.font = fH(fs); ctx.fillText(l, bx, by + 13 + lh * (j + .5)); });
      ctx.restore();
    }
  }
  // 情绪符号
  for (const em of a.emotes) {
    const k = t - em.t; if (k < 0 || k > 2.6) continue;
    const al = 1 - clamp((k - 2.1) / .5), pop = easeBack(k / .3);
    ctx.save(); ctx.globalAlpha = st.alpha * al;
    const ex = hx + face * hr * .9, ey = hy - hr * 1.3;
    SEED = a.seed + 999 + Math.floor(t * 8);
    if (em.name === "heart") for (let j = 0; j < 3; j++) { const kk = (k * .6 + j * .33) % 1; ctx.fillStyle = "#e45b7f"; ctx.font = `${30 + j * 6}px serif`; ctx.textAlign = "center"; ctx.fillText("♥", ex + Math.sin(kk * 6 + j) * 16 + j * 14 - 14, ey - kk * 90); }
    else if (em.name === "sweat") { ctx.fillStyle = "#7fb7e6"; const yy = hy - hr * .6 + k * 18; ctx.beginPath(); ctx.moveTo(hx - face * hr * .9, yy - 14); ctx.quadraticCurveTo(hx - face * hr * .9 + 9, yy + 6, hx - face * hr * .9, yy + 8); ctx.quadraticCurveTo(hx - face * hr * .9 - 9, yy + 6, hx - face * hr * .9, yy - 14); ctx.fill(); ctx.strokeStyle = INK; ctx.lineWidth = 2; ctx.stroke(); }
    else if (em.name === "anger") { ctx.strokeStyle = C.red; ctx.lineWidth = 5; for (const [dx, dy] of [[-1, -1], [1, -1], [-1, 1], [1, 1]]) { ctx.beginPath(); ctx.arc(ex + dx * 9, ey + dy * 9, 7, 0, Math.PI * 1.3); ctx.stroke(); } }
    else if (em.name === "zzz") { ctx.fillStyle = C.blue || "#5b9bd5"; ctx.font = fH(36); for (let j = 0; j < 3; j++) { const kk = (k * .7 + j * .33) % 1; ctx.globalAlpha = st.alpha * al * (1 - kk); ctx.fillText("z", ex + kk * 50 + j * 4, ey - kk * 70); } }
    else if (em.name === "music") { ctx.fillStyle = C.wo || "#8a55b8"; ctx.font = `40px serif`; for (let j = 0; j < 2; j++) { const kk = (k * .6 + j * .5) % 1; ctx.fillText(j ? "♫" : "♪", ex + Math.sin(kk * 7) * 20 + j * 30, ey - kk * 80); } }
    else if (em.name === "sparkle") { ctx.strokeStyle = "#e8b83a"; ctx.lineWidth = 4; for (let j = 0; j < 4; j++) { const aa = j / 4 * Math.PI * 2 + k; const rr = 40 + 30 * pop; ctx.beginPath(); const x = hx + Math.cos(aa) * rr * 1.2, y = hy + Math.sin(aa) * rr; ctx.moveTo(x - 8, y); ctx.lineTo(x + 8, y); ctx.moveTo(x, y - 8); ctx.lineTo(x, y + 8); ctx.stroke(); } }
    else if (em.name === "shock") { ctx.strokeStyle = INK; ctx.lineWidth = 4; for (let j = 0; j < 5; j++) { const aa = -Math.PI / 2 + (j - 2) * .35; const r1 = hr * 1.3, r2 = hr * (1.3 + .5 * pop); ctx.beginPath(); ctx.moveTo(hx + Math.cos(aa) * r1, hy + Math.sin(aa) * r1); ctx.lineTo(hx + Math.cos(aa) * r2, hy + Math.sin(aa) * r2); ctx.stroke(); } }
    else if (em.name === "tears") { /* 用表情 cry */ }
    else { // "!" "?" 或任意短文字
      const sym = em.name === "!" || em.name === "excl" ? "!" : em.name === "?" || em.name === "q" ? "?" : em.name;
      ctx.translate(ex, ey); ctx.scale(pop, pop); ctx.rotate(Math.sin(k * 8) * .08 * (1 - clamp(k)));
      ctx.font = `bold 64px ${HAND}`; ctx.textAlign = "center"; ctx.textBaseline = "middle";
      ctx.lineWidth = 8; ctx.strokeStyle = BUB; ctx.strokeText(sym, 0, 0); ctx.fillStyle = sym === "!" ? C.red : INK; ctx.fillText(sym, 0, 0);
    }
    ctx.restore();
  }
  ctx.restore();
}

function bbox(id, t) {
  const st = t == null ? null : state(id, t);
  const a = A[id]; if (!a) return null;
  const d = a.def, s = d.s ?? 1;
  const x = st ? st.x : d.x, y = st ? st.y : d.y;
  return {x0: x - 110 * s, x1: x + 110 * s, y0: y - 330 * s, y1: y + 20 * s};
}

HD.Actors = {prep, draw: drawActor, overlays: drawOverlays, bbox, ids: () => Object.keys(A), has: id => !!A[id], POSES, EXPRS};
})();
