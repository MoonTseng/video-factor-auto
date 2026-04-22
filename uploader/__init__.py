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
    """异步上传视频到B站（带重试、线路选择、超时控制、进度百分比）"""
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
        watermark=is_original,  # 原创视频自动加B站水印
    )
    if actual_cover and os.path.exists(actual_cover):
        from bilibili_api.utils.picture import Picture
        meta_kwargs["cover"] = Picture().from_file(actual_cover)
    else:
        # cover 是必需参数，传空字符串让B站用默认
        meta_kwargs["cover"] = ""

    meta = VideoMeta(**meta_kwargs)

    # ── 上传线路选择 ──────────────────────────────────
    upload_lines = bili_cfg.get("upload_lines", "AUTO")
    # 线路优先级: AUTO 自动选最快, kodo 七牛云, bda2 百度, ws 网宿, qn 轻量
    LINES_FALLBACK = ["kodo", "bda2", "ws", "qn"]

    # ── 创建上传器(带线路) ─────────────────────────────
    def _create_uploader(line: str = None):
        uploader_kwargs = dict(
            pages=[page],
            meta=meta,
            credential=credential,
        )
        if cover_path and os.path.exists(cover_path):
            uploader_kwargs["cover"] = cover_path
        if line and line != "AUTO":
            try:
                from bilibili_api.video_uploader import Lines
                line_enum = getattr(Lines, line.upper(), None)
                if line_enum:
                    uploader_kwargs["line"] = line_enum
                    logger.info(f"   📡 使用上传线路: {line.upper()}")
            except (ImportError, AttributeError):
                pass  # bilibili_api 版本不支持 Lines, 用默认
        return VideoUploader(**uploader_kwargs)

    # ── 事件监听(带进度百分比) ─────────────────────────
    file_size_mb = os.path.getsize(video_path) / 1024 / 1024
    # 估算分块数(bilibili_api 默认 ~5MB/chunk)
    estimated_chunks = max(1, int(file_size_mb / 5))
    upload_state = {"chunk_count": 0, "start_time": 0}

    def _attach_events(uploader):
        """给上传器绑定事件监听"""
        upload_state["chunk_count"] = 0
        upload_state["start_time"] = time.time()

        @uploader.on(VideoUploaderEvents.PREUPLOAD.value)
        async def on_preupload(data):
            upload_state["start_time"] = time.time()
            logger.info(f"📤 开始上传: {os.path.basename(video_path)} ({file_size_mb:.1f}MB)")

        @uploader.on(VideoUploaderEvents.PRE_PAGE.value)
        async def on_pre_page(data):
            logger.info(f"📦 准备上传分P...")

        @uploader.on(VideoUploaderEvents.AFTER_CHUNK.value)
        async def on_after_chunk(data):
            upload_state["chunk_count"] += 1
            cnt = upload_state["chunk_count"]
            pct = min(99, int(cnt / estimated_chunks * 100))
            elapsed = time.time() - upload_state["start_time"]
            speed = (cnt * 5) / elapsed if elapsed > 0 else 0
            if cnt % 5 == 0 or pct >= 95:
                logger.info(f"  ⏳ 上传进度: {pct}% ({cnt} chunks, {speed:.1f}MB/s)")

        @uploader.on(VideoUploaderEvents.PRE_COVER.value)
        async def on_pre_cover(data):
            logger.info("🖼️ 上传封面...")

        @uploader.on(VideoUploaderEvents.PRE_SUBMIT.value)
        async def on_pre_submit(data):
            logger.info("📋 提交稿件...")

        @uploader.on(VideoUploaderEvents.COMPLETED.value)
        async def on_completed(data):
            elapsed = time.time() - upload_state["start_time"]
            logger.info(f"✅ 上传完成！耗时 {elapsed:.0f}s, 平均 {file_size_mb/elapsed:.1f}MB/s")

        @uploader.on(VideoUploaderEvents.FAILED.value)
        async def on_failed(data):
            logger.error(f"❌ 上传失败: {data}")

    # ── 带重试+线路切换的上传执行 ────────────────────────
    logger.info(f"🎬 B站上传: 《{title}》")
    logger.info(f"   分区={tid}, 标签={tags}, 版权={'原创' if is_original else '转载'}")
    logger.info(f"   文件={file_size_mb:.1f}MB, 线路={upload_lines}")

    max_retries = 3
    last_error = None

    # 构建线路尝试列表
    if upload_lines == "AUTO":
        lines_to_try = ["AUTO"] + LINES_FALLBACK[:max_retries - 1]
    else:
        lines_to_try = [upload_lines] + [l for l in LINES_FALLBACK if l != upload_lines]
    lines_to_try = lines_to_try[:max_retries]

    for attempt, line in enumerate(lines_to_try):
        try:
            uploader = _create_uploader(line)
            _attach_events(uploader)

            # 超时控制: 按文件大小动态计算(至少5分钟, 每100MB加5分钟)
            timeout_sec = max(300, int(file_size_mb / 100 * 300) + 300)

            result = await asyncio.wait_for(
                uploader.start(),
                timeout=timeout_sec,
            )

            # ── 解析结果 ─────────────────────────────────
            if result and isinstance(result, dict):
                bvid = result.get("bvid", "")
                aid = result.get("aid", "")
                url = f"https://www.bilibili.com/video/{bvid}" if bvid else ""
                logger.info(f"🎉 发布成功! {url}")
                # 清理临时封面
                if temp_cover and os.path.exists(temp_cover):
                    os.remove(temp_cover)
                return {"bvid": bvid, "aid": aid, "url": url, "raw": result}
            else:
                last_error = f"提交结果异常: {result}"
                logger.warning(f"⚠️ {last_error}")

        except asyncio.TimeoutError:
            last_error = f"上传超时 ({timeout_sec}s)"
            logger.warning(f"⏰ 第 {attempt+1}/{max_retries} 次上传超时 (线路={line})")
        except Exception as e:
            last_error = str(e)
            logger.warning(f"❌ 第 {attempt+1}/{max_retries} 次上传失败 (线路={line}): {e}")

        if attempt < len(lines_to_try) - 1:
            next_line = lines_to_try[attempt + 1]
            wait = (attempt + 1) * 5
            logger.info(f"   🔄 {wait}s 后切换线路 {next_line} 重试...")
            await asyncio.sleep(wait)

    # ── 解析结果 ─────────────────────────────────────
    # 清理临时封面
    if temp_cover and os.path.exists(temp_cover):
        os.remove(temp_cover)
    raise RuntimeError(f"B站上传失败(重试 {max_retries} 次): {last_error}")


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


# ═══════════════════════════════════════════════════════
#  biliup CLI 上传（开源 Rust 实现，更快更稳定）
# ═══════════════════════════════════════════════════════

def _verify_video_file(video_path: str) -> dict:
    """上传前用 ffprobe 校验视频文件完整性，返回文件信息"""
    import subprocess
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"视频文件不存在: {video_path}")

    file_size = os.path.getsize(video_path)
    if file_size < 1024:  # 小于 1KB 肯定不是有效视频
        raise ValueError(f"视频文件太小({file_size} bytes)，可能损坏: {video_path}")

    try:
        probe_cmd = [
            "ffprobe", "-v", "quiet", "-print_format", "json",
            "-show_format", "-show_streams", video_path
        ]
        probe = subprocess.run(probe_cmd, capture_output=True, text=True, timeout=30)
        if probe.returncode != 0:
            raise ValueError(f"ffprobe 校验失败: {probe.stderr[:200]}")

        info = json.loads(probe.stdout)
        duration = float(info.get("format", {}).get("duration", 0))
        has_video = any(s["codec_type"] == "video" for s in info.get("streams", []))
        has_audio = any(s["codec_type"] == "audio" for s in info.get("streams", []))

        if not has_video:
            raise ValueError("视频文件无视频流")
        if duration < 1:
            raise ValueError(f"视频时长异常: {duration:.1f}s")

        return {
            "file_size_mb": file_size / 1024 / 1024,
            "duration": duration,
            "has_audio": has_audio,
        }
    except (subprocess.TimeoutExpired, FileNotFoundError):
        # ffprobe 不可用时跳过校验
        logger.warning("⚠️ ffprobe 不可用，跳过视频校验")
        return {"file_size_mb": file_size / 1024 / 1024, "duration": 0, "has_audio": True}


def _check_cookie_freshness(cookie_file: Path) -> bool:
    """检查 cookie 是否可能过期（超过 25 天未更新）"""
    if not cookie_file.exists():
        return False
    age_days = (time.time() - cookie_file.stat().st_mtime) / 86400
    if age_days > 25:
        logger.warning(f"⚠️ Cookie 已 {age_days:.0f} 天未更新，可能已过期！"
                       f"\n   建议重新登录: biliup login")
        return False
    return True


def upload_via_biliup(
    config: dict,
    video_path: str,
    title: str = "",
    desc: str = "",
    tags: list[str] = None,
    cover_path: str = None,
    source_url: str = "",
    tid: int = None,
    max_retries: int = 3,
) -> dict:
    """
    使用 biliup CLI (Rust) 上传视频到B站。

    优点：上传速度更快、并发分块、更稳定。
    需要 cookies.json 在项目根目录（从 .bili_credential.json 自动转换）。

    特性：
    - 上传前 ffprobe 校验视频完整性
    - 根据文件大小自动调整超时（100MB/min 基准）
    - 指数退避重试（最多 max_retries 次）
    - Cookie 过期自动检测提醒
    - 耗时统计

    返回:
        {"bvid": "BV...", "aid": 12345, "url": "...", "upload_seconds": N}
    """
    import subprocess

    bili_cfg = config.get("bilibili", {})
    project_root = Path(__file__).parent.parent
    cookie_file = project_root / "cookies.json"

    # ── 上传前校验 ──
    video_info = _verify_video_file(video_path)
    file_size_mb = video_info["file_size_mb"]

    # 确保 cookies.json 存在（从 .bili_credential.json 自动转换）
    _ensure_biliup_cookies(project_root, cookie_file)
    _check_cookie_freshness(cookie_file)

    # 参数处理
    if tags is None:
        tags = bili_cfg.get("default_tags", ["日剧", "预告片", "2026新番", "Netflix"])
    tag_str = ",".join(t[:20] for t in tags[:10])

    if not title:
        title = "新视频"
    title = title[:80]
    desc = (desc or "")[:2000]

    if tid is None:
        tid = bili_cfg.get("tid", 183)

    copyright_type = bili_cfg.get("copyright", 2)
    source = source_url or bili_cfg.get("default_source", "YouTube")

    # 自动截取封面
    actual_cover = cover_path
    temp_cover = None
    if not actual_cover or not os.path.exists(actual_cover):
        temp_cover = video_path + ".cover.jpg"
        subprocess.run(
            ["ffmpeg", "-y", "-ss", "10", "-i", video_path,
             "-vframes", "1", "-q:v", "2", "-update", "1", temp_cover],
            capture_output=True,
        )
        if os.path.exists(temp_cover):
            actual_cover = temp_cover
            logger.info("🖼️ 自动从视频截取封面")

    # 构建 biliup 命令
    cmd = [
        "biliup", "upload",
        "--user-cookie", str(cookie_file),
        "--copyright", str(copyright_type),
        "--tid", str(tid),
        "--title", title,
        "--tag", tag_str,
    ]
    if desc:
        cmd.extend(["--desc", desc])
    if copyright_type == 2 and source:
        cmd.extend(["--source", source])
    if actual_cover and os.path.exists(actual_cover):
        cmd.extend(["--cover", actual_cover])
    cmd.append(video_path)

    # 动态超时：100MB/min 基准，最低 300s，最高 7200s
    timeout = max(300, min(7200, int(file_size_mb / 100 * 60) + 300))

    logger.info(f"🚀 biliup 上传: 《{title}》({file_size_mb:.1f}MB)")
    logger.info(f"   分区={tid}, 标签={tag_str}, 版权={'原创' if copyright_type == 1 else '转载'}")
    if video_info.get("duration"):
        logger.info(f"   视频时长: {video_info['duration']:.0f}s, 超时: {timeout}s")

    # ── 指数退避重试上传 ──
    last_error = None
    for attempt in range(max_retries):
        t_start = time.time()
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            elapsed = time.time() - t_start

            if result.returncode == 0:
                # 解析输出，提取 BV 号
                output = result.stdout + result.stderr
                logger.info(f"📋 biliup 输出:\n{output}")

                bvid = ""
                aid = ""
                bv_match = re.search(r'(BV[\w]+)', output)
                if bv_match:
                    bvid = bv_match.group(1)
                aid_match = re.search(r'"aid"\s*:\s*(\d+)', output)
                if aid_match:
                    aid = int(aid_match.group(1))

                url = f"https://www.bilibili.com/video/{bvid}" if bvid else ""
                speed = file_size_mb / elapsed * 60 if elapsed > 0 else 0

                # 清理临时封面
                if temp_cover and os.path.exists(temp_cover):
                    os.unlink(temp_cover)

                if bvid:
                    logger.info(f"🎉 发布成功! {url} (耗时 {elapsed:.0f}s, {speed:.0f}MB/min)")
                else:
                    logger.warning(f"⚠️ 上传完成但未解析到 BV 号 (耗时 {elapsed:.0f}s)")

                return {"bvid": bvid, "aid": aid, "url": url,
                        "raw_output": output, "upload_seconds": round(elapsed, 1)}

            # 上传失败
            error_output = result.stderr + result.stdout
            last_error = error_output[:500]

            # 检测 cookie 过期特征
            cookie_expired = any(kw in error_output.lower() for kw in
                                 ["expired", "login", "cookie", "credential", "auth", "未登录", "过期"])
            if cookie_expired:
                logger.error(f"🔑 Cookie 可能已过期! 请重新登录:\n   biliup login")
                raise RuntimeError(f"Cookie 过期: {last_error}")

            if attempt < max_retries - 1:
                wait = (2 ** attempt) * 10  # 10s, 20s, 40s
                logger.warning(f"⚠️ biliup 上传失败 (第 {attempt+1}/{max_retries} 次), "
                               f"{wait}s 后重试...\n   错误: {last_error[:200]}")
                time.sleep(wait)
            else:
                logger.error(f"❌ biliup 上传失败 ({max_retries} 次重试均失败):\n{last_error}")

        except subprocess.TimeoutExpired:
            elapsed = time.time() - t_start
            last_error = f"上传超时({timeout}s, 文件 {file_size_mb:.0f}MB)"
            if attempt < max_retries - 1:
                wait = (2 ** attempt) * 15
                logger.warning(f"⏰ {last_error}, {wait}s 后重试 ({attempt+1}/{max_retries})...")
                time.sleep(wait)
                timeout = int(timeout * 1.5)  # 超时时加大下次超时时间
            else:
                logger.error(f"❌ {last_error} ({max_retries} 次均超时)")

    # 清理临时封面
    if temp_cover and os.path.exists(temp_cover):
        os.unlink(temp_cover)

    raise RuntimeError(f"biliup 上传失败 ({max_retries} 次): {last_error}")


def _ensure_biliup_cookies(project_root: Path, cookie_file: Path):
    """从 .bili_credential.json 自动生成 biliup 的 cookies.json"""
    cred_file = project_root / ".bili_credential.json"

    if cookie_file.exists():
        # 如果 credential 更新了，同步过来
        if cred_file.exists():
            cred_mtime = cred_file.stat().st_mtime
            cookie_mtime = cookie_file.stat().st_mtime
            if cred_mtime <= cookie_mtime:
                return  # cookies.json 已是最新

    if not cred_file.exists():
        raise FileNotFoundError(
            "未找到B站凭证！请先运行扫码登录:\n"
            "  python -c \"from uploader import qrcode_login; qrcode_login()\""
        )

    cred = json.loads(cred_file.read_text())
    cookies = {
        "SESSDATA": cred.get("SESSDATA", ""),
        "bili_jct": cred.get("bili_jct", ""),
        "DedeUserID": cred.get("DedeUserID", cred.get("dedeuserid", "")),
        "buvid3": cred.get("buvid3", ""),
        "buvid4": cred.get("buvid4", ""),
        "ac_time_value": cred.get("ac_time_value", ""),
    }
    cookie_file.write_text(json.dumps(cookies, indent=2))
    logger.info(f"🔄 已从 .bili_credential.json 同步到 cookies.json")


# ═══════════════════════════════════════════════════════
#  上传后清理：删除大文件，保留元数据
# ═══════════════════════════════════════════════════════

def cleanup_run(run_dir: str, keep_metadata: bool = True) -> dict:
    """
    清理已上传的 run 目录，释放磁盘空间。

    策略：
    - 删除 source/ 下的原始视频和音频（最大的文件）
    - 删除 output/ 下的合成视频
    - 保留 run_info.json、subtitles.srt、script/、output/cover.jpg（元数据+封面）
    - 保留 output/publish_info.json

    参数:
        run_dir: runs/xxx 目录路径
        keep_metadata: 是否保留元数据文件（默认 True）

    返回:
        {"freed_mb": float, "deleted_files": int}
    """
    import shutil

    run_path = Path(run_dir)
    if not run_path.exists():
        return {"freed_mb": 0, "deleted_files": 0}

    freed_bytes = 0
    deleted_count = 0

    # 要保留的文件
    keep_patterns = {
        "run_info.json", "subtitles.srt", "publish_info.json", "cover.jpg",
    }
    keep_dirs = {"script"}  # 保留文案脚本

    # 删除 source/ 目录（原始下载的视频+音频，最占空间）
    source_dir = run_path / "source"
    if source_dir.exists():
        for f in source_dir.iterdir():
            if f.is_file():
                freed_bytes += f.stat().st_size
                f.unlink()
                deleted_count += 1
                logger.debug(f"  🗑️ 删除 {f.name}")
        # 删除空目录
        if not any(source_dir.iterdir()):
            source_dir.rmdir()

    # 删除 output/ 下的视频文件（保留封面和 publish_info）
    output_dir = run_path / "output"
    if output_dir.exists():
        for f in output_dir.iterdir():
            if f.is_file() and f.name not in keep_patterns:
                if f.suffix in (".mp4", ".mkv", ".avi", ".mov", ".flv", ".m4a", ".mp3"):
                    freed_bytes += f.stat().st_size
                    f.unlink()
                    deleted_count += 1
                    logger.debug(f"  🗑️ 删除 {f.name}")

    freed_mb = freed_bytes / 1024 / 1024
    if freed_mb > 0:
        logger.info(f"🧹 清理 {run_path.name}: 释放 {freed_mb:.1f}MB, 删除 {deleted_count} 个文件")

    return {"freed_mb": freed_mb, "deleted_files": deleted_count}


def cleanup_uploaded_runs(runs_dir: str = "runs", dry_run: bool = False) -> dict:
    """
    批量清理所有已上传成功的 run 目录。

    只清理 run_info.json 中 uploaded=True 的目录。

    参数:
        runs_dir: runs 根目录
        dry_run: True 则只报告不实际删除

    返回:
        {"total_freed_mb": float, "cleaned_runs": int}
    """
    runs_path = Path(runs_dir)
    total_freed = 0
    cleaned = 0

    for run_dir in sorted(runs_path.iterdir()):
        if not run_dir.is_dir():
            continue

        info_file = run_dir / "run_info.json"
        if not info_file.exists():
            continue

        try:
            info = json.loads(info_file.read_text())
        except Exception:
            continue

        if not info.get("uploaded"):
            continue

        if dry_run:
            # 计算可释放空间
            size = sum(
                f.stat().st_size for f in run_dir.rglob("*")
                if f.is_file() and f.suffix in (".mp4", ".mkv", ".m4a", ".mp3", ".mov")
            )
            logger.info(f"  [DRY] {run_dir.name}: 可释放 {size / 1024 / 1024:.1f}MB")
            total_freed += size / 1024 / 1024
            cleaned += 1
        else:
            result = cleanup_run(str(run_dir))
            total_freed += result["freed_mb"]
            if result["freed_mb"] > 0:
                cleaned += 1

    logger.info(f"🧹 清理完成: {cleaned} 个目录, 共释放 {total_freed:.1f}MB")
    return {"total_freed_mb": total_freed, "cleaned_runs": cleaned}
