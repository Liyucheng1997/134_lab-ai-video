# 星星点灯

把一个 YouTube 视频（任意语言，包含中文）一键转成 **中文配音 + 中文硬字幕** 的 MP4。
中文源音频会自动走“中文 → 英文语义稿 → 全新中文口播稿”的深度洗稿路线。

## 点灯线

```
YouTube URL
  │
  ├─1. 下载         yt-dlp              → source.mp4 + source.wav(16k)
  ├─2. 转写         faster-whisper(GPU) → segments.json（带时间戳）
  ├─3. 翻译         DeepSeek API / Claude Code → 英文合成全文 → 整体中译并去广告 → 中文重分句/预估计时
  ├─4. 配音         F5-TTS(本地音色克隆) → dub.wav + dub_segments.json（连续分段配音并重新计时）
  ├─5. 字幕         自建 ASS/SRT        → subs.ass / subs.srt
  └─6. 归档         ffmpeg              → output/<编号>_<主题>_<日期>/（成片、封面、标题、简介）
```

中间产物都在 `work/<视频id>/`，**支持断点续跑**：删掉某一步的文件即可强制重算那一步。

## 环境

- **项目内环境 `tools/f5-tts-env`**：Python 3.12 + PyTorch 2.12(cu130) + F5-TTS + faster-whisper，已适配 RTX 50 系列。
- **ffmpeg**：项目优先使用 `tools/ffmpeg-nvenc-compatible/`（含 libass + NVENC）。这个版本兼容当前 NVIDIA 驱动；过新的 ffmpeg 会要求 NVENC API 13.1 / 610+ 驱动并导致硬件编码不可用。
- **yt-dlp**：优先使用本项目环境里的 `tools/f5-tts-env`（以 `python -m yt_dlp` 调用）。YouTube 反爬需要三件套：`.env` 里配置浏览器导出的 cookie（`YTDLP_COOKIE_FILE`）、本机 node/deno 作 JS 运行时、`tools/bgutil-pot/`（[bgutil-ytdlp-pot-provider](https://github.com/Brainicism/bgutil-ytdlp-pot-provider) 源码构建，配合 pip 装的同名插件提供 PO token，下载时自动拉起常驻服务）。高清流被 YouTube SABR 实验拦截时会自动降级 360p。
- **模型缓存**：F5-TTS / Whisper 权重缓存在项目内 `models/` 目录。

## 配置

```powershell
copy .env.example .env
# 编辑 .env，填入 DEEPSEEK_API_KEY
```

## 网页版（推荐）— 六步点灯线

```powershell
powershell -ExecutionPolicy Bypass -File .\web.ps1
```

浏览器打开 <http://127.0.0.1:8800>。界面是**分步点灯线**，每一步可单独配置、点灯、预览：

“星星队列”标题旁可切换两套默认创作配置：

| 配置 | 源语言 | 洗稿 | 配音 | 视频画面 | 封面 |
|------|--------|------|------|----------|------|
| 荣格 | 英语 | DeepSeek 深度顺稿 | 人工老龙凤，并发 4 | 原视频 | `pictures/01_荣格心理学.png` 生成横版和竖版 |
| 纳瓦尔 | 中文，第三步每句最多 30 字 | DeepSeek 中文深度洗稿，人物名 `Naval` 强制译为“纳瓦尔” | 大一嘉豪哥，并发 4 | `pictures/02_纳瓦尔.png` 生成 1080×1920 竖屏视频，顶部固定“关注我，和纳瓦尔一起成长”，字幕默认位于底部，每行固定最多 15 字、最多两行 | 同一图片生成竖版和横版；竖版顶部固定“纳瓦尔宝典” |

配置会随单步、一键点亮和星星队列一起提交。归档 metadata 会记录“荣格/纳瓦尔”分类，
“灯火档案”窗口可按这两个分类切换显示；没有分类字段的旧档案自动归为荣格。

| 步 | 配置项 | 预览 |
|----|--------|------|
| 1 视频下载 | 链接或上传；下载视频并提取音频 | 原视频播放 |
| 2 语音识别 | 模型(small/large-v3-turbo)、语言 | 逐句原文 |
| 3 翻译/洗稿 | **DeepSeek** / **Claude Code（本地订阅额度）** / **Google 免费**；两种 AI 引擎共用同一套洗稿流程，自动判断外语中译或中文深度洗稿，清除广告并按中文重分句。Claude Code 引擎调用本机已登录的 `claude` CLI，走订阅额度而非 API key，可用 `CLAUDE_CODE_MODEL`、`CLAUDE_CODE_EFFORT`、`CLAUDE_CODE_TIMEOUT`、`CLAUDE_CODE_CLI` 覆盖 | 原稿与中文稿对照 |
| 4 中文配音 | **F5-TTS**，音色、语速、并发数，可**试听** | 配音音轨试听 |
| 5 合成 | 可选原视频画面或上传一张图片；画面按当前配置生成横屏或 9:16 竖屏视频，并与配音、字幕合成；图片模式可添加第 6 步风格的画面文字，并调整字号、宽度及位置；支持 ass/srt/vtt、中英双语、硬字幕、配音二次变速 | 画面文字与字幕可视化 + 成片 + 下载 |
| 6 生成信息归档 | 信息模板(B站)、分区、版权类型 | 自动生成标题/简介/标签/分区，并把成片、封面和数据保存到 `output` 子文件夹 |

每步产物缓存在 `work/<id>/`，可单独重跑。每步以独立子进程执行（崩溃隔离 + CUDA 安全）。

### 输出归档

第 6 步不再自动上传。它只生成发布信息并归档到 `output/<编号>_<4-8个字中文主题>_<创建日期>/`，例如：

```text
output/01_心灵成长_20260629/
```

归档文件夹内包含 `video.mp4`、`cover.png`、`douyin_cover.png`（抖音竖版封面）、`title.txt`、`description.txt`、`tags.txt`、`publish_info.md` 和 `metadata.json`。

## 命令行点灯

```powershell
# 默认 F5 音色、small 模型、自动检测语言
powershell -ExecutionPolicy Bypass -File .\run.ps1 "https://www.youtube.com/watch?v=XXXX"

# 指定音色 / 更高质量 ASR / 源语言
powershell -ExecutionPolicy Bypass -File .\run.ps1 "URL" --voice 沉稳男声 --whisper large-v3-turbo --lang en
```

成品在 `output/<编号>_<主题>_<日期>/video.mp4`。

## 参数

| 参数 | 说明 |
|------|------|
| `--voice` | 配音音色：默认 F5-TTS 的 `人工老龙凤` |
| `--whisper` | ASR 模型：`small`（默认，离线）/ `large-v3-turbo`（更准） |
| `--lang` | 源语言代码，留空自动检测（en/ja/ko…） |
| `--out` | 自定义输出路径 |

配音只使用 F5-TTS，可在 `.env` 里覆盖：`TTS_ENGINE=f5`、`TTS_VOICE=人工老龙凤`、`TTS_SPEED=1.00`、`F5_TTS_PARALLEL=4`。DeepSeek 默认使用“深度顺稿”，不再逐条绑定英文时间轴，而是先生成完整中文稿、再由程序重新分句。荣格英语源采用两遍顺稿：第一遍按语义提纲重构全文，第二遍只看中文初稿，专门清除译腔、重复、病句和错指代；荣格沿用 `42` 字分段上限，横屏字幕每行最多 `24` 字，优先从能容纳前后两行的中文标点换行，默认最多两行。纳瓦尔竖屏配置为每段最多 `30` 字，超过后优先按中文标点自然切分，没有合适标点时均衡截断；第五步按每行 `15` 字严格换行，同样最多两行。全局回退值可通过 `TRANSLATE_MAX_ZH_SEGMENT_CHARS` 覆盖，设为 `0` 可关闭。中文源会先转换成不含中文原句的英文语义中间稿，再仅依据英文稿重新创作中文；最终稿仍会检查全文相似度、连续片段照搬率和最长复制片段。英文中间稿保存在 `translation.bridge.json`，回译失败后重跑会直接复用，不重复产生第一步调用。长视频可用 `DEEPSEEK_MAX_OUTPUT_TOKENS` 调整全文输出上限。F5 推理阶段保持原速，最终音轨后期应用 `TTS_SPEED`，避免推理阶段改速影响音色；原速中间音频不会保留。

### F5-TTS 音色

字幕段并发默认 `2`，可在界面第 4 步选择 1-4 条；也可用 `TTS_REF_AUDIO/TEXT` 克隆指定声音。本项目当前固定只维护 F5-TTS，不再保留其它 TTS 引擎。

## 对齐说明

第 3 步会按完整中文稿的句子长度生成连续的预估时间，仅用于翻译预览和段间停顿。第 4 步按每段真实中文音频重新计时并写出 `dub_segments.json`；第 5 步字幕优先使用这份精确时间，因此成片里的中文配音和中文字幕会对齐。
