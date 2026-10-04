# 画面指令全表（src/story.py）

```python
L("稿子文本", "关键词 > 动作; 动作", "关键词 > 动作", ..., hold=0, pause=None)
```

## 触发时刻（`>` 左边）
| 写法 | 含义 |
|---|---|
| `关键词` | 配音读到这个词的第一个字时（同一句里多个关键词按顺序往后找） |
| `^` | 本句开始前 0.25 秒（放镜头、hud、新元素入场） |
| `$` | 本句读完后 |
| `@1.5` | 本句开始后 1.5 秒（无声段里只能用 `@` / `^` / `$`） |

- `hold=1.2`：本句读完后多停 1.2 秒再进下一句（让动作/音效演完）。
- `L("", "@0 > …", pause=2.5)`：**无声段**，不配音，画面演 2.5 秒。
- 句首带 `hud` 的句子视为新章节，前面自动多停一点。

## 画布元素
| 动作 | 说明 |
|---|---|
| `card ID` | 画出人物/概念卡片（NODES） |
| `edge ID` | 画出关系线（EDGES），带 `with` 的会同时画出另一条 |
| `icon ID` | 画出涂鸦/道具/布景（ICON_POS） |
| `note ID` | 便签、标题、图例、批注（NOTES） |
| `group ID` | 分组框（GROUPS） |
| `ev ID` | 时间线事件卡（EVENTS），第一次出现时自动画出时间轴 |
| `dead ID` | 卡片：变灰 + 十字；人物：倒下 + × 眼 |
| `hl ID` | 红笔圈出来，4 秒后消失。卡片、线、涂鸦、便签、事件、人物、区域都能圈 |
| `fade ID` / `fade ID up` | 淡出 / 一边上飘一边淡出（如“升天”） |
| `swap A B` | 两张卡片加上对方颜色的虚线内框（“名字和性格对调”） |
| `move|walk|run|jump 道具ID x [y]` | 让涂鸦移动到世界坐标 (x, y)，jump 带弧线（兔子跑过来、球飞出去） |
| `fade` 之后不能再 `note/icon/card` 同一个 ID 让它重新出现 | 需要重复出现的东西，在 world.js 里复制一份换个 ID |

## 地图与行军路线（MAPS / ROUTES，字段见 world.md）
| 动作 | 说明 |
|---|---|
| `map ID` | 画出地图：海、海岸晕线、陆地、河流、山脉、画框、标题、指北针，从中间晕开 |
| `era 地图ID 时期ID` | 版图换成这个时期：新版图从时期的 center 向外晕开、带排线，旧版图淡出；左下角时期牌换字。`era 地图ID none` 清空 |
| `route ID` | 画一条行军/迁徙路线（虚线 + 箭头），`token` 涂鸦（大象、战船）跟着箭头走；`fade ID` 收起 |
| 地图上的城市 / 战役 / 地名 | 就是普通的 `icon`（city、battle 涂鸦）和 `note`（plain），坐标用 world.js 的 `geo(经度, 纬度)` 换算 |

## 动画人物（ACTORS）
| 动作 | 说明 |
|---|---|
| `actor ID` | 在家位置弹出来（带 Q 弹缩放） |
| `actor ID walkin` | 从背后方向（面朝右就从左边）走进来 |
| `pose ID 姿势` | 换姿势，0.5 秒带回弹的过渡。姿势表见 characters.md |
| `expr ID 表情` | 换表情 |
| `face ID left|right` | 转身 |
| `walk ID x [y]` / `run` / `jump` / `float` | 走/跑/跳/飘到 x（y 省略则不变），自动走路动画、自动转向、自动脚步声 |
| `place ID x [y]` | 瞬间换位置（换场景时把人物挪到另一块舞台） |
| `say ID 文字` | 对话气泡（每行 9 字自动换行，`\n` 手动换行），显示到下一句气泡或约 1.6+0.22×字数 秒 |
| `think ID 文字` | 心里话（云朵气泡） |
| `emote ID 符号` | `heart` `!` `?` `sweat` `anger` `zzz` `music` `sparkle` `shock`，或任意 1–2 个字（如 `emote X 哈`、`emote X 18`） |
| `talk ID` | 跟着本句配音对口型，到本句结束自动停；`talk ID off` 提前停 |
| `hold ID 道具` / `hold ID none` | 手里拿着一个涂鸦（涂鸦库里的任何名字，如 rabbit、book、sword、letter、phone） |
| `shake ID` | 发抖 1.6 秒（害怕、冷、气得发抖） |
| `leave ID` / `leave ID fade` / `leave ID up` | 朝面向走出画面 / 淡出 / 飘上天 |
| `dead ID` | 倒地，× 眼 |

## 镜头与氛围
| 动作 | 说明 |
|---|---|
| `cam A B …` | 1.7 秒平滑移到能框住这些元素的位置（ID、人物、AREAS 里的区域名都行，人物按 1.2 秒后的位置算） |
| `camz 1.3 A B …` | 同上但指定缩放（大于 1 = 推近特写） |
| `hud 文字` | 左上角章节标签（翻页音效） |
| `mood 名字` | 配乐换情绪：`calm` `warm` `happy` `tense` `sad` `dark` `epic` `silent`（2 秒渐变） |
| `grade 名字` | 整个画面换色调（2.4 秒渐变）：`warm` `gold` `dusk` `cold` `ash` `sepia` `night` `blood` `none`，按章节情绪用，见 themes.md |
| `rain on` / `rain off` | 满屏下雨 + 雨声 |
| `fx 音效` | 只播音效：`gun` `guns`(齐射) `stab` `thud` `train` `bell` `pop` `whoosh` `flip` `chime` `boing` `blip` `step` `swish`，`clash`(刀剑相击) `horn`(号角) `drums`(战鼓) `fire`(大火) `crowd`(人群欢呼) `cannon`(大炮)；`fx wind_start` 起风声 |
| `wind` | 飓风：画布上的所有东西和人物被吹走（`WIND_KEEP` 里的除外） |
| `restore` | 被吹走的东西飞回来 |

## 自动音效（不用写）
卡片/事件卡=铅笔声，连线=划线声，涂鸦=啵+铅笔，圈重点=马克笔，镜头移动=呼呼声，hud=翻页，去世=低沉钟声，
人物入场=啵，走/跑=脚步，跳=boing，气泡=滴，心/闪光=风铃，换姿势=衣服摆动。

## 例子
```python
L("有一天，一只兔子飞奔过来，一头撞在田边的树桩上。",
  "^ > icon stump", "兔子 > icon rabbit; run rabbit 400 -48",
  "撞在 > fx thud; emote FARMER !; pose FARMER stand; expr FARMER surprised")
L("", "@0 > note n_days; pose FARMER sleep; expr FARMER sleepy; emote FARMER zzz", pause=2.6)
L("邻居大婶路过，笑着说：“你这样傻等，兔子会自己送上门吗？”",
  "邻居 > actor AUNT walkin; cam AUNT FARMER", "笑着 > pose AUNT point; expr AUNT laugh", "你这样 > talk AUNT")
```
