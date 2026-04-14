"""今日头条/西瓜视频 上传模块 — Playwright persistent context + 视频发布

流程:
  1. 用 persistent context 保持登录态（首次需扫码）
  2. 打开 mp.toutiao.com/profile_v4/xigua/upload-video
  3. 上传视频文件 → 等待上传+处理完成 → 填写标题/描述/标签/封面 → 发布

依赖: pip install playwright && playwright install chromium
"""

import json
import logging
import os
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# 浏览器数据持久化目录
TOUTIAO_BROWSER_DIR = Path(__file__).parent.parent / ".toutiao_state" / "browser_data"


def _ensure_browser_dir():
    TOUTIAO_BROWSER_DIR.mkdir(parents=True, exist_ok=True)
    return str(TOUTIAO_BROWSER_DIR)


def _launch_context(playwright, headless=False):
    """启动 persistent context（自动保持登录态）"""
    return playwright.chromium.launch_persistent_context(
        _ensure_browser_dir(),
        headless=headless,
        viewport={"width": 1280, "height": 900},
        user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0.0.0 Safari/537.36"
        ),
    )


def _check_login(page) -> bool:
    """检查是否已登录（非登录页 + 无注册按钮）"""
    body = page.locator("body").text_content(timeout=5000) or ""
    # 如果页面同时包含"登录"和"注册"，说明在登录页
    if "登录" in body and "注册" in body:
        # 再看看是否有上传区域
        file_inputs = page.locator('input[type="file"]').count()
        if file_inputs > 0:
            return True  # 有上传控件说明已登录
        return False
    return True


def _do_login(page, timeout=120) -> bool:
    """引导扫码登录"""
    page.goto("https://mp.toutiao.com/auth/page/login", timeout=30000)
    time.sleep(3)

    print()
    print("=" * 50)
    print("📱 请使用【今日头条 App】或【抖音 App】扫码登录")
    print("=" * 50)
    print()

    start = time.time()
    while time.time() - start < timeout:
        cur = page.url
        if "login" not in cur and "auth" not in cur:
            print("✅ 头条登录成功!")
            return True
        time.sleep(2)

    print("❌ 登录超时")
    return False


def toutiao_login(headless=False) -> bool:
    """独立的头条登录入口"""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        context = _launch_context(p, headless=headless)
        page = context.new_page()
        result = _do_login(page, timeout=120)
        context.close()
        return result


def upload_to_toutiao(
    config: dict,
    video_path: str,
    title: str = "",
    desc: str = "",
    tags: list[str] = None,
    cover_path: str = None,
    headless: bool = False,
) -> dict:
    """
    上传视频到今日头条创作者平台（同步到西瓜视频）。

    参数:
        config: 配置字典
        video_path: 视频文件路径
        title: 视频标题（限30字）
        desc: 视频简介
        tags: 标签列表
        cover_path: 封面图片路径（可选）
        headless: 是否无头模式（首次登录必须 False）

    返回:
        {"success": True/False, "message": "...", "platform": "toutiao"}
    """
    from playwright.sync_api import sync_playwright

    if not os.path.exists(video_path):
        raise FileNotFoundError(f"视频文件不存在: {video_path}")

    logger.info(f"📤 头条上传: 《{title}》")
    tags = tags or []

    with sync_playwright() as p:
        context = _launch_context(p, headless=headless)
        page = context.new_page()

        try:
            # ── 1. 打开上传页 ──
            logger.info("   📄 打开发布页面...")
            page.goto(
                "https://mp.toutiao.com/profile_v4/xigua/upload-video",
                timeout=30000,
            )
            time.sleep(5)

            # ── 2. 检查登录 ──
            if not _check_login(page):
                logger.info("   📱 需要登录...")
                if not _do_login(page, timeout=120):
                    context.close()
                    return {"success": False, "message": "登录失败", "platform": "toutiao"}
                page.goto(
                    "https://mp.toutiao.com/profile_v4/xigua/upload-video",
                    timeout=30000,
                )
                time.sleep(5)

            # ── 3. 上传视频文件 ──
            logger.info("   📤 上传视频文件...")
            file_input = page.locator('input[type="file"]').first
            file_input.set_input_files(video_path)

            # ── 4. 等待上传+处理完成 ──
            logger.info("   ⏳ 等待视频上传...")
            _wait_for_upload(page, timeout=1800)

            # 上传完成后页面会展开编辑表单，等一下渲染
            time.sleep(5)

            # ── 5. 填写标题 ──
            if title:
                logger.info(f"   ✏️ 填写标题: {title[:30]}...")
                _fill_title(page, title[:30])

            # ── 6. 填写描述 ──
            if desc:
                logger.info("   ✏️ 填写描述...")
                _fill_desc(page, desc[:500])

            # ── 7. 上传封面 ──
            if cover_path and os.path.exists(cover_path):
                logger.info("   🖼️ 上传封面...")
                _upload_cover(page, cover_path)

            # ── 8. 截图留档 ──
            screenshot_dir = Path(__file__).parent.parent / ".toutiao_state"
            screenshot_dir.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(screenshot_dir / "before_publish.png"))

            # ── 9. 点击发布 ──
            logger.info("   🚀 提交发布...")
            result = _click_publish(page)

            context.close()
            return result

        except Exception as e:
            logger.error(f"❌ 头条上传异常: {e}")
            try:
                ss = Path(__file__).parent.parent / ".toutiao_state" / "error_screenshot.png"
                page.screenshot(path=str(ss))
                logger.info(f"   📸 错误截图: {ss}")
            except Exception:
                pass
            context.close()
            return {"success": False, "message": str(e), "platform": "toutiao"}


def _wait_for_upload(page, timeout=1800):
    """等待视频上传+处理完成"""
    start = time.time()
    last_log = 0

    while time.time() - start < timeout:
        try:
            # 标志1: 标题输入框出现（说明上传完成，编辑表单展开了）
            title_input = page.locator(
                'input[placeholder*="标题"], '
                'textarea[placeholder*="标题"], '
                '[class*="title"] input, '
                '[class*="title"] textarea'
            ).first
            if title_input.is_visible(timeout=1000):
                logger.info("   ✅ 视频上传完成（编辑表单已展开）")
                return

            # 标志2: 发布按钮出现
            pub = page.locator('button:has-text("发布")').first
            if pub.is_visible(timeout=500):
                logger.info("   ✅ 视频上传完成（发布按钮可见）")
                return

            # 打印进度
            now = time.time()
            if now - last_log > 30:
                # 尝试读取进度
                try:
                    progress_text = page.locator('[class*="progress"], [class*="percent"]').first
                    ptext = progress_text.text_content(timeout=1000)
                    logger.info(f"   ⏳ 上传中... {ptext}")
                except Exception:
                    elapsed = int(now - start)
                    logger.info(f"   ⏳ 上传中... ({elapsed}s)")
                last_log = now

        except Exception:
            pass

        time.sleep(3)

    logger.warning(f"⚠️ 等待上传超时 ({timeout}s)，继续尝试...")


def _fill_title(page, title: str):
    """填写标题"""
    try:
        # 头条的标题框
        selectors = [
            'input[placeholder*="标题"]',
            'textarea[placeholder*="标题"]',
            '[class*="title-input"] input',
            '[class*="title-input"] textarea',
            '[class*="titleInput"] input',
        ]
        for sel in selectors:
            inp = page.locator(sel).first
            try:
                if inp.is_visible(timeout=2000):
                    inp.click()
                    inp.fill(title)
                    logger.info(f"   ✅ 标题已填写")
                    return
            except Exception:
                continue
        logger.warning("   ⚠️ 未找到标题输入框")
    except Exception as e:
        logger.warning(f"   ⚠️ 填写标题失败: {e}")


def _fill_desc(page, desc: str):
    """填写描述"""
    try:
        selectors = [
            'textarea[placeholder*="描述"]',
            'textarea[placeholder*="简介"]',
            '[class*="desc"] textarea',
            '[class*="description"] textarea',
            '[contenteditable="true"]',
        ]
        for sel in selectors:
            el = page.locator(sel).first
            try:
                if el.is_visible(timeout=2000):
                    el.click()
                    if sel == '[contenteditable="true"]':
                        page.keyboard.press("Control+a")
                        page.keyboard.press("Backspace")
                        page.keyboard.type(desc, delay=10)
                    else:
                        el.fill(desc)
                    logger.info("   ✅ 描述已填写")
                    return
            except Exception:
                continue
        logger.debug("   跳过描述（未找到输入框）")
    except Exception as e:
        logger.debug(f"   跳过描述: {e}")


def _upload_cover(page, cover_path: str):
    """上传自定义封面"""
    try:
        # 点击"上传封面"或"自定义封面"按钮
        cover_triggers = [
            'text=上传封面',
            'text=自定义封面',
            'text=更换封面',
            '[class*="cover"] [class*="upload"]',
        ]
        for sel in cover_triggers:
            try:
                btn = page.locator(sel).first
                if btn.is_visible(timeout=2000):
                    btn.click()
                    time.sleep(1)
                    break
            except Exception:
                continue

        # 找图片上传 input
        cover_input = page.locator('input[type="file"][accept*="image"]').first
        cover_input.set_input_files(cover_path)
        time.sleep(3)

        # 确认封面
        for confirm_text in ["确定", "确认", "完成"]:
            try:
                btn = page.locator(f'button:has-text("{confirm_text}")').first
                if btn.is_visible(timeout=2000):
                    btn.click()
                    time.sleep(1)
                    logger.info("   ✅ 封面已上传")
                    return
            except Exception:
                continue

    except Exception as e:
        logger.debug(f"   跳过封面上传: {e}")


def _click_publish(page) -> dict:
    """点击发布按钮"""
    try:
        pub = page.locator('button:has-text("发布")').first
        pub.wait_for(state="visible", timeout=10000)
        pub.click()
        time.sleep(8)

        # 检查结果
        url = page.url
        body = page.locator("body").text_content(timeout=5000) or ""

        if "发布成功" in body or "upload-video" not in url:
            logger.info("✅ 头条视频发布成功！")
            return {"success": True, "message": "发布成功", "platform": "toutiao"}

        # 检查是否有错误提示
        error_text = ""
        try:
            for sel in ['[class*="error"]', '[class*="toast"]', '[class*="message"]']:
                el = page.locator(sel).first
                if el.is_visible(timeout=1000):
                    error_text = el.text_content(timeout=1000) or ""
                    if error_text.strip():
                        break
        except Exception:
            pass

        if error_text:
            return {"success": False, "message": error_text.strip(), "platform": "toutiao"}

        return {"success": True, "message": "已提交（请到后台确认）", "platform": "toutiao"}

    except Exception as e:
        return {"success": False, "message": str(e), "platform": "toutiao"}
