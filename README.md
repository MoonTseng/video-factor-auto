# 🍜 日本美食/旅行 · 二创内容管线

一键将日本 YouTube 美食/旅行视频转化为中文二创内容 —— 适用于 B站搬运和二次创作。

## 工作流

```
YouTube 视频 → Whisper 转录日语 → Claude 生成中文解说 → TTS 配音 → FFmpeg 合成视频
```

**七步流程：**

1. 🔍 **发现** — YouTube API 搜索小众日本美食/旅行视频
2. 📌 **选题** — Claude AI 自动选择最适合二创的视频
3. 📥 **下载** — yt-dlp 下载源视频
4. 🎙️ **转录** — Whisper 提取日语原音字幕
5. ✍️ **文案** — Claude Opus 生成口语化中文解说脚本
6. 🔊 **配音** — CosyVoice2 / Edge-TTS 生成中文配音
7. 🎬 **合成** — FFmpeg 烧字幕 + 替换/保留音轨 + 防版权处理

## 三种音频模式

| 模式 | 说明 | 适用场景 |
|------|------|----------|
| `original` | 保留日语原声 + 中文字幕 | 保留原片氛围感 |
| `dubbed` | 去原声 + 中文配音 | 完全中文化 |
| `mixed` | 原声降至 15% + 中文配音叠加 | 兼顾原声和解说 |

## 快速开始

### 1. 环境准备

```bash
# macOS / Linux
python3 -m venv .venv
source .venv/bin/activate

# 安装依赖（中国镜像加速）
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 系统依赖
brew install ffmpeg    # macOS
# sudo apt install ffmpeg  # Linux
```

### 2. 配置

```bash
cp config.template.yaml config.yaml
# 编辑 config.yaml，填写：
#   - LLM API 配置（Bedrock 代理 or Anthropic API key）
#   - YouTube 代理（访问 YouTube 必须）
#   - 音频模式选择（original / dubbed / mixed）
```

### 3. 运行

```bash
# 自动发现视频并生成
python main.py

# 指定 YouTube 视频 URL
python main.py --url "https://www.youtube.com/watch?v=XXXXX"

# 使用自定义配置
python main.py --config my_config.yaml
```

### 输出结构

```
runs/
└── 20260413_132421/           # 本次运行
    ├── source/                # 源视频 + 音频
    ├── script/
    │   ├── transcript_ja.json # 日语转录
    │   ├── script.json        # 二创脚本（程序用）
    │   └── script.txt         # 二创脚本（可读版）
    ├── audio/                 # TTS 配音分段
    │   ├── tts_000.mp3
    │   └── full_narration.mp3
    ├── output/
    │   ├── 20260413_xxx.mp4   # 最终视频
    │   └── publish_info.json  # 标题/标签/简介
    └── run_info.json          # 运行元数据
```

## 项目结构

```
japan-content-pipeline/
├── main.py                    # 主流程入口
├── config.template.yaml       # 配置模板
├── requirements.txt           # Python 依赖
├── scraper/                   # YouTube 搜索 + 下载
│   └── __init__.py
├── writer/                    # Whisper 转录 + Claude 文案
│   └── __init__.py
├── audio/                     # TTS 配音（CosyVoice2 / Edge-TTS）
│   └── __init__.py
└── video/                     # FFmpeg 视频合成
    └── __init__.py
```

## 依赖说明

| 组件 | 用途 | 备注 |
|------|------|------|
| yt-dlp | YouTube 下载 | 需要代理 |
| faster-whisper | 日语语音转录 | CPU 即可，base 模型够用 |
| anthropic / httpx | Claude LLM 调用 | 支持 Bedrock 代理 |
| edge-tts | TTS 配音（兜底） | 免费，质量一般 |
| CosyVoice2 | TTS 配音（首选） | 需单独安装，效果更好 |
| ffmpeg | 视频处理 | 系统安装 |

## 防版权处理

视频合成时自动进行轻微的防版权处理：
- 裁切边缘 3%（去水印）
- 微调亮度 +1%
- 可选水平翻转
- 可选添加自定义水印

这些参数在 `config.yaml` 的 `video.anti_copyright` 中配置。

## 字幕风格

参考 B站搬运 UP 主（多思味等）的风格：
- 小字号（18pt），不抢画面
- 白色文字 + 黑色描边
- 底部居中

## License

MIT
