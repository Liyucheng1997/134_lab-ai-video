# web/world.js 字段说明

世界坐标：y 向下为正；镜头缩放为 1 时 1 单位 = 1 像素（画面默认竖屏 1080×1920，由 story.META["size"] 决定）。人物站在 y = 0 的地面上。

## THEME（画风，必填）
`const THEME = "roman"` 或 `{base: "ink", …覆盖}`，见 themes.md。每期按题材选，别连着用同一个。

## C（颜色覆盖，可空）
默认有 `paper dot ink soft red love gold green blue sky out orange navy purple sea land panel`（画风预设会先改一遍，C 再覆盖）。

## KINDS（卡片类型）
```js
{hero: {color: "#e07a2e", label: "主角"}, villain: {color: "#2f6db5", label: "反派"}}
```
有 `label` 的会在第一张卡片出现后，常驻右上角小图例。

## ACTORS（动画人物）
见 characters.md。

## NODES（卡片）
```js
JA: {x: -680, y: 400, kind: "hero", name: "何塞·阿尔卡蒂奥", tag: "长子 · 力大无穷", gen: 2, swapTo: "villain"}
```
卡片宽度按名字自动算（约 名字字数×38+60）。并排的卡片中心至少隔 “两张卡片半宽之和 + 100”，关系线的标签才放得下。
`gen` 会在左上角显示 “G2”；`swapTo` 配合 `swap` 动作；`s` 整张卡片放大（如 1.2）；`seal` 是 seal 画风卡片上朱印里的字（默认名字第一个字）；`num` 是 index 卡片（zettel 画风）左上角的红色编号，如 "21a1"。

## EDGES（关系线）
| type | 画法 | 字段 |
|---|---|---|
| `marry` | 双实线（夫妻） | `a, b, label` |
| `child` | 从两位家长中点垂下来的箭头 | `from: [父, 母], to` |
| `adopt` | 点线箭头（收养） | `from: [..], to, label` |
| `lover` | 灰色虚线 | `a, b, label, bend` |
| `love` | 粉色虚线 + ♥（禁忌之爱、暗恋） | `a, b, label, bend` |
| `reject` | 灰虚线 + ✕ | `a, b, label` |
| `blood` | 粗红色蜿蜒线，慢慢流过去 | `a, b, caption` |
| `special` | 红色长虚线箭头（跨越时空的呼应） | `a, b, label, bend` |
| `dotted` | 棕色点线箭头（“研究”“来自”） | `a, b, label, bend` |

通用字段：`bend`（弧度，正负决定弯向哪边，0.2 左右）、`label`（中点的小标签）、`caption`（线旁的一句手写说明，`capDx/capDy` 调位置）、
`with`（画这条线时顺便画另一条）、`top: true`（画在卡片上层）。`a/b` 也可以是涂鸦 ID（如从“羊皮卷”连到人）。

## ICON_POS（涂鸦 / 道具 / 布景）
```js
stump: {x: 330, y: -58, s: 1.45},                        // id 就是画法名
sprouts: {icon: "sprout", x: -560, y: -38, s: 1.6},       // 用 icon 指定画法，同一种画法可以放多个
life: {icon: "lifebar", x: 1790, y: 0, len: 2000, color: "#8a55b8", text: "乌尔苏拉的一生"},
```
字段：`x y s`（s 省略为 1）、`top: true`（画在人物前面，地图上的标记都要加）、`cap`（下方小字）、`capY`、`capSize`、`capColor`、`flip`（左右翻转）、`hover`/`hoverSpeed`（一直上下浮动，如飞鸟、飘着的人）、`wobble`（轻轻摇摆，弧度）、`squash`（一伸一缩，如拍翅膀）、`dur`（画出来用几秒，默认 1.1）、`bob`（出现后慢慢上浮多少）、以及个别画法的参数（`len n text color`）。
涂鸦的中心就是 (x, y)，大约 ±60×s 大小；要“放在地上”，y 取 −(下半高度)，如树桩 `y: -58`。

**涂鸦库**（`board.js` 的 `ICONS`，人物也能 `hold` 其中任意一个）：
- 自然布景：`ground`(地平线，len) `house` `wall` `mountain` `sun` `moon` `cloud_big` `tree_big` `tree` `sea` `village` `ruin` `sprout` `weeds` `soil` `stump`
- 室内：`table` `chair` `bed` `door` `clock` `book` `letter` `bulb`
- 物品：`hoe` `rifle` `spear` `sword` `flag` `coin` `moneybag` `money` `gift` `basket` `scroll` `shroud` `sheet` `coffins` `crown` `magnet` `lens` `globe` `ice` `fish` `rabbit` `cows` `butterflies` `ants` `ghost` `heart` `star` `pigtail` `pigtail_ex`
- 现代/科普：`laptop` `phone` `chart` `bulb` `gear` `train` `boat`
- 古风/寓言：`kun`(鲲) `peng`(鹏) `cicada`(笑的蝉) `dove`(斑鸠) `sparrow`(小雀) `wren`(树枝上的小鸟) `mole`(鼹鼠) `weasel` `yak` `mushroom` `tree_old`(古树) `tree_ming` `gnarled`(疙瘩树) `gourd`/`gourd_broken`(葫芦) `jar`(药罐) `silk` `axe` `trap` `torch` `cup` `puddle` `grass_boat` `river`(len) `hut`(茅草屋) `cage_dead`(金笼里的死鸟) `wave_big`(len，大海) `splash` `whirl`(旋风) `wind` `seal`(红印章，text)
- 古代 / 罗马 / 战争：`wolf`(母狼和双胞胎) `temple`(神庙/元老院，text 写门楣) `colosseum` `arch`(凯旋门) `aqueduct`(引水渠) `galley`(战船，sail stripe color) `elephant`(战象，tower:false 去掉塔楼) `alps`(雪山) `tent`(军帐) `stone_wall`(石墙长城，len) `walls`(城墙塔楼，len，broken 缺口) `fire`(火焰，配 squash/wobble 会跳) `palace`(金宫) `throne` `bridge`(石拱桥) `chirho`(凯乐符号+光环) `cannon` `cannonball` `lyre`(里拉琴) `dagger` `scutum`(大方盾) `eagle`(罗马鹰旗) `dice` `laurel_big`(大桂冠) `crowns`/`crowns_x`(n 个皇冠 / 逐个打叉) `coin_silver`/`coin_bronze`(text 年份) `army_r`(方阵，cols rows color) `army_c`(新月阵，n r) `alesia`(山城+两圈围墙) `calendar` `abc` `scales`(天平) `bricks`(n 层砖) `sword_up` `helmet` `persia`
- 地图标记：`city`(小城堡，color) `battle`(交叉的剑 + 爆炸)
- 符号：`swap`(对调箭头) `target`(text) `loop`(text，周而复始) `dots` / `dots_cross`(n, text, color：一群人/逐个死去) `lifebar`(len, text, color) `drop` `cloud` `spiral` `war` `rome`
本片专用的涂鸦写在 world.js 的 `const ICONS = {名字(P, o) {…}}` 并放进 `window.WORLD`，会合并进涂鸦库（颜色写死十六进制，拿不到 board.js 里的 C）。
通用的缺的就在 board.js 的 `ICONS` 里照着写一个：用 `P.line / poly / circle / ellipse / arc / curve / text`，坐标在 ±60 左右，`{fill, c, w, dash}`。

## EVENTS + TIMELINE_META（竖向时间线）
```js
TIMELINE_META: {x: 2000, y0: -130, y1: 2520, title: "马孔多编年", ticks: [{y: -34, label: "第一代"}, {y: 366, label: "第二代"}]},
EVENTS: {ev_war: {y: 400, icon: "war", title: "内战", sub: "32 次起义，全部失败"}},
```
事件卡画在轴右侧；把事件的 y 和家谱里同一代的 y 对齐，时间线和人物关系就能一眼对上。

## GROUPS（分组框）
`{x0, x1, y0, y1, label}`，虚线圆角框，左上角写 label（如“第二代”）。

## NOTES（文字）
`{x, y, text, size, color, style}`，style：
- `title` 大标题 + 红色下划线；`plain` 普通手写字；`legend` 色块 + 文字（x 是色块位置）
- `sticky` 黄色便签（带胶带、微微歪）；`bubble` 对话气泡（`tail: [x, y]` 尖角指向说话者，给动物/涂鸦“说话”用，`fade` 收起）；`circle` 文字外面绕一个圈
`text` 里 `\n` 换行。便签文字 ≤ 12 字。

## AREAS（镜头区域）
`{x0, x1, y0, y1}`。`cam 区域名` 会框住它。没写 `all` 时自动取全部元素的外框。
舞台区域的高度大约取 “人物高度×s + 250”，人物才够大。

## WIND_KEEP
`wind` 飓风时不被吹走的元素 ID 列表（比如留下一张羊皮卷）。

## MAPS（会动的历史地图）
地图数据：`src/make_geo.py` 把 Natural Earth 1:50m（公有领域）的陆地/湖/河流裁到需要的经纬度范围，写成 `web/geo.js`
（第一次用：把 ne_50m_land / ne_50m_lakes / ne_50m_rivers_lake_centerlines 的 geojson 下载到 build/geo/，改 make_geo.py 里的 BOX，跑一次）。
```js
const MAP = {x: 0, y: -2700, k: 46, lon0: 19, lat0: 41};          // 投影：经纬度 → 世界坐标（k = 每度多少单位）
const geo = (lon, lat) => [MAP.x + (lon - MAP.lon0) * MAP.k * Math.cos(MAP.lat0 * Math.PI / 180), MAP.y - (lat - MAP.lat0) * MAP.k];
const MAPS = {med: {...MAP, box: [-10.5, 26, 49.5, 56.5], title: "Imperium Romanum", labelSize: 84,
  rivers: ["rhine", "danube", "nile"], mountains: [[[5.8, 44.2], [7.0, 45.9], [10.6, 46.7]]],
  eras: {e117: {label: "公元117年", sub: "版图最大", center: [36, 38], regions: [{color: "#9e2a1f", polys: [[[lon, lat], …], …], isles: [[lon, lat]], alpha: .34}]}}}};
```
- 版图多边形只在大陆上生效（会被大陆的轮廓裁掉），所以海里可以画得很随意；岛屿的中心落在任一多边形里就整块涂上，或在 `isles` 里点名。
- 一个时期可以有多块不同颜色的 region（如东西罗马）。`center` 是新版图开始晕开的地方。
- 地名、城市、战役：`NOTES` 的 plain 字 + `ICON_POS` 的 city / battle 涂鸦，坐标都用 `geo()`；镜头区域用经纬度框：`garea(lon0, lat0, lon1, lat1)`。
- 全图镜头缩放约 0.4，地图上的字要 44–90 号才看得清；放大看局部时（如意大利）缩放约 1。
- 地图放在舞台上方（y 负几千），镜头在舞台和地图之间来回：演一段 → 上去看版图变化 → 回来。

## ROUTES（行军 / 迁徙路线）
```js
r_hannibal: {pts: [geo(-1, 37.6), geo(4.6, 43.9), geo(16.1, 41.3)], color: "#6a2c5c", w: 7, dur: 6, token: "elephant", tokenS: .5, tokenDy: 34,
             label: "翻越阿尔卑斯", labelAt: .42, labelDy: -80, top: true},
```
点会用平滑曲线连起来；`dash: false` 画实线；`noArrow` 不要箭头；`keepToken` 走完后涂鸦留在终点。也能用在舞台的“沙盘”上（坐标直接写世界坐标），比如两翼包抄的箭头。
