# 123 · AI 视频自动化

把一个 YouTube 视频（任意语言）一键转成 **中文配音 + 中文硬字幕** 的 MP4。

## 流水线

```
YouTube URL
  │
  ├─1. 下载         yt-dlp              → source.mp4 + source.wav(16k)
  ├─2. 转写         faster-whisper(GPU) → segments.json（带时间戳）
  ├─3. 翻译         DeepSeek API        → translated.json（逐段中文）
  ├─4. 配音         F5-TTS(GPU)         → dub.wav（按时间戳对齐替换原声）
  ├─5. 字幕         自建 ASS/SRT        → subs.ass / subs.srt
  └─6. 合成         ffmpeg              → output/<id>_zh.mp4（烧录硬字幕）
```

中间产物都在 `work/<视频id>/`，**支持断点续跑**：删掉某一步的文件即可强制重算那一步。

## 环境（复用，无需重装）

- **复用 `114_听书软件/.venv`**：Python 3.11 + PyTorch 2.11(cu128) + faster-whisper + F5-TTS，已适配 RTX 50 系列。
- **ffmpeg**：项目自带静态构建在 `tools/`（含 libass，用于烧字幕），导入时自动加入 PATH。
- **yt-dlp**：复用系统已安装的 `yt-dlp.exe`。
- **模型缓存**：`faster-whisper-small` 与 `F5-TTS` 权重已在本机 HuggingFace 缓存，离线可用。

## 配置

```powershell
copy .env.example .env
# 编辑 .env，填入 DEEPSEEK_API_KEY
```

## 网页版（推荐）

```powershell
powershell -ExecutionPolicy Bypass -File .\web.ps1
```

浏览器打开 <http://127.0.0.1:8800>：可**填 YouTube 链接**或**上传本地视频**，选音色/精度/语言，点「开始生成」，
页面会**实时显示六步进度 + 日志**，完成后直接在线预览并下载 MP4。单卡串行（同时只跑一个任务）。

## 命令行运行

```powershell
# 默认温柔女声、small 模型、自动检测语言
powershell -ExecutionPolicy Bypass -File .\run.ps1 "https://www.youtube.com/watch?v=XXXX"

# 指定音色 / 更高质量 ASR / 源语言
powershell -ExecutionPolicy Bypass -File .\run.ps1 "URL" --voice 沉稳男声 --whisper large-v3-turbo --lang en
```

成品在 `output/<id>_zh.mp4`。

## 参数

| 参数 | 说明 |
|------|------|
| `--voice` | 配音音色：沉稳男声 / 温柔女声 / 浑厚男声 |
| `--whisper` | ASR 模型：`small`（默认，离线）/ `large-v3-turbo`（更准） |
| `--lang` | 源语言代码，留空自动检测（en/ja/ko…） |
| `--out` | 自定义输出路径 |

声音克隆：在 `.env` 里设 `TTS_REF_AUDIO` 指向一段参考人声即可。

## 对齐说明

中文译文常比原文长。配音按每段 `start` 落位，可用时长内放不下时**变速加快**（保音高，上限 `TTS_MAX_SPEEDUP`），仍超出则容忍少量重叠；译文偏短则补静音。这样画面与配音基本同步。
