"""B站视频上传模块 — 扫码登录 + 凭证持久化 + 自动刷新 + 一键发布"""

import asyncio
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# 凭证持久化路径
CREDENTIAL_FILE = Path(__file__).parent.parent / ".bili_credential.json"


# ═══════════════════════════════════════════════════════
#  凭证管理：扫码登录 / 保存 / 加载 / 刷新
# ═══════════════════════════════════════════════════════

def _save_credential(cred) -> None:
    """将 Credential 序列化保存到本地 JSON"""
    data = cred.get_cookies()
    data["dedeuserid"] = cred.dedeuserid or ""
    data["saved_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    CREDENTIAL_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    logger.info(f"💾 凭证已保存到 {CREDENTIAL_FILE}")


def _load_credential():
    """从本地 JSON 加载已保存的 Credential"""
    from bilibili_api import Credential

    if not CREDENTIAL_FILE.exists():
        return None

    try:
        data = json.loads(CREDENTIAL_FILE.read_text())
        cred = Credential(
            sessdata=data.get("SESSDATA", ""),
            bili_jct=data.get("bili_jct", ""),
            buvid3=data.get("buvid3", ""),
            buvid4=data.get("buvid4", ""),
            dedeuserid=data.get("dedeuserid", ""),
            ac_time_value=data.get("ac_time_value", ""),
        )
        if not cred.sessdata or not cred.bili_jct:
            logger.warning("⚠️ 已保存的凭证不完整，需要重新登录")
            return None
        return cred
    except Exception as e:
        logger.warning(f"⚠️ 加载凭证失败: {e}")
        return None


async def _refresh_if_needed(cred) -> bool:
    """检查并刷新凭证（如果快过期）"""
    try:
        need_refresh = await cred.check_refresh()
        if need_refresh:
            logger.info("🔄 凭证即将过期，正在刷新...")
            await cred.refresh()
            _save_credential(cred)
            logger.info("✅ 凭证刷新成功")
            return True
        return False
    except Exception as e:
        logger.warning(f"⚠️ 凭证刷新失败: {e}")
        return False


def qrcode_login() -> dict:
    """
    扫码登录B站。

    在终端显示二维码，用B站手机客户端扫码即可登录。
    登录成功后凭证自动保存到 .bili_credential.json。

    返回:
        {"name": "...", "uid": 12345, "credential": Credential}
    """
    from bilibili_api import login_v2, sync as bili_sync

    qr = login_v2.QrCodeLogin(platform=login_v2.QrCodeLoginChannel.WEB)

    # 生成二维码
    bili_sync(qr.generate_qrcode())

    # 终端显示二维码
    terminal_qr = qr.get_qrcode_terminal()
    print("\n" + "=" * 50)
    print("📱 请使用B站手机客户端扫描下方二维码登录")
    print("=" * 50)
    print(terminal_qr)
    print("=" * 50)

    # 等待扫码
    state = None
    max_wait = 120  # 最多等2分钟
    start = time.time()

    while time.time() - start < max_wait:
        state = bili_sync(qr.check_state())

        if state == login_v2.QrCodeLoginEvents.DONE:
            print("✅ 扫码成功！")
            break
        elif state == login_v2.QrCodeLoginEvents.SCAN:
            print("📲 已扫码，请在手机上确认...")
        elif state == login_v2.QrCodeLoginEvents.TIMEOUT:
            print("⏰ 二维码已过期")
            raise TimeoutError("二维码已过期，请重新运行 qrcode_login()")

        time.sleep(2)
    else:
        raise TimeoutError("等待扫码超时")

    # 获取凭证
    credential = qr.get_credential()

    # 补充 buvid
    bili_sync(credential.get_buvid_cookies())

    # 保存凭证
    _save_credential(credential)

    # 验证并返回用户信息
    info = verify_credential_from_cred(credential)
    info["credential"] = credential
    return info


def get_credential(config: dict = None):
    """
    获取可用的 Credential（优先本地缓存，自动刷新）。
    
    优先级:
        1. 本地 .bili_credential.json（自动刷新）
        2. config.yaml 中的 credential 配置
        3. 都没有则提示需要扫码登录
    """
    from bilibili_api import Credential

    # 1. 尝试加载本地缓存
    cred = _load_credential()
    if cred:
        # 尝试刷新
        try:
            loop = asyncio.new_event_loop()
            loop.run_until_complete(_refresh_if_needed(cred))
            loop.close()
        except Exception:
            pass  # 刷新失败也先用着
        logger.info("🔑 使用本地缓存凭证")
        return cred

    # 2. 尝试从 config 读取
    if config:
        bili_cfg = config.get("bilibili", {})
        cred_cfg = bili_cfg.get("credential", {})
        sessdata = cred_cfg.get("sessdata", "")
        bili_jct = cred_cfg.get("bili_jct", "")
        if sessdata and bili_jct:
            cred = Credential(
                sessdata=sessdata,
                bili_jct=bili_jct,
                buvid3=cred_cfg.get("buvid3", ""),
                buvid4=cred_cfg.get("buvid4", ""),
                dedeuserid=cred_cfg.get("dedeuserid", ""),
            )
            logger.info("🔑 使用 config.yaml 凭证")
            return cred

    # 3. 都没有
    raise ValueError(
        "未找到B站登录凭证！请先运行扫码登录:\n"
        "  python -c \"from uploader import qrcode_login; qrcode_login()\""
    )


def verify_credential_from_cred(cred) -> dict:
    """用 Credential 对象验证登录状态"""
    from bilibili_api import user, sync as bili_sync

    uid = int(cred.dedeuserid) if cred.dedeuserid else 0
    if not uid:
        # 尝试从 cookies 获取
        cookies = cred.get_cookies()
        uid = int(cookies.get("DedeUserID", 0))

    if uid:
        u = user.User(uid=uid, credential=cred)
        info = bili_sync(u.get_user_info())
        return {
            "name": info.get("name", ""),
            "uid": info.get("mid", uid),
            "level": info.get("level", 0),
        }
    return {"name": "未知", "uid": 0, "level": 0}


def verify_credential(config: dict = None) -> dict:
    """验证B站登录状态，返回用户信息"""
    cred = get_credential(config)
    return verify_credential_from_cred(cred)


# ═══════════════════════════════════════════════════════
#  视频上传
# ═══════════════════════════════════════════════════════

async def _upload_video_async(
    config: dict,
    video_path: str,
    title: str = "",
    desc: str = "",
    tags: list[str] = None,
    cover_path: str = None,
    source_url: str = "",
    tid: int = None,
) -> dict:
    """异步上传视频到B站"""
    from bilibili_api.video_uploader import (
        VideoUploader,
        VideoUploaderPage,
        VideoMeta,
        VideoUploaderEvents,
    )

    bili_cfg = config.get("bilibili", {})
    credential = get_credential(config)

    if not os.path.exists(video_path):
        raise FileNotFoundError(f"视频文件不存在: {video_path}")

    # ── 参数处理 ─────────────────────────────────────
    if tags is None:
        tags = bili_cfg.get("default_tags", ["日剧", "预告片", "2026新番", "Netflix"])
    tags = [t[:20] for t in tags[:10]]

    if not title:
        title = "新视频"
    title = title[:80]

    if desc:
        desc = desc[:2000]

    if tid is None:
        tid = bili_cfg.get("tid", 183)

    copyright_type = bili_cfg.get("copyright", 2)
    is_original = copyright_type == 1
    source = source_url or bili_cfg.get("default_source", "YouTube")

    # ── 构建上传页 ────────────────────────────────────
    page = VideoUploaderPage(
        path=video_path,
        title=title,
        description="",
    )

    # ── 构建视频元信息 ─────────────────────────────────
    # 如果没有封面，从视频截取一帧
    actual_cover = cover_path
    temp_cover = None
    if not actual_cover or not os.path.exists(actual_cover):
        import subprocess
        temp_cover = video_path + ".cover.jpg"
        subprocess.run([
            "ffmpeg", "-y", "-ss", "10", "-i", video_path,
            "-vframes", "1", "-q:v", "2", "-update", "1", temp_cover
        ], capture_output=True)
        if os.path.exists(temp_cover):
            actual_cover = temp_cover
            logger.info("🖼️ 自动从视频截取封面")

    meta_kwargs = dict(
        tid=tid,
        title=title,
        desc=desc,
        tags=tags,
        original=is_original,
        source=None if is_original else source,
        no_reprint=is_original,
    )
    if actual_cover and os.path.exists(actual_cover):
        from bilibili_api.utils.picture import Picture
        meta_kwargs["cover"] = Picture().from_file(actual_cover)
    else:
        # cover 是必需参数，传空字符串让B站用默认
        meta_kwargs["cover"] = ""

    meta = VideoMeta(**meta_kwargs)

    # ── 创建上传器 ────────────────────────────────────
    uploader_kwargs = dict(
        pages=[page],
        meta=meta,
        credential=credential,
    )
    if cover_path and os.path.exists(cover_path):
        uploader_kwargs["cover"] = cover_path

    uploader = VideoUploader(**uploader_kwargs)

    # ── 事件监听 ─────────────────────────────────────
    file_size_mb = os.path.getsize(video_path) / 1024 / 1024
    chunk_count = 0

    @uploader.on(VideoUploaderEvents.PREUPLOAD.value)
    async def on_preupload(data):
        logger.info(f"📤 开始上传: {os.path.basename(video_path)} ({file_size_mb:.1f}MB)")

    @uploader.on(VideoUploaderEvents.PRE_PAGE.value)
    async def on_pre_page(data):
        logger.info(f"📦 准备上传分P...")

    @uploader.on(VideoUploaderEvents.AFTER_CHUNK.value)
    async def on_after_chunk(data):
        nonlocal chunk_count
        chunk_count += 1
        if chunk_count % 10 == 0:
            logger.info(f"  ⏳ 已上传 {chunk_count} 个分块...")

    @uploader.on(VideoUploaderEvents.PRE_COVER.value)
    async def on_pre_cover(data):
        logger.info("🖼️ 上传封面...")

    @uploader.on(VideoUploaderEvents.PRE_SUBMIT.value)
    async def on_pre_submit(data):
        logger.info("📋 提交稿件...")

    @uploader.on(VideoUploaderEvents.COMPLETED.value)
    async def on_completed(data):
        logger.info("✅ 上传完成！")

    @uploader.on(VideoUploaderEvents.FAILED.value)
    async def on_failed(data):
        logger.error(f"❌ 上传失败: {data}")

    # ── 执行上传 ─────────────────────────────────────
    logger.info(f"🎬 B站上传: 《{title}》")
    logger.info(f"   分区={tid}, 标签={tags}, 版权={'原创' if is_original else '转载'}")

    result = await uploader.start()

    # ── 解析结果 ─────────────────────────────────────
    if result and isinstance(result, dict):
        bvid = result.get("bvid", "")
        aid = result.get("aid", "")
        url = f"https://www.bilibili.com/video/{bvid}" if bvid else ""
        logger.info(f"🎉 发布成功! {url}")
        return {"bvid": bvid, "aid": aid, "url": url, "raw": result}
    else:
        logger.warning(f"⚠️ 提交结果: {result}")
        return {"raw": result}


def upload_to_bilibili(
    config: dict,
    video_path: str,
    title: str = "",
    desc: str = "",
    tags: list[str] = None,
    cover_path: str = None,
    source_url: str = "",
    tid: int = None,
) -> dict:
    """
    上传视频到B站（同步接口）。

    参数:
        config: 配置字典（需包含 bilibili 段）
        video_path: 视频文件路径
        title: 视频标题（<=80字）
        desc: 视频简介（<=2000字）
        tags: 标签列表（<=10个）
        cover_path: 封面图片路径（可选）
        source_url: 转载来源 URL
        tid: 分区ID（覆盖配置，常用: 183=影视剪辑, 17=影视杂谈, 211=美食, 212=美食侦探）

    返回:
        {"bvid": "BV...", "aid": 12345, "url": "https://www.bilibili.com/video/BV..."}
    """
    return asyncio.run(
        _upload_video_async(
            config=config,
            video_path=video_path,
            title=title,
            desc=desc,
            tags=tags,
            cover_path=cover_path,
            source_url=source_url,
            tid=tid,
        )
    )


# ═══════════════════════════════════════════════════════
#  标题/简介自动生成
# ═══════════════════════════════════════════════════════

def generate_trailer_title(trailer_info: dict, style: str = "bilibili") -> str:
    """根据预告片信息自动生成B站标题"""
    original_title = trailer_info.get("title", "")
    platform = trailer_info.get("platform", "")

    platform_names = {
        "netflix_japan": "Netflix日本",
        "disney_japan": "Disney+",
        "hulu_japan": "Hulu日本",
        "netflix_korea": "Netflix韩国",
    }
    platform_cn = platform_names.get(platform, platform)
    work_name = _extract_work_name(original_title)

    if style == "bilibili":
        templates = [
            f"【{platform_cn}】{work_name}｜2026年重磅新作预告！中文字幕",
            f"🔥{platform_cn} 2026新作《{work_name}》预告片【中字】",
            f"【中字预告】{work_name}丨{platform_cn} 2026年最新力作",
            f"终于来了！{platform_cn}《{work_name}》2026预告片 中文字幕",
        ]
        templates.sort(key=len)
        for t in templates:
            if len(t) <= 80:
                return t
        return templates[0][:80]
    else:
        return f"{platform_cn}《{work_name}》2026 预告片 中文字幕"[:80]


def generate_trailer_desc(trailer_info: dict) -> str:
    """自动生成B站视频简介"""
    original_title = trailer_info.get("title", "")
    platform = trailer_info.get("platform", "")
    url = trailer_info.get("url", "")
    channel = trailer_info.get("channel", "")

    platform_names = {
        "netflix_japan": "Netflix日本",
        "disney_japan": "Disney+日本",
        "hulu_japan": "Hulu日本",
        "netflix_korea": "Netflix韩国",
    }
    platform_cn = platform_names.get(platform, platform)

    lines = [
        f"📺 {platform_cn} 2026年新作预告片",
        f"🎬 原标题: {original_title}",
        "",
        "本视频为官方预告片搬运，添加中文字幕方便国内观众观看。",
        "字幕翻译仅供参考，如有错误欢迎指正！",
        "",
        "━━━━━━━━━━━━━━━━━━━━━━━━",
    ]
    if channel:
        lines.append(f"📡 来源频道: {channel}")
    if url:
        lines.append(f"🔗 原视频: {url}")
    lines.extend(["", "#预告片 #2026新番 #中文字幕 #日剧 #韩剧"])
    return "\n".join(lines)


def _extract_work_name(title: str) -> str:
    """从 YouTube 预告片标题中提取作品名"""
    parts = re.split(r'\s*[\|｜\-\—]\s*', title)

    noise_patterns = [
        r'(?i)official\s*(trailer|teaser)',
        r'(?i)^(trailer|teaser)',
        r'(?i)^netflix\b',
        r'(?i)^disney\+?',
        r'(?i)^hulu\b',
        r'予告(編)?', r'ティーザー', r'예고편',
        r'^넷플릭스', r'^\d{4}$',
        r'(?i)netflix\s*(japan|korea|日本|韓国)',
    ]

    candidates = []
    semi_candidates = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        is_noise = any(re.search(pat, part) for pat in noise_patterns)
        if not is_noise:
            candidates.append(part)
        else:
            cleaned = part
            cleaned = re.sub(r'(?i)^(netflix|disney\+?|hulu)\s*(japan|korea|日本|韓国|한국)?\s*\d*\s*', '', cleaned)
            cleaned = re.sub(r'(?i)\s*(official\s*)?(trailer|teaser|予告編?|ティーザー|예고편?)$', '', cleaned)
            cleaned = re.sub(r'^넷플릭스\s*(오리지널\s*)?(시리즈\s*)?', '', cleaned)
            cleaned = cleaned.strip()
            if cleaned and len(cleaned) >= 2:
                semi_candidates.append(cleaned)

    if candidates:
        result = max(candidates, key=len)
    elif semi_candidates:
        result = max(semi_candidates, key=len)
    else:
        result = parts[0].strip() if parts else title

    result = re.sub(r'(?i)^(netflix|disney\+?|hulu)\s*(japan|korea|日本|韓国|한국)?\s*', '', result)
    result = re.sub(r'(?i)\s*(official\s*)?(trailer|teaser|予告編?|예고편?).*$', '', result)
    result = re.sub(r'^\d{4}\s*', '', result)
    result = re.sub(r'^넷플릭스\s*(오리지널\s*)?(시리즈\s*)?', '', result)
    result = re.sub(r'^(韓ドラ|ドラマ)\s*', '', result)

    return result.strip() or title[:30]
