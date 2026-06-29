# 123 · AI 视频自动化

把一个 YouTube 视频（任意语言）一键转成 **中文配音 + 中文硬字幕** 的 MP4。

## 流水线

```
YouTube URL
  │
  ├─1. 下载         yt-dlp              → source.mp4 + source.wav(16k)
  ├─2. 转写         faster-whisper(GPU) → segments.json（带时间戳）
  ├─3. 翻译         DeepSeek API        → translated.json（整段上下文翻译，按段回写）
  ├─4. 配音         Qwen3-TTS(GPU, ryan) → dub.wav + dub_segments.json（连续分块配音并重新计时）
  ├─5. 字幕         自建 ASS/SRT        → subs.ass / subs.srt
  └─6. 归档         ffmpeg              → output/<编号>_<主题>_<日期>/（成片、封面、标题、简介）
```

中间产物都在 `work/<视频id>/`，**支持断点续跑**：删掉某一步的文件即可强制重算那一步。

## 环境

- **项目内环境 `tools/qwen3-tts-env`**：Python 3.12 + PyTorch 2.12(cu130) + Qwen3-TTS + faster-whisper，已适配 RTX 50 系列。
- **ffmpeg**：项目优先使用 `tools/ffmpeg-nvenc-compatible/`（含 libass + NVENC）。这个版本兼容当前 NVIDIA 驱动；过新的 ffmpeg 会要求 NVENC API 13.1 / 610+ 驱动并导致硬件编码不可用。
- **yt-dlp**：优先使用本项目环境里的 `tools/qwen3-tts-env/Scripts/yt-dlp.exe`。
- **模型缓存**：Qwen3-TTS 权重在 `models/qwen3-tts/`，Whisper 权重在 `models/huggingface/`。

## 配置

```powershell
copy .env.example .env
# 编辑 .env，填入 DEEPSEEK_API_KEY
```

## 网页版（推荐）— 六步流水线

```powershell
powershell -ExecutionPolicy Bypass -File .\web.ps1
```

浏览器打开 <http://127.0.0.1:8800>。界面是**分步流水线**，每一步可单独配置、运行、预览：

| 步 | 配置项 | 预览 |
|----|--------|------|
| 1 下载/导入 | 链接或上传；视频 / 仅音频 | 原视频播放 |
| 2 语音识别 | 模型(small/large-v3-turbo)、语言 | 逐句原文 |
| 3 翻译 | **DeepSeek** / **Google 免费** | 中英对照 |
| 4 中文配音 | **可选引擎**：Qwen3-TTS / F5-TTS / Azure / CosyVoice2，音色或风格、语速，可**试听** | 配音音轨试听 |
| 5 合成 | **原视频** 或 **图片(纯色+标题)**；字幕格式 ass/srt/vtt；中英双语；硬字幕 | 字幕可视化 + 成片 + 下载 |
| 6 生成信息归档 | 信息模板(B站)、分区、版权类型 | 自动生成标题/简介/标签/分区，并把成片、封面和数据保存到 `output` 子文件夹 |

每步产物缓存在 `work/<id>/`，可单独重跑。每步以独立子进程执行（崩溃隔离 + CUDA 安全）。

### 输出归档

第 6 步不再自动上传。它只生成发布信息并归档到 `output/<编号>_<4-8个字中文主题>_<创建日期>/`，例如：

```text
output/01_心灵成长_20260629/
```

归档文件夹内包含 `video.mp4`、`cover.png`、`title.txt`、`description.txt`、`tags.txt`、`publish_info.md` 和 `metadata.json`。

## 命令行运行

```powershell
# 默认 ryan 音色、small 模型、自动检测语言
powershell -ExecutionPolicy Bypass -File .\run.ps1 "https://www.youtube.com/watch?v=XXXX"

# 指定音色 / 更高质量 ASR / 源语言
powershell -ExecutionPolicy Bypass -File .\run.ps1 "URL" --voice 沉稳男声 --whisper large-v3-turbo --lang en
```

成品在 `output/<编号>_<主题>_<日期>/video.mp4`。

## 参数

| 参数 | 说明 |
|------|------|
| `--voice` | Qwen3-TTS 音色：默认 `ryan`，也可用 `aiden` / `dylan` / `eric` / `serena` 等 |
| `--whisper` | ASR 模型：`small`（默认，离线）/ `large-v3-turbo`（更准） |
| `--lang` | 源语言代码，留空自动检测（en/ja/ko…） |
| `--out` | 自定义输出路径 |

Qwen3-TTS 配置可在 `.env` 里覆盖：`TTS_VOICE=ryan`、`TTS_SPEED=1.15`、`QWEN_TTS_MODEL=Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice`。

### 配音引擎（可在界面第 4 步切换）

| 引擎 | 类型 | 自然度 | 说明 / 启用方式 |
|---|---|---|---|
| **Qwen3-TTS** | 本地 | ★★☆ | 默认，开箱即用，稳定 |
| **F5-TTS** | 本地 | ★★☆ | 音色克隆，需 `pip install f5-tts`；可用 `TTS_REF_AUDIO/TEXT` 克隆指定声音 |
| **Azure TTS** | 云端 | ★★★★ | 最稳、性价比高；`.env` 填 `AZURE_SPEECH_KEY`、`AZURE_SPEECH_REGION`，可设 `AZURE_TTS_STYLE=calm` |
| **Gemini TTS** | 云端 | ★★★★ | 自然语言控情感；`.env` 填 `GEMINI_API_KEY`（aistudio.google.com/apikey），可设 `GEMINI_TTS_STYLE` |
| **CosyVoice2** | 本地 | ★★★★ | 阿里开源，情感指令；`git clone CosyVoice` 并下载 `CosyVoice2-0.5B`，设置 `COSYVOICE_REPO_DIR/MODEL_DIR` |

界面会自动检测每个引擎是否就绪：未安装/未配置的引擎在下拉框中置灰并给出提示。各引擎参数见 `.env.example`。
> 想进一步提升「像中国人」的自然度，可考虑接火山引擎(豆包)或 MiniMax 云端语音——本项目已做成可插拔，新增引擎只需在 `src/` 加一个 `tts_xxx.py` 并在 `src/tts.py` 的 `_ENGINES` 注册。

## 对齐说明

中文译文常比原文长。当前流程会按翻译后的中文逐段生成配音，再把每段中文音频顺序拼接并写出 `dub_segments.json`，第 5 步字幕优先使用这个重新计时后的文件，因此成片里的中文配音和中文字幕会对齐。
