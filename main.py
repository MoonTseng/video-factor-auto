#!/usr/bin/env python3
"""
多渠道内容管线 — Netflix预告 / 日本美食 / 日本旅行
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
用法:
  python main.py netflix [url 或关键词]                    # 自动搜索 + 上传全部平台
  python main.py netflix -p bilibili                      # 只上传 B站
  python main.py netflix -p bilibili,douyin               # B站 + 抖音
  python main.py netflix --no-upload                      # 只生成视频不上传

支持平台: bilibili(B站), douyin(抖音)

工作流:
  1. YouTube 搜索/下载 (yt-dlp)
  2. Whisper 转录原声
  3. LLM 翻译字幕 / 生成二创文案
  4. 合成视频 (保留原声 + 中文硬字幕)
  5. 提取封面
  6. 多渠道发布 (用户选择平台)
  7. 清理本地文件
"""

import argparse
import json
import logging
import os
import re
import sys
from datetime import datetime

import yaml

from themes import get_theme, list_themes

# ── 历史记录（防重复） ──────────────────────────────────────
HISTORY_FILE = os.path.join(os.path.dirname(__file__), "history.json")


def _load_history() -> dict:
    """加载已处理视频的历史记录。格式: {video_id: {title, theme, timestamp, bvid, uploaded}}"""
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {}


def _save_history(history: dict):
    """保存历史记录"""
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


def _add_to_history(video_id: str, video_info: dict, theme_name: str,
                    bvid: str = None, uploaded: bool = False,
                    upload_results: dict = None):
    """追加一条视频到历史记录

    参数:
        upload_results: 各平台上传结果，格式: {"bilibili": {"bvid": "xxx"}, "douyin": {"success": True}, ...}
    """
    history = _load_history()
    history[video_id] = {
        "title": video_info.get("title", ""),
        "url": video_info.get("url", ""),
        "theme": theme_name,
        "timestamp": datetime.now().isoformat(),
        "bvid": bvid,
        "uploaded": uploaded,
        "platforms": upload_results or {},
    }
    _save_history(history)
    logger.info(f"   📝 已记录到历史: {video_id}")


def _get_history_video_ids() -> set:
    """获取所有已处理过的 video_id 集合"""
    return set(_load_history().keys())


# ── 日志 ──────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s │ %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════
#  配置 & 目录
# ══════════════════════════════════════════════════════════

def load_config(path: str = "config.yaml") -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def create_run_dir(config: dict, theme_name: str) -> str:
    runs_dir = config.get("output", {}).get("runs_dir", "runs")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = os.path.join(runs_dir, f"{theme_name}_{timestamp}")
    os.makedirs(run_dir, exist_ok=True)
    return run_dir


# ══════════════════════════════════════════════════════════
#  核心 Pipeline
# ══════════════════════════════════════════════════════════

def run_pipeline(config: dict, theme_name: str, target: str = None,
                 platforms: list[str] = None):
    """
    完整管线：搜索/下载 → 转录 → 翻译/文案 → 合成 → 封面 → 多渠道发布 → 清理

    参数:
        config: 配置字典
        theme_name: netflix / food / travel
        target: YouTube URL 或搜索关键词（None=用主题默认搜索）
        platforms: 发布平台列表，如 ["bilibili", "douyin"]
                   None 时只上传 B站（兼容旧行为）
    """
    # 默认只上传 B站
    if platforms is None:
        platforms = ["bilibili"]

    PLATFORM_NAMES = {
        "bilibili": "B站",
        "douyin": "抖音",
    }

    theme = get_theme(theme_name)

    print()
    print("╔═══════════════════════════════════════════════════════╗")
    print(f"║   🎬 多渠道内容管线 — {theme.name.upper():34s}║")
    print("╚═══════════════════════════════════════════════════════╝")
    platforms_str = " + ".join(PLATFORM_NAMES.get(p, p) for p in platforms)
    print(f"   📡 发布平台: {platforms_str}")
    print()

    run_dir = create_run_dir(config, theme_name)
    logger.info(f"📁 运行目录: {run_dir}")
    logger.info(f"🎨 主题: {theme.name} | 分区: {theme.tid} | 音频: {theme.audio_mode}")

    # ── 应用主题配置覆盖 ──
    config = _apply_theme_overrides(config, theme)

    # ── Step 1: 获取视频 ──────────────────────────────────
    video_url = None
    video_info = {}

    if target and _is_url(target):
        # 直接指定 URL
        video_url = target
        video_info = {"title": "用户指定视频", "url": target}
        logger.info(f"🎯 使用指定视频: {video_url}")
    elif target:
        # 关键词搜索
        logger.info(f"🔍 Step 1: 搜索 YouTube: {target}")
        video_url, video_info = _search_and_pick(config, theme, keyword=target)
    else:
        # 用主题默认搜索词
        logger.info("🔍 Step 1: 使用主题默认搜索...")
        video_url, video_info = _search_and_pick(config, theme)

    if not video_url:
        logger.error("❌ 没有找到可用视频")
        return None

    logger.info(f"📌 视频: {video_info.get('title', video_url)}")

    # ── Step 2: 下载 ──────────────────────────────────────
    logger.info("📥 Step 2: 下载视频...")
    from scraper import download_video
    source_dir = os.path.join(run_dir, "source")
    topic = {
        "url": video_url,
        "title": video_info.get("title", ""),
        "video_id": _extract_video_id(video_url),
    }
    source = download_video(config, topic, source_dir)
    logger.info(f"   ✅ {source['duration']:.0f}s, {os.path.getsize(source['video_path'])/1024/1024:.1f}MB")

    # ── Step 3: Whisper 转录 ──────────────────────────────
    logger.info("🎙️ Step 3: Whisper 转录...")
    from writer import transcribe_source, save_transcript
    transcript = transcribe_source(config, source["audio_path"])
    save_transcript(transcript, run_dir)

    if not transcript:
        logger.warning("⚠️ 转录为空（纯音乐/无对话）")
        transcript = [{"start": 0, "end": source["duration"],
                       "text": f"[无对话] {video_info.get('title', '')}"}]

    # ── Step 4: 翻译/文案 ─────────────────────────────────
    audio_mode = theme.audio_mode
    srt_path = os.path.join(run_dir, "subtitles.srt")
    full_audio_path = None

    if audio_mode == "subtitle_only":
        # 预告片模式：翻译字幕，不生成配音
        logger.info("🈶→🈳 Step 4: 翻译字幕 (subtitle_only 模式)...")
        from writer import translate_transcript_to_srt
        translate_transcript_to_srt(config, transcript, srt_path,
                                    video_title=video_info.get("title", ""),
                                    video_theme=theme.name)

    elif audio_mode in ("original", "mixed"):
        # 原声/混合模式：翻译字幕
        logger.info("🈶→🈳 Step 4: 翻译字幕...")
        from writer import translate_transcript_to_srt
        translate_transcript_to_srt(config, transcript, srt_path,
                                    video_title=video_info.get("title", ""),
                                    video_theme=theme.name)

        if audio_mode == "mixed":
            # mixed 还需要生成配音
            logger.info("🔊 Step 4b: 生成配音...")
            from writer import generate_script
            from audio import generate_audio_segments, concat_audio_segments
            script = generate_script(config, video_info, transcript)
            audio_dir = os.path.join(run_dir, "audio")
            audio_segs = generate_audio_segments(config, script["segments"], audio_dir)
            full_audio_path = os.path.join(audio_dir, "full_narration.mp3")
            concat_audio_segments(audio_segs, full_audio_path, source["duration"])

    else:
        # dubbed 模式：完全配音
        logger.info("✍️ Step 4: 生成文案 + 配音...")
        from writer import generate_script, save_script
        from audio import generate_audio_segments, concat_audio_segments, generate_srt
        script = generate_script(config, video_info, transcript)
        save_script(script, run_dir)
        audio_dir = os.path.join(run_dir, "audio")
        audio_segs = generate_audio_segments(config, script["segments"], audio_dir)
        full_audio_path = os.path.join(audio_dir, "full_narration.mp3")
        concat_audio_segments(audio_segs, full_audio_path, source["duration"])
        generate_srt(audio_segs, srt_path)

    # ── Step 5: 合成视频 ──────────────────────────────────
    logger.info("🎬 Step 5: 合成最终视频...")
    from video import compose_video

    output_dir = os.path.join(run_dir, "output")
    os.makedirs(output_dir, exist_ok=True)
    safe_title = re.sub(r'[^\w\u4e00-\u9fff]', '_', video_info.get("title", "video"))[:20]
    output_path = os.path.join(output_dir, f"{datetime.now().strftime('%Y%m%d')}_{safe_title}.mp4")

    srt_for_compose = srt_path if os.path.exists(srt_path) else None
    compose_video(config, source["video_path"], output_path,
                  audio_track_path=full_audio_path, srt_path=srt_for_compose)

    output_size = os.path.getsize(output_path) / 1024 / 1024
    logger.info(f"   ✅ 输出: {output_path} ({output_size:.1f}MB)")

    # ── Step 6: 提取封面 ──────────────────────────────────
    logger.info("🖼️ Step 6: 提取封面...")
    cover_path = os.path.join(output_dir, "cover.jpg")
    try:
        from cover import extract_cover
        strategy = theme.get_cover_strategy()
        extract_cover(source["video_path"], cover_path, strategy)
        logger.info(f"   ✅ 封面: {cover_path}")
    except Exception as e:
        logger.warning(f"⚠️ 封面提取失败: {e}, 使用默认帧...")
        _fallback_cover(source["video_path"], cover_path)

    # ── Step 7: 生成发布信息（按平台区分） ──────────────────
    logger.info("📋 Step 7: 生成发布信息...")

    # 把 transcript 摘要注入 video_info，供 generate_desc 使用
    if transcript:
        preview_lines = []
        for seg in transcript[:30]:  # 取前30段
            text = seg.get("text", "").strip()
            if text and text != f"[无对话] {video_info.get('title', '')}":
                preview_lines.append(text)
        video_info["transcript_preview"] = " ".join(preview_lines)[:800]

    publish_infos = {}
    for plat in platforms:
        publish_infos[plat] = _build_publish_info(theme, video_info, output_path, cover_path, platform=plat)
    # 保存B站版作为默认
    default_info = publish_infos.get("bilibili", list(publish_infos.values())[0])
    _save_json(default_info, os.path.join(output_dir, "publish_info.json"))
    # 如果有抖音版也保存
    if "douyin" in publish_infos:
        _save_json(publish_infos["douyin"], os.path.join(output_dir, "publish_info_douyin.json"))

    # ── Step 8: 多渠道发布 ──────────────────────────────────
    uploaded = False
    bvid = None
    upload_results = {}

    if config.get("_no_upload"):
        logger.info("⏭️ Step 8: 跳过上传 (--no-upload)")
    else:
        logger.info(f"📤 Step 8: 多渠道发布 ({', '.join(platforms)})...")

        for platform in platforms:
            plat_info = publish_infos.get(platform, default_info)
            try:
                if platform == "bilibili":
                    logger.info("   📺 上传 B站...")
                    b_bvid = _upload_bilibili(config, plat_info)
                    if b_bvid:
                        bvid = b_bvid
                        uploaded = True
                        upload_results["bilibili"] = {"success": True, "bvid": b_bvid}
                        logger.info(f"   ✅ B站上传成功! BV号: {b_bvid}")
                    else:
                        upload_results["bilibili"] = {"success": False, "message": "返回空BV号"}

                elif platform == "douyin":
                    logger.info("   🎵 上传抖音...")
                    result = _upload_douyin(config, plat_info)
                    upload_results["douyin"] = result
                    if result.get("success"):
                        uploaded = True
                        logger.info("   ✅ 抖音上传成功!")
                    else:
                        logger.warning(f"   ⚠️ 抖音上传: {result.get('message', '未知错误')}")

                else:
                    logger.warning(f"   ⚠️ 未知平台: {platform}")

            except Exception as e:
                logger.warning(f"   ⚠️ {platform} 上传失败: {e}")
                upload_results[platform] = {"success": False, "message": str(e)}

    # ── Step 9: 清理 ──────────────────────────────────────
    if uploaded:
        logger.info("🧹 Step 9: 清理本地文件...")
        _cleanup(run_dir, keep_publish_info=True)
    else:
        logger.info("⏭️ Step 9: 跳过清理（未上传成功）")

    # ── 完成 ──────────────────────────────────────────────
    print()
    print("╔═══════════════════════════════════════════════════════╗")
    print("║   ✅ 完成!                                           ║")
    print("╚═══════════════════════════════════════════════════════╝")
    print()
    print(f"   📁 目录: {run_dir}")
    print(f"   🎬 视频: {output_path}")
    if bvid:
        print(f"   📺 B站: https://www.bilibili.com/video/{bvid}")
    if upload_results.get("douyin", {}).get("success"):
        print(f"   🎵 抖音: 已发布（请到 creator.douyin.com 查看）")
    # 打印失败的平台
    for plat, res in upload_results.items():
        if not res.get("success"):
            print(f"   ❌ {plat}: {res.get('message', '失败')}")
    print()

    # 保存运行记录
    _save_json({
        "timestamp": datetime.now().isoformat(),
        "theme": theme_name,
        "source_url": video_url,
        "source_title": video_info.get("title", ""),
        "output_video": output_path,
        "bvid": bvid,
        "uploaded": uploaded,
        "platforms": platforms,
        "upload_results": upload_results,
        "publish_info": publish_infos,
    }, os.path.join(run_dir, "run_info.json"))

    # 追加到历史记录（防重复发布）
    vid = _extract_video_id(video_url)
    _add_to_history(vid, video_info, theme_name, bvid=bvid,
                    uploaded=uploaded, upload_results=upload_results)

    return output_path


# ══════════════════════════════════════════════════════════
#  辅助函数
# ══════════════════════════════════════════════════════════

def _apply_theme_overrides(config: dict, theme) -> dict:
    """用主题配置覆盖全局配置"""
    import copy
    config = copy.deepcopy(config)

    # 覆盖 B站分区 和 音频模式
    config.setdefault("bilibili", {})["tid"] = theme.tid
    config.setdefault("video", {})["audio_mode"] = theme.audio_mode

    # Whisper 语言覆盖
    whisper_override = theme.get_whisper_override()
    if whisper_override:
        config.setdefault("whisper", {}).update(whisper_override)

    return config


def _is_url(text: str) -> bool:
    return text.startswith("http://") or text.startswith("https://") or "youtu" in text


def _extract_video_id(url: str) -> str:
    patterns = [
        r'(?:v=|/v/|youtu\.be/)([a-zA-Z0-9_-]{11})',
        r'(?:embed/)([a-zA-Z0-9_-]{11})',
    ]
    for p in patterns:
        m = re.search(p, url)
        if m:
            return m.group(1)
    return url[-11:]


def _search_and_pick(config: dict, theme, keyword: str = "") -> tuple:
    """搜索 YouTube 并用 LLM 智能选择最合适的视频，返回 (url, video_info)"""
    from scraper import search_youtube

    queries = theme.get_search_queries(keyword)
    logger.info(f"   搜索词: {queries[:3]}...")

    all_results = []
    for q in queries[:5]:  # 最多搜 5 个关键词
        try:
            results = search_youtube(config, q, max_results=5)
            all_results.extend(results)
        except Exception as e:
            logger.debug(f"   搜索 '{q}' 失败: {e}")

    if not all_results:
        return None, {}

    # 去重 + 排除历史已处理
    history_ids = _get_history_video_ids()
    seen = set()
    unique = []
    skipped = 0
    for r in all_results:
        vid = r.get("video_id", r.get("url", ""))
        if vid in seen:
            continue
        seen.add(vid)
        if vid in history_ids:
            skipped += 1
            continue
        unique.append(r)

    if skipped:
        logger.info(f"   ⏭️ 跳过 {skipped} 个已处理过的视频")

    if not unique:
        logger.warning("   ⚠️ 所有搜索结果都已处理过，没有新视频")
        return None, {}

    logger.info(f"   共 {len(unique)} 个候选视频，LLM 筛选中...")

    # 用 LLM 智能挑选
    best = _llm_pick_video(config, unique, theme)
    if not best:
        # LLM 失败时 fallback：选播放量最高的
        logger.warning("   ⚠️ LLM 筛选失败，fallback 选播放量最高")
        best = max(unique, key=lambda x: x.get("view_count", 0))

    url = best.get("url", f"https://www.youtube.com/watch?v={best.get('video_id', '')}")
    return url, best


def _llm_pick_video(config: dict, candidates: list[dict], theme) -> dict | None:
    """
    用 LLM 从候选视频中选出最适合做二创的那个。
    优先选：单部作品预告片 > 合集/总集编 > 新闻/花絮
    """
    from writer import _call_llm

    # 构造候选列表文本
    items = []
    for i, c in enumerate(candidates):
        dur = c.get("duration", 0)
        dur_str = f"{int(dur)//60}:{int(dur)%60:02d}" if dur else "?"
        views = c.get("view_count", 0)
        items.append(
            f"[{i}] 标题: {c.get('title', '?')}\n"
            f"    频道: {c.get('channel', '?')} | 时长: {dur_str} | 播放量: {views:,}"
        )
    candidates_text = "\n".join(items)

    theme_desc = getattr(theme, "name", "unknown")
    audio_mode = getattr(theme, "audio_mode", "subtitle_only")

    # 获取历史记录中的作品名，用于避免连续选同一作品
    history = _load_history()
    recent_works = []
    for vid, info in sorted(history.items(), key=lambda x: x[1].get("timestamp", ""), reverse=True):
        t = info.get("title", "")
        if t:
            recent_works.append(t)
        if len(recent_works) >= 5:
            break
    recent_works_text = "\n".join(f"  - {w}" for w in recent_works) if recent_works else "  （无）"

    prompt = f"""你是一个 B站二创视频选片助手。从下面的 YouTube 搜索结果中，选出最适合做"加中文字幕搬运到B站"的视频。

主题: {theme_desc} (音频模式: {audio_mode})

候选视频:
{candidates_text}

最近已处理过的视频（尽量选不同作品，同作品的不同版本可以发但不要连续选同一作品）:
{recent_works_text}

选择标准（重要性从高到低）:
1. 必须是单部作品的官方预告片/预告編（trailer/teaser），不要合集（ラインナップ/まとめ/総集編）
2. 时长合适：预告片通常 30秒~4分钟
3. 有一定播放量，说明有关注度
4. 最近上传的优先（更新鲜）
5. 来自官方频道（Netflix Japan/Disney+等）更好
6. 尽量选跟最近处理过的不同的作品（不是硬性要求，但优先选新作品丰富内容多样性）

请只返回一个 JSON，格式: {{"index": N, "reason": "简短理由"}}
其中 N 是你选择的视频序号（方括号里的数字）。"""

    try:
        response = _call_llm(
            config,
            [{"role": "user", "content": prompt}],
            max_tokens=200,
        )

        # 解析 LLM 响应
        # 尝试提取 JSON
        json_match = re.search(r'\{[^}]+\}', response)
        if json_match:
            result = json.loads(json_match.group())
            idx = result.get("index", 0)
            reason = result.get("reason", "")
            if 0 <= idx < len(candidates):
                picked = candidates[idx]
                logger.info(f"   🤖 LLM 选择: [{idx}] {picked.get('title', '?')}")
                logger.info(f"      理由: {reason}")
                return picked

        logger.warning(f"   ⚠️ LLM 响应解析失败: {response[:200]}")
        return None

    except Exception as e:
        logger.warning(f"   ⚠️ LLM 选片调用失败: {e}")
        return None


def _build_publish_info(theme, video_info: dict, video_path: str, cover_path: str, platform: str = "bilibili") -> dict:
    """按平台生成发布信息

    bilibili: 标题<=80字, 简介<=2000字, 标签<=10个
    douyin:   标题<=30字, 描述<=1000字, 话题<=5个, 无搬运/三连/UP主用语
    """
    if platform == "douyin":
        title = theme.generate_title_douyin(video_info)
        desc = theme.generate_desc_douyin(video_info)
        tags = theme.generate_tags_douyin(video_info)
    else:
        # bilibili 或其他平台用默认
        title = theme.generate_title(video_info)
        desc = theme.generate_desc(video_info)
        tags = theme.generate_tags(video_info)

    return {
        "title": title,
        "description": desc,
        "tags": tags,
        "tid": theme.tid,
        "copyright": theme.copyright,
        "video_path": video_path,
        "cover_path": cover_path if os.path.exists(cover_path) else None,
        "source": video_info.get("url", "YouTube"),
        "platform": platform,
    }


def _upload_bilibili(config: dict, publish_info: dict) -> str | None:
    """上传到 B站，返回 BV号"""
    try:
        from uploader import upload_to_bilibili
        result = upload_to_bilibili(
            config,
            video_path=publish_info["video_path"],
            title=publish_info["title"],
            desc=publish_info["description"],
            tags=publish_info["tags"],
            tid=publish_info["tid"],
            cover_path=publish_info.get("cover_path"),
            source_url=publish_info.get("source", "YouTube"),
        )
        # result 可能是 dict 或 str(bvid)
        if isinstance(result, dict):
            return result.get("bvid", result.get("aid", str(result)))
        return result
    except ImportError:
        logger.warning("⚠️ uploader 模块未安装")
        return None


def _upload_douyin(config: dict, publish_info: dict) -> dict:
    """上传到抖音"""
    try:
        from uploader.douyin import upload_to_douyin
        return upload_to_douyin(
            config,
            video_path=publish_info["video_path"],
            title=publish_info["title"],
            desc=publish_info["description"],
            tags=publish_info.get("tags", []),
            cover_path=publish_info.get("cover_path"),
            headless=False,
        )
    except ImportError as e:
        return {"success": False, "message": f"抖音模块导入失败: {e}"}


def _fallback_cover(video_path: str, cover_path: str):
    """降级封面提取：取视频 30% 处帧"""
    import subprocess
    info_cmd = ["ffprobe", "-v", "quiet", "-show_entries",
                "format=duration", "-of", "csv=p=0", video_path]
    try:
        dur = float(subprocess.check_output(info_cmd).decode().strip())
    except Exception:
        dur = 60
    t = dur * 0.3
    subprocess.run([
        "ffmpeg", "-y", "-ss", str(t), "-i", video_path,
        "-vframes", "1", "-q:v", "2",
        "-vf", "crop=in_w:in_h*0.75:0:in_h*0.125",
        cover_path,
    ], capture_output=True)


def _cleanup(run_dir: str, keep_publish_info: bool = True):
    """清理运行目录，只保留发布信息"""
    import shutil

    if keep_publish_info:
        # 保留 run_info.json 和 output/publish_info.json
        for subdir in ["source", "audio", "script"]:
            path = os.path.join(run_dir, subdir)
            if os.path.isdir(path):
                shutil.rmtree(path)
        # 删除大的视频文件
        output_dir = os.path.join(run_dir, "output")
        for f in os.listdir(output_dir):
            if f.endswith(".mp4"):
                os.remove(os.path.join(output_dir, f))
    else:
        shutil.rmtree(run_dir)


def _save_json(data: dict, path: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ══════════════════════════════════════════════════════════
#  CLI 入口
# ══════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="🎬 多渠道内容管线 — Netflix预告 / 日本美食 / 日本旅行",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python main.py netflix                           # 自动搜索 + 上传 B站
  python main.py netflix "https://youtu.be/xxx"    # 指定视频 URL
  python main.py netflix "地獄に堕ちるわよ"          # 搜索关键词
  python main.py food "ラーメン 職人"                # 日本拉面匠人
  python main.py travel "京都 紅葉"                  # 京都红叶旅行

多渠道发布:
  python main.py netflix -p bilibili               # 只发 B站（默认）
  python main.py netflix -p bilibili,douyin        # B站 + 抖音
  python main.py netflix -p douyin                 # 只发抖音

可用主题: netflix, food, travel
支持平台: bilibili(B站), douyin(抖音)
        """
    )
    parser.add_argument("theme", nargs="?", choices=list_themes(),
                        help="主题: netflix / food / travel")
    parser.add_argument("target", nargs="?", default=None,
                        help="YouTube URL 或搜索关键词")
    parser.add_argument("--config", default="config.yaml", help="配置文件路径")
    parser.add_argument("-p", "--platforms", default="bilibili",
                        help="发布平台，逗号分隔: bilibili,douyin（默认: bilibili）")
    parser.add_argument("--no-upload", action="store_true",
                        help="不上传任何平台，只生成视频")
    parser.add_argument("--list-themes", action="store_true",
                        help="列出所有可用主题")
    parser.add_argument("--history", action="store_true",
                        help="查看已处理视频历史记录")
    parser.add_argument("--clear-history", metavar="VIDEO_ID",
                        help="从历史记录中删除指定视频（允许重新处理）")

    args = parser.parse_args()

    if args.history:
        history = _load_history()
        if not history:
            print("\n📭 历史记录为空\n")
            return
        print(f"\n📋 已处理视频历史 ({len(history)} 条):\n")
        for vid, info in sorted(history.items(), key=lambda x: x[1].get("timestamp", ""), reverse=True):
            status = "✅ 已上传" if info.get("uploaded") else "📝 已处理"
            bvid = info.get("bvid", "")
            bvid_str = f" (BV: {bvid})" if bvid else ""
            print(f"  {status} {info.get('title', vid)[:50]}")
            print(f"         ID: {vid} | {info.get('theme', '?')} | {info.get('timestamp', '?')[:10]}{bvid_str}")
        print()
        return

    if args.clear_history:
        history = _load_history()
        vid = args.clear_history
        if vid in history:
            removed = history.pop(vid)
            _save_history(history)
            print(f"\n🗑️ 已从历史中删除: {removed.get('title', vid)}\n")
        else:
            print(f"\n⚠️ 未找到 video_id: {vid}\n")
        return

    if args.list_themes or not args.theme:
        print("\n可用主题:")
        for name in list_themes():
            t = get_theme(name)
            print(f"  {name:10s} — 分区:{t.tid}, 音频:{t.audio_mode}")
        print("\n用法: python main.py <theme> [url/keyword]\n")
        return

    config = load_config(args.config)

    if args.no_upload:
        # 临时禁用上传
        config["_no_upload"] = True

    # 解析平台列表
    VALID_PLATFORMS = {"bilibili", "douyin"}
    platforms = [p.strip().lower() for p in args.platforms.split(",")]
    invalid = [p for p in platforms if p not in VALID_PLATFORMS]
    if invalid:
        print(f"\n❌ 未知平台: {', '.join(invalid)}")
        print(f"   支持的平台: {', '.join(sorted(VALID_PLATFORMS))}\n")
        sys.exit(1)

    run_pipeline(config, args.theme, target=args.target, platforms=platforms)


if __name__ == "__main__":
    main()
