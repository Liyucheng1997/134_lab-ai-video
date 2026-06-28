# 123 · AI 视频自动化

把一个 YouTube 视频（任意语言）一键转成 **中文配音 + 中文硬字幕** 的 MP4。

## 流水线

```
YouTube URL
  │
  ├─1. 下载         yt-dlp              → source.mp4 + source.wav(16k)
  ├─2. 转写         faster-whisper(GPU) → segments.json（带时间戳）
  ├─3. 翻译         DeepSeek API        → translated.json（整段上下文翻译，按段回写）
  ├─4. 配音         F5-TTS(GPU)         → dub.wav + dub_segments.json（顺序配音并重新计时）
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
| 4 中文配音 | 音色（可**试听**）、段间停顿 | 配音音轨试听 |
| 5 合成 | **原视频** 或 **图片(纯色+标题)**；字幕格式 ass/srt/vtt；中英双语；硬字幕 | 字幕可视化 + 成片 + 下载 |
| 6 准备发布 | 平台(B站)；只生成信息 / 自动投稿 | 自动生成标题/简介/标签/分区；可上传视频、封面并提交 |

每步产物缓存在 `work/<id>/`，可单独重跑。每步以独立子进程执行（崩溃隔离 + CUDA 安全）。

### B 站自动投稿

在网页第 6 步点击「扫码登录 B站」，用 B 站 App 扫码确认即可。登录态会保存在 `work/bilibili.cookie.json`，不在项目里保存账号密码。

也可以用命令行备用方式扫码：

```powershell
& "F:\我的编程项目\114_听书软件\.venv\Scripts\python.exe" .\bili_login.py
```

扫码成功后会生成 `work/bilibili.cookie.json`。之后第 6 步选择「自动投稿到 B站」即可自动上传成片、封面、标题、简介、标签和分区。

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

中文译文常比原文长。当前流程会按翻译后的中文逐段生成配音，再把每段中文音频顺序拼接并写出 `dub_segments.json`，第 5 步字幕优先使用这个重新计时后的文件，因此成片里的中文配音和中文字幕会对齐。
