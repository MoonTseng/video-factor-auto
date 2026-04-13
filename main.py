#!/usr/bin/env python3
"""
日本美食/旅行内容二创管线 — 主流程
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
工作流:
  1. 发现候选视频（YouTube API / 内置话题库）
  2. 选择最佳话题
  3. 下载源视频 (yt-dlp)
  4. Whisper 转录日语原音
  5. Claude 生成中文二创解说脚本
  6. TTS 配音（CosyVoice2 / Edge-TTS）
  7. 合成最终视频（源画面 + 新配音 + 中文字幕）
"""

import argparse
import json
import logging
import os
import sys
from datetime import datetime

import yaml

# ── 日志配置 ──────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s │ %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def load_config(path: str = "config.yaml") -> dict:
    """加载配置"""
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def create_run_dir(config: dict) -> str:
    """创建本次运行的输出目录"""
    runs_dir = config.get("output", {}).get("runs_dir", "runs")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = os.path.join(runs_dir, timestamp)
    os.makedirs(run_dir, exist_ok=True)
    return run_dir


# ══════════════════════════════════════════════════════════
#  主流程
# ══════════════════════════════════════════════════════════

def run_pipeline(config: dict, video_url: str = None):
    """
    执行完整的二创管线。
    如果提供 video_url，直接跳过发现步骤使用该 URL。
    """
    print()
    print("╔══════════════════════════════════════════════════╗")
    print("║   🍜 日本美食/旅行 · 二创内容管线               ║")
    print("╚══════════════════════════════════════════════════╝")
    print()

    run_dir = create_run_dir(config)
    logger.info(f"📁 运行目录: {run_dir}")

    # ── Step 1: 发现候选视频 ─────────────────────────────
    from scraper import fetch_trending_topics, download_video, format_topic_summary

    if video_url:
        # 直接使用指定 URL
        logger.info(f"🎯 使用指定视频: {video_url}")
        topic = {
            "video_id": _extract_video_id(video_url),
            "title": "用户指定视频",
            "description": "",
            "channel": "",
            "url": video_url,
            "view_count": 0,
        }
    else:
        logger.info("🔍 Step 1/7: 发现候选视频...")
        topics = fetch_trending_topics(config)
        if not topics:
            logger.error("❌ 没有找到候选视频")
            return

        print(format_topic_summary(topics[:5]))

        # 选择话题
        from writer import select_topic
        topic = select_topic(config, topics)

    logger.info(f"📌 选中: {topic['title']}")

    # ── Step 2: 下载源视频 ───────────────────────────────
    if not topic.get("url"):
        logger.error("❌ 选中的视频没有 URL，请在内置话题中填入实际 YouTube URL")
        # 保存话题信息供用户参考
        _save_json(topic, os.path.join(run_dir, "selected_topic.json"))
        return

    logger.info("📥 Step 2/7: 下载源视频...")
    source_dir = os.path.join(run_dir, "source")
    source = download_video(config, topic, source_dir)
    logger.info(f"   视频: {source['video_path']} ({source['duration']:.0f}s)")

    # ── Step 3: Whisper 转录 ─────────────────────────────
    from writer import transcribe_source, generate_script, save_script, save_transcript

    logger.info("🎙️ Step 3/7: Whisper 转录日语原音...")
    transcript = transcribe_source(config, source["audio_path"])
    save_transcript(transcript, run_dir)

    if not transcript:
        logger.warning("⚠️ 转录为空，可能是纯音乐/无对话视频，将基于视频信息生成文案")
        transcript = [{
            "start": 0, "end": source["duration"],
            "text": f"[视频无对话内容] {topic['title']} - {topic.get('description', '')}"
        }]

    # ── Step 4: 生成二创解说脚本 ─────────────────────────
    logger.info("✍️ Step 4/7: Claude 生成二创解说脚本...")
    script = generate_script(config, topic, transcript)
    save_script(script, run_dir)

    print(f"\n   📝 标题: {script['title']}")
    print(f"   📝 分段: {len(script['segments'])} 段")
    print(f"   📝 总字数: {sum(len(s['text']) for s in script['segments'])}\n")

    # ── Step 5: TTS 配音 ─────────────────────────────────
    from audio import generate_audio_segments, concat_audio_segments, generate_srt

    logger.info("🔊 Step 5/7: TTS 生成配音...")
    audio_dir = os.path.join(run_dir, "audio")
    audio_segments = generate_audio_segments(config, script["segments"], audio_dir)

    if not audio_segments:
        logger.error("❌ TTS 配音全部失败")
        return

    # ── Step 6: 拼接音频 + 生成字幕 ─────────────────────
    logger.info("🎵 Step 6/7: 拼接音频 + 生成字幕...")
    full_audio_path = os.path.join(audio_dir, "full_narration.mp3")
    concat_audio_segments(audio_segments, full_audio_path, source["duration"])

    srt_path = os.path.join(run_dir, "subtitles.srt")
    generate_srt(audio_segments, srt_path)

    # ── Step 7: 合成最终视频 ─────────────────────────────
    from video import compose_video, compose_video_simple

    logger.info("🎬 Step 7/7: 合成最终视频...")
    output_dir = os.path.join(run_dir, "output")
    os.makedirs(output_dir, exist_ok=True)
    safe_title = script['title'][:20].replace(' ', '_').replace('/', '_')
    output_path = os.path.join(output_dir, f"{datetime.now().strftime('%Y%m%d')}_{safe_title}.mp4")

    audio_mode = config.get("video", {}).get("audio_mode", "dubbed")

    try:
        if audio_mode == "original":
            # 原声模式：不需要配音音轨
            compose_video(config, source["video_path"], output_path, srt_path=srt_path)
        else:
            # dubbed / mixed 模式：需要配音音轨
            compose_video(config, source["video_path"], output_path,
                         audio_track_path=full_audio_path, srt_path=srt_path)
    except Exception as e:
        logger.warning(f"⚠️ 带字幕合成失败: {e}")
        logger.info("尝试不带字幕的简化合成...")
        output_path = output_path.replace(".mp4", "_nosub.mp4")
        compose_video_simple(config, source["video_path"], full_audio_path, output_path)

    # ── 完成 ─────────────────────────────────────────────
    print()
    print("╔══════════════════════════════════════════════════╗")
    print("║   ✅ 二创视频生成完成!                           ║")
    print("╚══════════════════════════════════════════════════╝")
    print()
    print(f"   📁 输出目录: {run_dir}")
    print(f"   🎬 最终视频: {output_path}")
    print(f"   📝 解说脚本: {os.path.join(run_dir, 'script', 'script.txt')}")
    print(f"   📄 SRT 字幕: {srt_path}")
    print()

    # 保存运行信息和发布参考
    run_info = {
        "timestamp": datetime.now().isoformat(),
        "topic": topic,
        "source_video": source["video_path"],
        "duration": source["duration"],
        "script_title": script["title"],
        "segments_count": len(script["segments"]),
        "audio_segments": len(audio_segments),
        "audio_mode": audio_mode,
        "output_video": output_path,
    }
    _save_json(run_info, os.path.join(run_dir, "run_info.json"))

    publish_info = {
        "title": script["title"],
        "tags": script.get("tags", []),
        "description": script.get("description", ""),
        "video_file": output_path,
    }
    _save_json(publish_info, os.path.join(run_dir, "output", "publish_info.json"))

    return output_path


# ══════════════════════════════════════════════════════════
#  工具函数
# ══════════════════════════════════════════════════════════

def _extract_video_id(url: str) -> str:
    """从 YouTube URL 提取 video ID"""
    import re
    patterns = [
        r'(?:v=|/v/|youtu\.be/)([a-zA-Z0-9_-]{11})',
        r'(?:embed/)([a-zA-Z0-9_-]{11})',
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return url[-11:]  # 兜底


def _save_json(data: dict, path: str):
    """保存 JSON 文件"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ══════════════════════════════════════════════════════════
#  CLI 入口
# ══════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="🍜 日本美食/旅行二创内容管线",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例:
  # 自动发现视频并生成
  python main.py

  # 指定 YouTube 视频 URL
  python main.py --url "https://www.youtube.com/watch?v=XXXXX"

  # 使用自定义配置
  python main.py --config my_config.yaml

  # 只执行特定步骤（调试用）
  python main.py --url "..." --step transcribe  # 只转录
  python main.py --url "..." --step script      # 只生成脚本
        """
    )
    parser.add_argument("--config", default="config.yaml", help="配置文件路径")
    parser.add_argument("--url", help="直接指定 YouTube 视频 URL")
    parser.add_argument("--step", choices=["discover", "transcribe", "script", "tts", "full"],
                        default="full", help="只执行到指定步骤")

    args = parser.parse_args()
    config = load_config(args.config)

    if args.step == "full":
        run_pipeline(config, video_url=args.url)
    elif args.step == "discover":
        from scraper import fetch_trending_topics, format_topic_summary
        topics = fetch_trending_topics(config)
        print(format_topic_summary(topics))
    else:
        # 其他步骤需要完整流程，暂时走 full
        run_pipeline(config, video_url=args.url)


if __name__ == "__main__":
    main()
