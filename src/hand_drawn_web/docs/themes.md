# 画风（THEME）：按题材换一套视觉

用户的长期要求（2026-10-03）：**画风要跟着视频内容和主题变，不要每期都是同一个米色点阵本子**。
所以每做一期，第 3 步搭舞台之前先定画风，在 `web/world.js` 顶部写一行：

```js
const THEME = "roman";                                         // 直接用预设
const THEME = {base: "ink", C: {red: "#a8241c"}, line: {mul: 1.4}};   // 在预设上改几处
```
（别忘了 `window.WORLD = {THEME, …}`。预设定义在 `web/themes.js`，字段说明写在那个文件开头。）

## 预设怎么选

| 预设 | 样子 | 适合 | 配乐建议（META.music） |
|---|---|---|---|
| `roman` | 羊皮纸 + 灰泥斑驳、赭红与金、版画排线、衬线字、回纹边框、绶带字幕 | 西方古代史、神话、帝国、战争、宗教 | `preset: "minor", instr: "ancient"`（里拉琴 + 铜号 + 战鼓） |
| `ink` | 宣纸纤维、毛笔粗细线、朱砂印章卡片、竖排朱印章节、无底描边字幕 | 中国历史、诗词、国学、武侠、禅 | `minor` / `major`，bpm 60–72 |
| `chalk` | 墨绿黑板、粉笔断续线、粉笔灰、黄色便签 | 科普、数学物理、课堂知识点、学习方法 | `major` |
| `blueprint` | 蓝底白线网格、等宽标签 | 科技、发明、工程、机器、建筑、编程 | `major`，bpm 84–96 |
| `noir` | 灰阶 + 排线阴影 + 胶片颗粒 + 黑边，只留红色 | 悬疑、犯罪、战争、悲剧、存在主义文学（《局外人》这类） | `minor`，mood 多用 tense / dark |
| `storybook` | 奶白纸、水彩晕染、柔和描边、圆角卡 | 童话、寓言、成长、温情故事、亲子 | `major` |
| `zettel` | 牛皮纸桌面 + 白色索引卡（顶部色条、红线蓝格、左上角编号，卡片微微歪），卡片 `num` 字段写编号 | 学习方法、笔记、写作、效率、知识管理（42 卡片笔记写作法） | `major`，bpm 80–88 |
| `notebook` | 原来的米色点阵手账 + 手写字 | 生活方式、轻松讲书；**连续两期不要都用它** | 随意 |

拿不准就问自己：这个题材如果做成一本书，封面会是什么材质？羊皮卷 → roman，宣纸 → ink，课本 → chalk，图纸 → blueprint，黑白照片 → noir，绘本 → storybook。
跟上一期用的画风不一样，是硬性要求；同一个画风也可以用 `C`、`line`、`fill` 覆盖出变化。

## 片内也要变：`grade` 调色
`grade 名字` 让整个画面在 2.4 秒里换一种色调（盖在画面上，人物、地图、卡片一起变）：
`warm`（暖）、`gold`（金色盛世）、`dusk`（黄昏/流血/衰落）、`cold`（冷）、`ash`（褪色、去饱和）、`sepia`（老照片）、`night`（夜、亡国）、`blood`（血色）、`none`。
用法：按剧情的情绪曲线给每一章一个色调，比如罗马那期：崛起 warm → 盛世 gold → 刺杀/大火 dusk → 灭亡 night → 结尾 warm。
画风预设也可以带默认调色（`noir` 默认 `ash`）。

## 每个画风会改变什么
- 背景底色、跟镜头走的底纹（`bg.tile`：mottle / rice / chalk / wash）、点阵或网格
- 最上层纸纹（`tex`：paper / parchment / rice / chalk / grain，grain 每帧抖动成胶片颗粒）和暗角
- 画面边框（`frame`：meander 回纹 / film 黑边 / brush 墨笔）
- 线条：粗细倍数、抖动幅度、笔触（pen 一笔 / double 素描双线 / brush 毛笔粗细 / chalk 粉笔断续）
- 填色浓度、排线阴影（hatch）、水彩溢色（wash）
- 卡片外形（round 圆角 / tablet 切角石碑 + 圆钉 / seal 宣纸 + 朱印）、便签颜色
- 字体（手写 / 标签 / 标题 / 字幕各自的字体和字重）
- 字幕样式（bar 圆角条 / banner 燕尾绶带 / outline 无底描边）、章节标签（tab / banner / seal / chalk）
- 人物描边颜色和粗细、线条重抖频率、气泡底色

## 加一个新画风
在 `web/themes.js` 里照着已有的写一个对象即可；字体可以用系统字体，或把 OFL 字体放进 `web/fonts/` 并在 `index.html` 里 `@font-face`。
可用的中文字体：LongCang（手写）、MaShanZheng（毛笔楷）、ZhiMangXing（行草）、KaiTi、Noto Serif SC（宋体衬线，可 900 粗）、Noto Sans SC、Microsoft YaHei。
做完先用 `render.py --preview` 出几张图对比，再录音。
