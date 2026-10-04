// 画风预设（每个视频按题材选一个，别每期都用 notebook）。world.js 里写：
//   const THEME = "roman";                                  // 直接用预设
//   const THEME = {base: "ink", C: {red: "#a8241c"}, line: {mul: 1.4}};   // 在预设上改几处
// 选择建议见 skill 的 references/themes.md。故事里还能用 `grade 名字` 给某一段调色（warm gold dusk ash night cold sepia none）。
//
// 字段：
//   C      调色板（覆盖 board.js 的默认色：paper 底色 dot 点阵 ink 墨线 soft 次要文字 red gold green blue sky out orange navy purple love，
//          sea/land 地图的海和陆地，panel 便签/事件卡/气泡底色）
//   bg     背景：kind = dots | grid | lines | plain；tile = mottle(灰泥斑驳) | rice(宣纸纤维) | chalk(粉笔灰) | wash(水彩晕染) | null，tileAlpha
//   tex    盖在最上面的纸纹：kind = paper | parchment | rice | chalk | grain；vignette 暗角颜色
//   frame  画面边框：null | meander(古希腊回纹细框) | film(电影黑边) | brush(墨笔框)
//   fonts  hand 手写/气泡/便签，label 小标签，title 卡片名/大标题，sub 字幕；*W 是字重
//   line   mul 线宽倍数，amp 抖动倍数，style = pen(一笔) | double(双线素描/版画) | brush(毛笔粗细变化) | chalk(粉笔断续)
//   fill   alpha 填色浓度，hatch 阴影排线浓度（0 = 不排线），hatchGap 排线间距
//   card   style = round(圆角卡) | tablet(石碑/铜牌) | seal(宣纸+朱印)；radius
//   sticky 便签 bg / tape / text
//   sub    字幕 style = bar(圆角条) | banner(两端燕尾的绶带) | outline(无底，描边字)；bg fg alpha
//   hud    章节标签 style = tab | banner | seal | chalk
//   actor  人物 ink 描边色、out 描边粗细、boil 每秒重抖次数
//   bubble 气泡 fill / text
//   grades `grade` 指令可用的调色，见 board.js 的 GRADES（这里可以加或覆盖）
(() => {
const T = {};
T.notebook = {
  label: "手账点阵：米色纸 + 点阵 + 手写字（文学、生活、轻松讲书）",
  C: {},
  bg: {kind: "dots"}, tex: {kind: "paper", vignette: "rgba(60,40,20,0.20)"}, frame: null,
  fonts: {hand: "LongCang, KaiTi, serif", label: "KaiTi, STKaiti, serif", title: "LongCang, KaiTi, serif", sub: "'Microsoft YaHei', 'PingFang SC', sans-serif", subW: 600},
  line: {mul: 1, amp: 1, style: "pen"}, fill: {alpha: 1, hatch: 0, hatchGap: 7},
  card: {style: "round", radius: 16}, sticky: {bg: "#fff0a6", tape: "#d9d2c0"},
  sub: {style: "bar", bg: "#2b2521", fg: "#fffaf0", alpha: .78}, hud: {style: "tab"},
  actor: {ink: "#2d2824", out: 3.6, boil: 8}, bubble: {fill: "#fffdf6"},
};
T.roman = {
  label: "古典壁画 + 古地图：羊皮纸、赭红与金、版画排线、衬线字（西方古代史、神话、帝国、战争）",
  C: {paper: "#ead8b2", dot: "#d3bd92", ink: "#3a2516", soft: "#76593f", red: "#9e2a1f", love: "#b8456a", gold: "#c08a24",
      green: "#5f7434", blue: "#3f6f82", sky: "#cfe0dc", out: "#8f7d66", orange: "#c46a2b", navy: "#2c4a66", purple: "#6a2c5c",
      sea: "#b4cbc0", land: "#efe0bb", panel: "#f6ead0"},
  bg: {kind: "plain", tile: "mottle", tileAlpha: 1}, tex: {kind: "parchment", vignette: "rgba(70,35,10,0.34)"}, frame: "meander",
  fonts: {hand: "'Noto Serif SC', 'SimSun', serif", handW: 600, label: "'Noto Serif SC', 'SimSun', serif", labelW: 500,
          title: "'Noto Serif SC', 'SimSun', serif", titleW: 900, latin: "'Palatino Linotype', 'Book Antiqua', Georgia, serif",
          sub: "'Noto Serif SC', 'SimSun', serif", subW: 700},
  line: {mul: 1.1, amp: .9, style: "double"}, fill: {alpha: .95, hatch: .26, hatchGap: 6},
  card: {style: "tablet", radius: 6}, sticky: {bg: "#f2e2bb", tape: "#b49b74", text: "#3a2516"},
  sub: {style: "banner", bg: "#2a170d", fg: "#f6e6c2", alpha: .84}, hud: {style: "banner"},
  actor: {ink: "#3a2516", out: 3.8, boil: 8}, bubble: {fill: "#fbf1da"},
};
T.ink = {
  label: "水墨国风：宣纸、毛笔线、朱砂印（中国历史、古诗词、国学、武侠）",
  C: {paper: "#f2ede0", dot: "#e0d8c6", ink: "#1f1c1a", soft: "#5e5850", red: "#b3271e", love: "#c0475e", gold: "#a8823a",
      green: "#56735a", blue: "#46647a", sky: "#dde5e3", out: "#8d8679", orange: "#c0682c", navy: "#2f4a5f", purple: "#6b4c6e",
      sea: "#d5dcd6", land: "#f1ebdc", panel: "#f7f2e6"},
  bg: {kind: "plain", tile: "rice", tileAlpha: 1}, tex: {kind: "rice", vignette: "rgba(40,30,20,0.18)"}, frame: "brush",
  fonts: {hand: "MaShanZheng, KaiTi, serif", label: "KaiTi, STKaiti, serif", title: "MaShanZheng, KaiTi, serif",
          sub: "KaiTi, STKaiti, serif", subW: 700},
  line: {mul: 1.25, amp: 1.1, style: "brush"}, fill: {alpha: .78, hatch: 0, hatchGap: 7},
  card: {style: "seal", radius: 4}, sticky: {bg: "#f4ecd8", tape: "#c9b893", text: "#1f1c1a"},
  sub: {style: "outline", bg: "#f7f2e6", fg: "#1f1c1a", alpha: 1}, hud: {style: "seal"},
  actor: {ink: "#1f1c1a", out: 3.4, boil: 6}, bubble: {fill: "#faf6ec"},
};
T.chalk = {
  label: "黑板粉笔：墨绿黑板、粉笔线、粉笔灰（科普、数学物理、课堂知识点）",
  C: {paper: "#2e3a33", dot: "#3a4840", ink: "#f1efe6", soft: "#c4cbc0", red: "#f2897b", love: "#f3a0b8", gold: "#f1d37a",
      green: "#a8d58b", blue: "#8cc4ea", sky: "#5f7f8f", out: "#9aa69b", orange: "#f2b16d", navy: "#9cb7e6", purple: "#c7a4e8",
      sea: "#3a5560", land: "#3b4a40", panel: "#38463e"},
  bg: {kind: "plain", tile: "chalk", tileAlpha: 1}, tex: {kind: "chalk", vignette: "rgba(0,0,0,0.42)"}, frame: null,
  fonts: {hand: "LongCang, KaiTi, serif", label: "KaiTi, STKaiti, serif", title: "LongCang, KaiTi, serif", sub: "'Microsoft YaHei', sans-serif", subW: 600},
  line: {mul: 1.1, amp: 1.2, style: "chalk"}, fill: {alpha: .55, hatch: .32, hatchGap: 8},
  card: {style: "round", radius: 14}, sticky: {bg: "#f1d37a", tape: "#9aa69b", text: "#2e3a33"},
  sub: {style: "bar", bg: "#111614", fg: "#f1efe6", alpha: .8}, hud: {style: "chalk"},
  actor: {ink: "#1b211d", out: 3.6, boil: 8}, bubble: {fill: "#f1efe6", text: "#1b211d"},
};
T.blueprint = {
  label: "工程蓝图：蓝底白线 + 网格（科技、发明、工程、机器、建筑）",
  C: {paper: "#1d4e7a", dot: "#2d6595", ink: "#eaf3fb", soft: "#a9c7e3", red: "#ff8b7b", love: "#ffa3c0", gold: "#ffd479",
      green: "#9be3b0", blue: "#9fd4ff", sky: "#3d78a8", out: "#7fa6c9", orange: "#ffb46e", navy: "#cfe3ff", purple: "#d0b4ff",
      sea: "#245c8c", land: "#2a6697", panel: "#24598a"},
  bg: {kind: "grid"}, tex: {kind: "grain", vignette: "rgba(5,20,40,0.38)"}, frame: null,
  fonts: {hand: "LongCang, KaiTi, serif", label: "Consolas, 'Microsoft YaHei', monospace", title: "'Microsoft YaHei', sans-serif", titleW: 700,
          sub: "'Microsoft YaHei', sans-serif", subW: 600},
  line: {mul: .9, amp: .55, style: "pen"}, fill: {alpha: .4, hatch: .3, hatchGap: 9},
  card: {style: "tablet", radius: 2}, sticky: {bg: "#ffd479", tape: "#7fa6c9", text: "#16314d"},
  sub: {style: "bar", bg: "#0b2238", fg: "#eaf3fb", alpha: .82}, hud: {style: "tab"},
  actor: {ink: "#10283f", out: 3.4, boil: 8}, bubble: {fill: "#eaf3fb", text: "#10283f"},
};
T.noir = {
  label: "黑白默片：灰阶、排线阴影、胶片颗粒，只留红色（悬疑、犯罪、战争、悲剧、存在主义）",
  C: {paper: "#d8d5ce", dot: "#c2beb5", ink: "#141414", soft: "#4e4c48", red: "#b01e1e", love: "#b01e1e", gold: "#8f8a7e",
      green: "#6b6e66", blue: "#5c636b", sky: "#b9bcbf", out: "#7d7a74", orange: "#8a7c6a", navy: "#3c434b", purple: "#5a5260",
      sea: "#a9adb0", land: "#dcd9d2", panel: "#ebe8e1"},
  bg: {kind: "plain", tile: "mottle", tileAlpha: .55}, tex: {kind: "grain", vignette: "rgba(0,0,0,0.5)"}, frame: "film",
  fonts: {hand: "LongCang, KaiTi, serif", label: "KaiTi, STKaiti, serif", title: "'Noto Serif SC', SimSun, serif", titleW: 800,
          sub: "'Noto Serif SC', SimSun, serif", subW: 600},
  line: {mul: 1.15, amp: 1, style: "double"}, fill: {alpha: .85, hatch: .42, hatchGap: 5},
  card: {style: "tablet", radius: 3}, sticky: {bg: "#ebe8e1", tape: "#9a968e", text: "#141414"},
  sub: {style: "outline", bg: "#000000", fg: "#f2f0ea", alpha: 1}, hud: {style: "tab"},
  actor: {ink: "#141414", out: 3.8, boil: 10}, bubble: {fill: "#f2f0ea"}, grade: "ash",
};
T.storybook = {
  label: "水彩绘本：奶白纸、水彩晕染、柔和描边（童话、寓言、成长、温情故事）",
  C: {paper: "#fbf5ea", dot: "#eadfcb", ink: "#4b3a4f", soft: "#86728a", red: "#e0605a", love: "#ec7fa0", gold: "#efb94a",
      green: "#7fb069", blue: "#6aa6d6", sky: "#d7ebf5", out: "#b3a3a8", orange: "#f09a52", navy: "#4e6fa8", purple: "#a07cc5",
      sea: "#cde4ee", land: "#f6eedd", panel: "#fffaf2"},
  bg: {kind: "plain", tile: "wash", tileAlpha: 1}, tex: {kind: "paper", vignette: "rgba(120,80,60,0.14)"}, frame: null,
  fonts: {hand: "LongCang, KaiTi, serif", label: "KaiTi, STKaiti, serif", title: "LongCang, KaiTi, serif", sub: "'Microsoft YaHei', sans-serif", subW: 600},
  line: {mul: .95, amp: 1.3, style: "pen"}, fill: {alpha: .9, hatch: 0, hatchGap: 7, wash: true},
  card: {style: "round", radius: 26}, sticky: {bg: "#ffe3ec", tape: "#e6cfd6", text: "#4b3a4f"},
  sub: {style: "bar", bg: "#4b3a4f", fg: "#fffaf2", alpha: .8}, hud: {style: "tab"},
  actor: {ink: "#4b3a4f", out: 3.2, boil: 6}, bubble: {fill: "#fffaf2"},
};
T.zettel = {
  label: "卡片盒：牛皮纸桌面 + 白色索引卡（红线蓝格、卢曼编号）（学习方法、笔记、写作、效率、知识管理）",
  C: {paper: "#d6bd92", dot: "#c7ab7c", ink: "#2c2117", soft: "#62492f", red: "#c0392b", love: "#d0587a", gold: "#d99a2b",
      green: "#4f8a4a", blue: "#3a6ea5", sky: "#cfe0ea", out: "#8a7356", orange: "#d9772b", navy: "#2c4a6e", purple: "#7a4f8a",
      sea: "#c9d8d6", land: "#e8d6b2", panel: "#fffcf3"},
  bg: {kind: "plain", tile: "mottle", tileAlpha: .55}, tex: {kind: "paper", vignette: "rgba(70,40,10,0.30)"}, frame: null,
  fonts: {hand: "LongCang, KaiTi, serif", label: "KaiTi, STKaiti, serif", title: "LongCang, KaiTi, serif", sub: "'Microsoft YaHei', sans-serif", subW: 600},
  line: {mul: 1.05, amp: 1, style: "pen"}, fill: {alpha: .95, hatch: 0, hatchGap: 7},
  card: {style: "index", radius: 2}, sticky: {bg: "#fff0a6", tape: "#e8dcc0", text: "#2c2117"},
  sub: {style: "bar", bg: "#2c2117", fg: "#fffcf3", alpha: .82}, hud: {style: "tab"},
  actor: {ink: "#2c2117", out: 3.6, boil: 8}, bubble: {fill: "#fffcf3"},
};
window.THEMES = T;
})();
