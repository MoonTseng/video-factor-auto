"""YouTube 视频发现 + 下载 — 支持日本美食纪录片搬运 & 日韩预告片搜集"""

import json
import logging
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import httpx

logger = logging.getLogger(__name__)


def _get_ytdlp_bin() -> str:
    """获取 yt-dlp 可执行文件路径（优先 .venv 内的）"""
    # 优先用项目 .venv 里的 yt-dlp
    venv_bin = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".venv", "bin", "yt-dlp")
    if os.path.isfile(venv_bin):
        return venv_bin
    # fallback: 系统 PATH
    return "yt-dlp"


# 缓存探测结果，进程生命周期内只探测一次
_js_runtime: str | None = None  # "node", "deno", "bun" 或 ""


def _ensure_js_paths_in_env():
    """确保 deno / node / bun 等常见安装路径都在 PATH 中。
    很多 AI agent 环境下 PATH 只包含系统基础路径，遗漏用户级别安装。"""
    extra_paths = [
        os.path.join(os.path.expanduser("~"), ".deno", "bin"),
        os.path.join(os.path.expanduser("~"), ".local", "bin"),
        "/usr/local/bin",
        "/opt/homebrew/bin",
    ]
    # NVM 路径：扫描 ~/.nvm/versions/node/*/bin
    nvm_dir = os.path.join(os.path.expanduser("~"), ".nvm", "versions", "node")
    if os.path.isdir(nvm_dir):
        try:
            versions = sorted(os.listdir(nvm_dir), reverse=True)
            for v in versions:
                nbin = os.path.join(nvm_dir, v, "bin")
                if os.path.isdir(nbin):
                    extra_paths.append(nbin)
                    break  # 只取最新版
        except OSError:
            pass

    current = os.environ.get("PATH", "")
    for p in extra_paths:
        if os.path.isdir(p) and p not in current:
            os.environ["PATH"] = p + os.pathsep + os.environ["PATH"]


def _detect_js_runtime() -> str:
    """探测可用的 JS 运行时（yt-dlp 解 YouTube n-challenge 必需）。
    如果没有任何运行时，自动尝试安装 deno。"""
    global _js_runtime
    if _js_runtime is not None:
        return _js_runtime

    import shutil
    _ensure_js_paths_in_env()

    for rt in ("deno", "node", "bun"):
        path = shutil.which(rt)
        if path:
            _js_runtime = rt
            logger.info(f"🔧 检测到 JS 运行时: {rt} ({path})")
            return _js_runtime

    # 没有任何 JS runtime —— 自动安装 deno（最轻量、yt-dlp 默认支持）
    logger.warning("⚠️ 未检测到 JS 运行时 (PATH=%s)，尝试自动安装 deno...", os.environ.get("PATH", "")[:200])
    try:
        install = subprocess.run(
            ["sh", "-c", "curl -fsSL https://deno.land/install.sh | sh"],
            capture_output=True, text=True, timeout=60,
        )
        _ensure_js_paths_in_env()
        if shutil.which("deno"):
            _js_runtime = "deno"
            logger.info("✅ deno 自动安装成功")
            return _js_runtime
        else:
            logger.error(f"❌ deno 安装后仍不可用: {install.stderr[-200:]}")
    except Exception as e:
        logger.error(f"❌ deno 自动安装失败: {e}")

    _js_runtime = ""
    logger.warning("⚠️ JS 运行时不可用，YouTube 下载可能会被 bot 验证拦截")
    return _js_runtime


def _add_js_runtime_args(cmd: list[str]) -> None:
    """为 yt-dlp 命令添加 JS 运行时 + remote-components 参数"""
    rt = _detect_js_runtime()
    if rt:
        cmd.extend(["--js-runtimes", rt])
    # 允许 yt-dlp 从 GitHub 拉取 JS 挑战求解脚本（n-challenge 等）
    cmd.extend(["--remote-components", "ejs:github"])


def _add_cookies_args(cmd: list[str]) -> None:
    """为 yt-dlp 命令添加 cookies 参数。
    
    策略：
    1. macOS 有 Chrome → 自动从 Chrome DB 导出新鲜 cookies 文件
    2. 无 Chrome → 用已有的静态 cookies 文件
    """
    project_root = os.path.dirname(os.path.dirname(__file__))
    cookies_file = os.path.join(project_root, "www.youtube.com_cookies.txt")
    export_script = os.path.join(project_root, "scripts", "export_chrome_cookies.py")

    # macOS + Chrome 存在 → 自动导出最新 cookies
    chrome_dir = os.path.expanduser("~/Library/Application Support/Google/Chrome")
    if not os.path.isdir(chrome_dir):
        chrome_dir = os.path.expanduser("~/.config/google-chrome")

    if os.path.isdir(chrome_dir) and os.path.isfile(export_script):
        # 每次运行都导出最新 cookies（防止 token rotation）
        try:
            logger.info("🍪 从 Chrome 导出最新 cookies...")
            result = subprocess.run(
                [sys.executable, export_script],
                capture_output=True, text=True, timeout=60,
                cwd=project_root,
            )
            if result.returncode == 0:
                logger.info("🍪 Chrome cookies 导出成功")
            else:
                logger.warning(f"⚠️ Chrome cookies 导出失败: {result.stderr[-200:]}")
        except Exception as e:
            logger.warning(f"⚠️ Chrome cookies 导出异常: {e}")

    # 校验 cookies 文件
    if os.path.isfile(cookies_file):
        file_size = os.path.getsize(cookies_file)
        if file_size < 1000:
            logger.warning(f"⚠️ cookies 文件可能不完整 ({file_size} 字节)，尝试从 git 恢复...")
            try:
                subprocess.run(
                    ["git", "checkout", "origin/main", "--", "www.youtube.com_cookies.txt"],
                    cwd=project_root, capture_output=True, text=True, timeout=10,
                )
            except Exception:
                pass

    if os.path.isfile(cookies_file) and os.path.getsize(cookies_file) >= 1000:
        cmd.extend(["--cookies", cookies_file])
        logger.info(f"🍪 使用cookies文件: {cookies_file} ({os.path.getsize(cookies_file)} 字节)")
    else:
        logger.warning("⚠️ 无可用的 cookies，部分视频可能无法下载")


def search_youtube(config: dict, query: str, max_results: int = 5) -> list[dict]:
    """
    通用 YouTube 搜索（yt-dlp 驱动）。
    返回 [{video_id, title, channel, url, duration, view_count, upload_date}, ...]
    """
    yt_cfg = config.get("youtube", {})
    proxy = yt_cfg.get("proxy", "")

    results = _ytdlp_search(query, proxy, max_results)
    videos = []
    for r in results:
        vid = r.get("id", "")
        if not vid:
            continue
        videos.append({
            "video_id": vid,
            "title": r.get("title", ""),
            "channel": r.get("channel", r.get("uploader", "")),
            "url": f"https://www.youtube.com/watch?v={vid}",
            "duration": r.get("duration", 0),
            "view_count": r.get("view_count", 0),
            "upload_date": r.get("upload_date", ""),
        })
    return videos

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
    "焼き鳥 職人",         # 烤鸡肉串匠人
]

# ── 预告片搜索关键词（按平台分组） ─────────────────────
TRAILER_QUERIES = {
    "netflix_japan": [
        "Netflix Japan 2026 予告",
        "Netflix 日本 2026 新作 予告編",
        "Netflix Japan 2026 trailer",
        "Netflixジャパン 2026 ドラマ 予告",
        "Netflix 日本映画 2026 予告",
    ],
    "disney_japan": [
        "Disney+ Japan 2026 予告",
        "ディズニープラス 2026 新作 予告編",
        "Disney Plus 日本 2026 trailer",
        "ディズニープラス 韓ドラ 2026",
    ],
    "hulu_japan": [
        "Hulu Japan 2026 予告",
        "Hulu ジャパン 新作ドラマ 2026",
        "Hulu Japan 2026 original trailer",
    ],
    "netflix_korea": [
        "Netflix Korea 2026 예고편",
        "넷플릭스 2026 한국 드라마 예고",
        "Netflix Korean drama 2026 trailer",
        "Netflix 韓国ドラマ 2026 予告",
    ],
}


# ══════════════════════════════════════════════════════════════
#  预告片搜索 & 下载（新功能）
# ══════════════════════════════════════════════════════════════

def search_trailers(config: dict, platform: str = "all",
                    max_per_query: int = 10) -> list[dict]:
    """
    使用 yt-dlp 搜索各平台的 2026 年日韩预告片。
    
    参数:
        config: 配置字典
        platform: "all" 或具体平台名 (netflix_japan/disney_japan/hulu_japan/netflix_korea)
        max_per_query: 每个关键词最多返回几条
    返回:
        [{video_id, title, channel, url, duration, platform, upload_date}, ...]
    """
    yt_cfg = config.get("youtube", {})
    proxy = yt_cfg.get("proxy", "")
    
    # 也可从 config.trailer.search_queries 覆盖
    trailer_cfg = config.get("trailer", {})
    queries_map = trailer_cfg.get("search_queries", TRAILER_QUERIES)
    
    if platform == "all":
        platforms = list(queries_map.keys())
    else:
        platforms = [platform]
    
    all_results = []
    seen_ids = set()
    
    for plat in platforms:
        queries = queries_map.get(plat, [])
        for query in queries:
            try:
                results = _ytdlp_search(query, proxy, max_per_query)
                for r in results:
                    vid = r.get("id", "")
                    if vid and vid not in seen_ids:
                        seen_ids.add(vid)
                        all_results.append({
                            "video_id": vid,
                            "title": r.get("title", ""),
                            "channel": r.get("channel", r.get("uploader", "")),
                            "url": f"https://www.youtube.com/watch?v={vid}",
                            "duration": r.get("duration", 0),
                            "platform": plat,
                            "upload_date": r.get("upload_date", ""),
                            "view_count": r.get("view_count", 0),
                            "search_query": query,
                        })
            except Exception as e:
                logger.warning(f"搜索 '{query}' 失败: {e}")
                continue
    
    # 按上传日期倒序（最新的在前）
    all_results.sort(key=lambda x: x.get("upload_date", ""), reverse=True)
    logger.info(f"🔍 预告片搜索完成: {len(all_results)} 条 ({', '.join(platforms)})")
    return all_results


def download_trailer(config: dict, trailer_info: dict, output_dir: str) -> dict:
    """
    下载预告片（最高画质，up to 1080p）。
    
    参数:
        config: 配置字典
        trailer_info: search_trailers 返回的单条记录
        output_dir: 输出目录
    返回:
        {video_path, audio_path, duration, title, video_id, url, platform}
    """
    os.makedirs(output_dir, exist_ok=True)
    yt_cfg = config.get("youtube", {})
    proxy = yt_cfg.get("proxy", "")
    
    video_url = trailer_info.get("url", "")
    video_id = trailer_info.get("video_id", "unknown")
    platform = trailer_info.get("platform", "unknown")
    
    if not video_url:
        raise ValueError(f"预告片 URL 为空: {trailer_info}")
    
    video_path = os.path.join(output_dir, f"{video_id}.mp4")
    audio_path = os.path.join(output_dir, f"{video_id}_audio.m4a")
    
    logger.info(f"📥 下载预告片: {trailer_info.get('title', video_url)}")
    
    # 尽量高清：1080p > 720p > best
    fmt = (
        "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/"
        "bestvideo[height<=1080]+bestaudio/"
        "best[height<=1080]/best"
    )
    
    cmd = [
        _get_ytdlp_bin(),
        "-f", fmt,
        "--merge-output-format", "mp4",
        "-o", video_path,
        "--no-playlist",
        "--no-warnings",
        "--remote-components", "ejs:github",
    ]
    
    if proxy:
        cmd.extend(["--proxy", proxy])
    
    _add_cookies_args(cmd)
    _add_js_runtime_args(cmd)
    
    cmd.append(video_url)
    
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=1200)
    if result.returncode != 0:
        logger.error(f"yt-dlp 下载失败: {result.stderr[-500:]}")
        raise RuntimeError(f"yt-dlp 下载失败: {result.stderr[-200:]}")
    
    # 提取纯音频（给 Whisper 用）
    logger.info("🎵 提取音频轨道...")
    audio_cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-vn", "-acodec", "copy",
        audio_path,
    ]
    audio_result = subprocess.run(audio_cmd, capture_output=True, text=True, timeout=120)
    if audio_result.returncode != 0:
        audio_path = audio_path.replace(".m4a", ".mp3")
        audio_cmd = [
            "ffmpeg", "-y",
            "-i", video_path,
            "-vn", "-acodec", "libmp3lame", "-q:a", "2",
            audio_path,
        ]
        subprocess.run(audio_cmd, capture_output=True, text=True, timeout=120)
    
    duration = _get_duration(video_path)
    logger.info(f"✅ 预告片下载完成: {video_path} ({duration:.0f}s)")
    
    return {
        "video_path": video_path,
        "audio_path": audio_path,
        "duration": duration,
        "title": trailer_info.get("title", ""),
        "video_id": video_id,
        "url": video_url,
        "platform": platform,
    }


def _ytdlp_search(query: str, proxy: str = "", max_results: int = 10) -> list[dict]:
    """用 yt-dlp 搜索 YouTube（不下载，只获取元数据）"""
    cmd = [
        _get_ytdlp_bin(),
        "--flat-playlist",
        "--dump-json",
        f"ytsearch{max_results}:{query}",
    ]
    if proxy:
        cmd.extend(["--proxy", proxy])
    
    _add_cookies_args(cmd)
    _add_js_runtime_args(cmd)
    
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        raise RuntimeError(f"yt-dlp 搜索失败: {result.stderr[-200:]}")
    
    items = []
    for line in result.stdout.strip().split("\n"):
        line = line.strip()
        if line:
            try:
                items.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return items


def format_trailer_list(trailers: list[dict]) -> str:
    """格式化预告片列表，便于展示"""
    lines = []
    for i, t in enumerate(trailers, 1):
        dur = t.get("duration", 0)
        dur_str = f"{int(dur) // 60}:{int(dur) % 60:02d}" if dur else "?"
        views = t.get("view_count", 0)
        view_str = f"{views:,}" if views else "?"
        lines.append(
            f"{i}. [{t['platform']}] {t['title']}\n"
            f"   频道: {t['channel']} | 时长: {dur_str} | "
            f"播放: {view_str} | {t['url']}"
        )
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════
#  原有功能：美食/旅行视频搜索（保持不变）
# ══════════════════════════════════════════════════════════════

def fetch_trending_topics(config: dict) -> list[dict]:
    """
    从 YouTube 搜索日本美食/旅行小众视频作为搬运候选。
    优先选择小频道、低播放量的内容（版权风险低）。
    如果 API 不可用，自动降级为内置视频列表。
    """
    yt_cfg = config.get("youtube", {})
    api_key = yt_cfg.get("api_key", "")
    proxy = yt_cfg.get("proxy", "")

    if api_key and api_key != "YOUR_API_KEY":
        topics = _fetch_via_api(api_key, yt_cfg, proxy)
        if topics:
            return topics

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

    logger.info(f"📥 下载视频: {topic.get('title', video_url)}")

    cmd = [
        _get_ytdlp_bin(),
        "-f", "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080][ext=mp4]/best",
        "--merge-output-format", "mp4",
        "-o", video_path,
        "--no-playlist",
        "--no-warnings",
        "--remote-components", "ejs:github",
    ]

    if proxy:
        cmd.extend(["--proxy", proxy])

    _add_cookies_args(cmd)
    _add_js_runtime_args(cmd)

    cmd.append(video_url)

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=1200)
    if result.returncode != 0:
        logger.error(f"yt-dlp 下载失败: {result.stderr[-500:]}")
        raise RuntimeError(f"yt-dlp 下载失败: {result.stderr[-200:]}")

    logger.info("🎵 提取音频轨道...")
    audio_cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-vn", "-acodec", "copy",
        audio_path,
    ]
    audio_result = subprocess.run(audio_cmd, capture_output=True, text=True, timeout=120)
    if audio_result.returncode != 0:
        audio_path = audio_path.replace(".m4a", ".mp3")
        audio_cmd = [
            "ffmpeg", "-y",
            "-i", video_path,
            "-vn", "-acodec", "libmp3lame", "-q:a", "2",
            audio_path,
        ]
        subprocess.run(audio_cmd, capture_output=True, text=True, timeout=120)

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
                "order": "date",
                "maxResults": max_per_query,
                "publishedAfter": published_after,
                "relevanceLanguage": "ja",
                "videoDuration": "medium",
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
    all_topics.sort(key=lambda x: x["view_count"], reverse=True)
    return _deduplicate(all_topics)[:20]


# ── 内置视频列表（兜底用） ────────────────────────────────
def _get_builtin_topics() -> list[dict]:
    """内置的日本美食/旅行视频列表 — API 不可用时的兜底。"""
    topics = [
        {
            "title": "夫婦で守り続ける天ぷら屋 — 40年の味",
            "description": "东京下町的一对夫妻经营了40年的天妇罗小店。",
            "view_count": 15000,
            "suggested_theme": "夫妻二人坚守40年的天妇罗小店",
            "video_id": "builtin_tempura", "url": "",
        },
        {
            "title": "行列のできるラーメン屋の1日",
            "description": "大排长龙的拉面店，美女老板娘的一天跟拍。",
            "view_count": 25000,
            "suggested_theme": "美女拉面店老板娘的一天",
            "video_id": "builtin_ramen", "url": "",
        },
        {
            "title": "日本の離島で一人旅 — 人口30人の島",
            "description": "日本一座只有30人的小岛，一个人的旅行纪录。",
            "view_count": 8000,
            "suggested_theme": "人口仅30人的日本离岛独旅",
            "video_id": "builtin_island", "url": "",
        },
    ]
    for t in topics:
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
