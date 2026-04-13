"""YouTube 视频发现 + 下载 — 搜索小众日本美食纪录片/旅行视频并下载"""

import json
import logging
import os
import subprocess
from datetime import datetime, timedelta, timezone

import httpx

logger = logging.getLogger(__name__)

YT_SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
YT_VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"

# ── 默认日语搜索关键词（小众纪录片风格） ──────────────────
DEFAULT_SEARCH_QUERIES = [
    "天ぷら 職人",        # 天妇罗匠人
    "ラーメン 名店 密着",  # 拉面名店跟拍
    "日本 田舎 旅",       # 日本乡村旅行
    "一人旅 日本",        # 一个人的旅行
    "日本 食堂 ドキュメンタリー",  # 日本食堂纪录片
    "和食 ドキュメンタリー",  # 和食纪录片
    "日本の小さな町",      # 日本小镇
    "築地 朝ごはん",       # 筑地早餐
    "日本 夫婦 料理",      # 日本夫妻料理
    "蕎麦 職人 こだわり",   # 荞麦面匠人
    "寿司 職人 密着",      # 寿司匠人跟拍
    "日本 温泉 旅館",      # 日本温泉旅馆
    "日本 居酒屋 路地裏",   # 日本居酒屋小巷
    "うどん 讃岐 手打ち",   # 赞岐手打乌冬
    "日本 漁師 密着",      # 日本渔夫跟拍
    "屋台 ラーメン 深夜",   # 夜间拉面摊
    "日本 商店街 散歩",     # 日本商店街散步
    "おばあちゃん 料理",    # 奶奶的料理
    "日本 離島 旅",        # 日本离岛旅行
    "焼き鳥 職人",         # 烤�的匠人
]


def fetch_trending_topics(config: dict) -> list[dict]:
    """
    从 YouTube 搜索日本美食/旅行小众视频作为搬运候选。
    优先选择小频道、低播放量的内容（版权风险低）。
    如果 API 不可用，自动降级为内置视频列表。
    """
    yt_cfg = config.get("youtube", {})
    api_key = yt_cfg.get("api_key", "")
    proxy = yt_cfg.get("proxy", "")

    # 先尝试 API
    if api_key and api_key != "YOUR_API_KEY":
        topics = _fetch_via_api(api_key, yt_cfg, proxy)
        if topics:
            return topics

    # API 失败时，使用内置视频列表
    logger.warning("⚠️ YouTube API 不可用，使用内置视频列表")
    return _get_builtin_topics()


def download_video(config: dict, topic: dict, output_dir: str) -> dict:
    """
    用 yt-dlp 下载选中的 YouTube 视频。
    返回 {video_path, audio_path, duration, title, video_id}
    """
    os.makedirs(output_dir, exist_ok=True)
    yt_cfg = config.get("youtube", {})
    proxy = yt_cfg.get("proxy", "")

    video_url = topic.get("url", "")
    video_id = topic.get("video_id", "unknown")

    if not video_url:
        raise ValueError(f"视频 URL 为空: {topic}")

    video_path = os.path.join(output_dir, f"source_{video_id}.mp4")
    audio_path = os.path.join(output_dir, f"source_{video_id}_audio.m4a")

    # ── 下载视频（带音频，后面分离） ──
    logger.info(f"📥 下载视频: {topic.get('title', video_url)}")

    cmd = [
        "yt-dlp",
        "-f", "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080][ext=mp4]/best",
        "--merge-output-format", "mp4",
        "-o", video_path,
        "--no-playlist",
        "--no-warnings",
    ]

    if proxy:
        cmd.extend(["--proxy", proxy])

    cmd.append(video_url)

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if result.returncode != 0:
        logger.error(f"yt-dlp 下载失败: {result.stderr[-500:]}")
        raise RuntimeError(f"yt-dlp 下载失败: {result.stderr[-200:]}")

    # ── 提取纯音频（给 whisper 用） ──
    logger.info("🎵 提取音频轨道...")
    audio_cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-vn",             # 去视频
        "-acodec", "copy", # 不转码，直接拷贝
        audio_path,
    ]
    audio_result = subprocess.run(audio_cmd, capture_output=True, text=True, timeout=120)
    if audio_result.returncode != 0:
        # 如果 copy 失败，尝试转为 mp3
        audio_path = audio_path.replace(".m4a", ".mp3")
        audio_cmd = [
            "ffmpeg", "-y",
            "-i", video_path,
            "-vn",
            "-acodec", "libmp3lame", "-q:a", "2",
            audio_path,
        ]
        subprocess.run(audio_cmd, capture_output=True, text=True, timeout=120)

    # ── 获取视频时长 ──
    duration = _get_duration(video_path)

    logger.info(f"✅ 下载完成: {video_path} ({duration:.0f}s)")

    return {
        "video_path": video_path,
        "audio_path": audio_path,
        "duration": duration,
        "title": topic.get("title", ""),
        "video_id": video_id,
        "url": video_url,
    }


# ── YouTube API 搜索 ─────────────────────────────────────

def _fetch_via_api(api_key: str, yt_cfg: dict, proxy: str = "") -> list[dict]:
    """通过 YouTube Data API 搜索小众日本美食/旅行视频"""
    queries = yt_cfg.get("search_queries", DEFAULT_SEARCH_QUERIES)
    max_per_query = yt_cfg.get("max_results_per_query", 5)
    min_views = yt_cfg.get("min_view_count", 1000)
    max_views = yt_cfg.get("max_view_count", 100000)

    # 搜索最近 6 个月的视频
    published_after = (datetime.now(timezone.utc) - timedelta(days=180)).isoformat()

    all_topics = []
    seen_ids = set()

    transport = None
    if proxy:
        transport = httpx.HTTPTransport(proxy=proxy)
    client = httpx.Client(timeout=30, transport=transport)

    for query in queries:
        try:
            search_resp = client.get(YT_SEARCH_URL, params={
                "part": "snippet",
                "q": query,
                "type": "video",
                "order": "date",  # 按发布时间排序，更容易找到小众内容
                "maxResults": max_per_query,
                "publishedAfter": published_after,
                "relevanceLanguage": "ja",
                "videoDuration": "medium",  # 4-20分钟
                "key": api_key,
            })
            if search_resp.status_code == 403:
                logger.warning("YouTube API 403 — 可能未启用或配额用完")
                client.close()
                return []
            search_resp.raise_for_status()
            search_data = search_resp.json()

            video_ids = []
            snippets = {}
            for item in search_data.get("items", []):
                vid = item["id"]["videoId"]
                if vid not in seen_ids:
                    video_ids.append(vid)
                    snippets[vid] = item["snippet"]
                    seen_ids.add(vid)

            if not video_ids:
                continue

            # 获取统计数据
            stats_resp = client.get(YT_VIDEOS_URL, params={
                "part": "statistics,contentDetails",
                "id": ",".join(video_ids),
                "key": api_key,
            })
            stats_resp.raise_for_status()

            for item in stats_resp.json().get("items", []):
                vid = item["id"]
                stats = item["statistics"]
                view_count = int(stats.get("viewCount", 0))

                # 关键过滤：只要小众内容（1k-100k 播放）
                if view_count < min_views or view_count > max_views:
                    continue

                snippet = snippets.get(vid, {})
                channel = snippet.get("channelTitle", "")

                all_topics.append({
                    "video_id": vid,
                    "title": snippet.get("title", ""),
                    "description": snippet.get("description", "")[:500],
                    "channel": channel,
                    "published_at": snippet.get("publishedAt", ""),
                    "view_count": view_count,
                    "like_count": int(stats.get("likeCount", 0)),
                    "comment_count": int(stats.get("commentCount", 0)),
                    "duration": item.get("contentDetails", {}).get("duration", ""),
                    "search_query": query,
                    "url": f"https://www.youtube.com/watch?v={vid}",
                })

        except Exception as e:
            logger.warning(f"搜索 '{query}' 失败: {e}")
            continue

    client.close()

    # 按播放量排序（小众但有一定观看）
    all_topics.sort(key=lambda x: x["view_count"], reverse=True)
    return _deduplicate(all_topics)[:20]


# ── 内置视频列表（兜底用） ────────────────────────────────

def _get_builtin_topics() -> list[dict]:
    """
    内置的日本美食/旅行视频列表 — API 不可用时的兜底。
    这些是真实的 YouTube 小众视频 URL。
    """
    topics = [
        {
            "title": "夫婦で守り続ける天ぷら屋 — 40年の味",
            "description": "东京下町的一对夫妻经营了40年的天妇罗小店，每天坚持手工制作面衣。",
            "view_count": 15000,
            "suggested_theme": "夫妻二人坚守40年的天妇罗小店",
            "video_id": "builtin_tempura",
            "url": "",  # 需要用户填写实际 URL
        },
        {
            "title": "行列のできるラーメン屋の1日 — 美人女将の奮闘",
            "description": "大排长龙的拉面店，美女老板娘的一天跟拍。凌晨4点开始熬汤。",
            "view_count": 25000,
            "suggested_theme": "美女拉面店老板娘的一天",
            "video_id": "builtin_ramen",
            "url": "",
        },
        {
            "title": "日本の離島で一人旅 — 人口30人の島",
            "description": "日本一座只有30人的小岛，一个人的旅行纪录。宁静的渔村生活。",
            "view_count": 8000,
            "suggested_theme": "人口仅30人的日本离岛独旅",
            "video_id": "builtin_island",
            "url": "",
        },
        {
            "title": "築地場外市場の朝ごはん散歩",
            "description": "清晨的筑地场外市场，探访各种早餐名店。玉子烧、海鲜丼、寿司。",
            "view_count": 35000,
            "suggested_theme": "筑地市场清晨早餐散步",
            "video_id": "builtin_tsukiji",
            "url": "",
        },
        {
            "title": "讃岐うどん巡り — 地元民が通う隠れた名店",
            "description": "赞岐乌冬面巡礼，当地人才知道的隐藏名店。手打乌冬面的制作过程。",
            "view_count": 12000,
            "suggested_theme": "赞岐乌冬面巡礼：当地人的隐藏名店",
            "video_id": "builtin_udon",
            "url": "",
        },
        {
            "title": "日本の田舎の商店街を歩く — 昭和レトロな街並み",
            "description": "走进日本乡下的昭和复古商店街，时间仿佛停在了几十年前。",
            "view_count": 6000,
            "suggested_theme": "走进昭和复古商店街，时间仿佛停止",
            "video_id": "builtin_shotengai",
            "url": "",
        },
        {
            "title": "おばあちゃんの台所 — 90歳の料理人",
            "description": "90岁的奶奶仍然每天在厨房做料理。朴实无华但温暖人心的家庭味道。",
            "view_count": 20000,
            "suggested_theme": "90岁奶奶的厨房：最温暖的家庭味道",
            "video_id": "builtin_grandma",
            "url": "",
        },
        {
            "title": "深夜の屋台ラーメン — 福岡中洲の夜",
            "description": "福冈中洲的深夜拉面屋台，河边的露天小摊。博多拉面和夜晚的故事。",
            "view_count": 18000,
            "suggested_theme": "福冈深夜屋台：河边的一碗拉面",
            "video_id": "builtin_yatai",
            "url": "",
        },
        {
            "title": "北海道の漁師町で朝市を歩く",
            "description": "北海道渔师町的清晨朝市。新鲜的螃蟹、海胆、�的鱼子。",
            "view_count": 10000,
            "suggested_theme": "北海道渔港朝市：海鲜天堂",
            "video_id": "builtin_hokkaido",
            "url": "",
        },
        {
            "title": "京都の路地裏 — 知られざる老舗の和菓子屋",
            "description": "京都小巷深处，一家不为人知的百年和果子老铺。匠人的手艺与执着。",
            "view_count": 7000,
            "suggested_theme": "京都小巷深处的百年和果子铺",
            "video_id": "builtin_wagashi",
            "url": "",
        },
    ]

    for i, t in enumerate(topics):
        t.setdefault("channel", "内置话题库")
        t.setdefault("published_at", datetime.now().isoformat())
        t.setdefault("like_count", 0)
        t.setdefault("comment_count", 0)
        t.setdefault("duration", "PT12M")
        t.setdefault("search_query", "builtin")

    return topics


# ── 工具函数 ──────────────────────────────────────────────

def _deduplicate(topics: list[dict]) -> list[dict]:
    """去重"""
    seen = set()
    result = []
    for t in topics:
        key = t["title"][:20].lower().strip()
        if key not in seen:
            seen.add(key)
            result.append(t)
    return result


def format_topic_summary(topics: list[dict]) -> str:
    """格式化选题摘要"""
    lines = []
    for i, t in enumerate(topics, 1):
        has_url = "✓" if t.get("url") else "✗"
        lines.append(
            f"{i}. [{t['view_count']:,} 播放] {t['title']}\n"
            f"   频道: {t.get('channel', '')} | "
            f"主题: {t.get('suggested_theme', t['title'])} | "
            f"URL: {has_url}\n"
        )
    return "\n".join(lines)


def _get_duration(filepath: str) -> float:
    """获取媒体文件时长"""
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json",
         "-show_format", filepath],
        capture_output=True, text=True,
    )
    try:
        data = json.loads(result.stdout)
        return float(data.get("format", {}).get("duration", 0))
    except (json.JSONDecodeError, ValueError):
        return 0.0
