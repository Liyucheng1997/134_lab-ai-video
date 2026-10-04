# 动画人物

引擎在 `web/characters.js`：Q 版“橡皮管”人物（大头、圆手、粗描边），站立高约 300（`s: 1`），脚底在 y。
每 1/8 秒重新抖一次线条（手绘动画的“呼吸感”），自带呼吸、微摆、每人不同节奏的眨眼。

## 定义（web/world.js 的 ACTORS）
```js
FARMER: {x: -330, y: 0, face: 1, s: 1.35, look: {hair: "short", hat: "straw", top: "#a0784f", bottom: "#5a5048",
         sleeve: "short", beard: "mustache", belt: "#6b4a2e"}},
```
- `x, y`：家位置（y 是脚底，通常 0 = 地面）；`face`：1 朝右 / -1 朝左；`s`：缩放，舞台上一般 1.2–1.5。
- `look` 可选项：

| 键 | 取值 |
|---|---|
| `skin` | 肤色，默认 `#f4d6bc` |
| `hair` | `short` `long` `bun` `braid` `curly` `spiky` `bald` `wild` `bob`(齐刘海波波头) |
| `hairColor` | 默认深棕 |
| `hat` / `hatColor` | `military`(军帽) `sombrero` `crown` `cap` `straw`(草帽) `veil`(修女头巾) `bow`(蝴蝶结) `flower` `guan`(古人束发小冠+簪，`pinColor`) `mian`(帝王的冕，带旒) `laurel`(桂冠，hatColor 金色就是金桂冠) `galea`(罗马军团头盔，`crest` 盔缨色) `diadem`(晚期罗马/拜占庭的珍珠头箍) `uraeus`(埃及金带+圣蛇) |
| `top` / `bottom` / `shoes` | 上衣 / 裤子 / 鞋颜色 |
| `dress` / `dressColor` | `true` 穿裙子；再加 `robe: true` 是垂到脚面的古人长袍 |
| `sleeve` | `"short"` 短袖（前臂露肤色） |
| `beard` / `beardColor` | `mustache` `full` `goatee` |
| `age` | `child`（小一号身体） `adult` `old`（驼背，默认拄拐，`cane: false` 取消） |
| 古装 / 军装 | `toga`(从前肩斜搭到后腰的托加布色) `togaBorder`(托加紫边/金边) `cape`(披风色，走动时往后飘) `armor`(护甲色，自动画环片甲横条) `tunic`(短袍下摆色) `pteruges`(皮条裙色) `shield`(大方盾颜色，挂在后侧手) `shieldTrim` `fur`(毛皮领子色，蛮族) `eyepatch`(眼罩) |
| 配饰 | `glasses` `medal` `tattoo`(手臂刺青) `apron`(围裙色) `belt`(腰带色) `buttons` `blush` `cane` `propScale`(手持道具大小，默认 .55) |

同一个人不同年龄：建两个 actor（如 `COL_YOUNG` 和 `COL_OLD`），用 `leave … fade` / `actor …` 交接。
同一个 actor 可以多次登场 / 离场：`leave X fade` → `place X 新x` → `actor X`（或 `actor X walkin`，从背后走回当前位置）。换场景时先离场再挪，别在镜头里瞬移。

## 姿势（`pose ID 名字`）
| 类别 | 姿势 |
|---|---|
| 站立/讲解 | `stand` `wave`(挥手，自动摆动) `point`(指) `present`(摊手讲解) `think`(托下巴) `shrug`(耸肩) `hold`(托着东西) `read`(捧书) `write`(写字，手在动) `work`(干活，锄地/扫地动作循环) |
| 情绪 | `cheer`(双手举起，会跳) `hug` `cry`(捂脸) `angry`(握拳) `scared`(护头后仰，会抖) `facepalm` `dance`(会扭) |
| 身体 | `sit`(坐，屁股在 y 上方约 55×s，可配 stump/chair 涂鸦) `kneel` `pray` `bow`(鞠躬) |
| 戏剧 | `hands_up`(投降/行刑队前) `aim`(举枪，配 `hold X rifle`) `punch` `tied`(双手反绑，绑在树上) `float`(飘起) |
| 躺 | `lie` `sleep` `dead`（整个人横躺在地上） |
| 打仗 / 古代 | `stab`(往前刺，配 dagger/sword) `raise`(把手里的东西高高举起：皇冠、鹰旗、剑) `salute`(举手致敬) `fight`(举剑 + 前伸盾牌) `defend`(举盾防守) `play`(弹琴，配 lyre) |

走/跑/跳/飘时会自动覆盖成对应的动作，到达后回到当前姿势。

## 表情（`expr ID 名字`）
`neutral` `happy` `laugh` `sad` `cry`(流泪) `angry`(皱眉+青筋) `surprised` `scared` `love`(脸红) `smug`(得意)
`sleepy` `dead`(× 眼) `determined` `thinking` `shy`(脸红)

## 表演指南（让人物“活”起来）
1. **说到谁，谁就动。** 每句台词至少给一个人物动作；连续两句同一姿势时，换表情或加个情绪符号。
2. **情绪三件套**：姿势 + 表情 + 符号一起给。高兴 = `pose cheer; expr laugh; emote sparkle`；
   惊讶 = `pose scared; expr surprised; emote !`；伤心 = `pose cry; expr cry`；生气 = `pose angry; expr angry; emote anger; shake`；
   恋爱 = `expr love; emote heart`；困惑 = `pose shrug; expr thinking; emote ?`。
3. **先反应，再说话**：事件发生的关键词上给反应（`emote !`），下一句再给动作。
4. **对话要面对面**：两人说话前 `face` 转向对方，距离约 300–450×s；说话的人 `talk`，听的人换表情点反应。
5. **走位讲关系**：靠近 = 亲近（hug、love），走开 = 疏远（leave），跪 = 求/臣服，背对 = 冷漠。
6. **气泡只放金句**，≤ 12 字；长的解释交给旁白。心里话用 `think`。
7. **道具讲故事**：`hold` 书、信、剑、金鱼、手机……比写一行字直观。道具画法见 world.md 的涂鸦库。
8. **给表演留时间**：动作密集的地方 `hold=0.8`，或插一个 `pause=2` 的无声段。
9. **旁白人物（HOST）**：讲知识点时，放一个讲解员在画布旁，`pose present` + `talk HOST`，指向卡片（`pose point`），能大幅减少“纯文字”的感觉。
10. **死亡与消失**：`dead`（倒下）比直接消失更有分量；升天/离开用 `leave X up` / `fade`。

## 检查造型
```bash
python "<skill>/scripts/character_sheet.py" --project . --actor FARMER   # → character_FARMER_poses.png / _exprs.png
python "<skill>/scripts/character_sheet.py" --out .                        # 全部姿势和表情的总表
```

## 扩展
- 新姿势：在 `characters.js` 的 `POSES` 里加一行（角度：0 = 下垂，正 = 朝面向方向抬；`fa`/`sh` 相对上一节）。
  手举过头会被大头挡住，举手类姿势让上臂在 125–140° 左右。
- 新发型/帽子：在 `drawHead` 里加分支；头心在 (0.72r, 0)（函数开头已平移），脸朝 +x。
- 新情绪符号：在 `drawOverlays` 的 emote 分支里加。

## 造型例子（罗马）
```js
const legion = x => ({x, y: 0, face: 1, s: 1.34, look: {hat: "galea", crest: "#a8322a", top: "#a8322a", armor: "#b9b2a4", tunic: "#a8322a",
  pteruges: "#7a5230", bottom: "#e2b48c", skin: "#e2b48c", shield: "#a8322a", sleeve: "short"}});
CAESAR: {look: {hair: "bald", hat: "laurel", top: "#f3ecdc", dress: true, robe: true, dressColor: "#f3ecdc", toga: "#e6dcc6", togaBorder: "#6a2c5c", cape: "#a8322a"}},
CLEO: {look: {hair: "bob", hairColor: "#141210", hat: "uraeus", skin: "#d9a982", dress: true, dressColor: "#f3ecdc", belt: "#d9a52b"}},
HANNIBAL: {look: {hair: "curly", beard: "full", eyepatch: true, armor: "#c49a4a", tunic: "#5a2a4a", pteruges: "#5a3a22", cape: "#6a2c5c", sleeve: "short"}},
```
人物的 `s` 一般 1.35–1.5：舞台区域高约 760 时，人物能占画面高度的 35–40%。
