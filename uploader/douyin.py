"""抖音创作者平台 上传模块 — Playwright persistent context + 视频发布

流程:
  1. 用 persistent context 保持登录态（首次需扫码+短信验证）
  2. 打开 creator.douyin.com/creator-micro/content/upload
  3. 上传视频文件 → 填写标题+描述+话题 → 发布

依赖: pip install playwright && playwright install chromium
"""

import logging
import os
import time
from pathlib import Path

logger = logging.getLogger(__name__)

DOUYIN_BROWSER_DIR = Path(__file__).parent.parent / ".douyin_state" / "browser_data"


def _ensure_browser_dir():
    DOUYIN_BROWSER_DIR.mkdir(parents=True, exist_ok=True)
    return str(DOUYIN_BROWSER_DIR)


def _launch_context(playwright, headless=False):
    """启动 persistent context"""
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
    """检查是否已登录（上传页有 file input 即为已登录）"""
    try:
        file_count = page.locator('input[type="file"]').count()
        if file_count > 0:
            return True
        # 检查是否有身份验证弹窗
        body = page.locator("body").text_content(timeout=3000) or ""
        if "身份验证" in body:
            logger.warning("   ⚠️ 需要身份验证（短信验证码），请手动完成")
            return False
        if "登录" in body and "上传" not in body:
            return False
    except Exception:
        pass
    return False


def douyin_login(headless=False) -> bool:
    """独立的抖音登录入口"""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        context = _launch_context(p, headless=headless)
        page = context.new_page()
        page.goto("https://creator.douyin.com/", timeout=30000)
        time.sleep(3)

        print()
        print("=" * 50)
        print("📱 请使用【抖音 App】扫描浏览器中的二维码登录")
        print("   如果弹出身份验证，请完成短信验证")
        print("=" * 50)
        print()

        start = time.time()
        while time.time() - start < 120:
            try:
                page.goto(
                    "https://creator.douyin.com/creator-micro/content/upload",
                    timeout=15000,
                )
                time.sleep(5)
                if _check_login(page):
                    print("✅ 抖音登录成功!")
                    context.close()
                    return True
            except Exception:
                pass
            time.sleep(5)

        print("❌ 登录超时")
        context.close()
        return False


def upload_to_douyin(
    config: dict,
    video_path: str,
    title: str = "",
    desc: str = "",
    tags: list[str] = None,
    cover_path: str = None,
    headless: bool = False,
) -> dict:
    """
    上传视频到抖音创作者平台。

    返回: {"success": True/False, "message": "...", "platform": "douyin"}
    """
    from playwright.sync_api import sync_playwright

    if not os.path.exists(video_path):
        raise FileNotFoundError(f"视频文件不存在: {video_path}")

    logger.info(f"📤 抖音上传: 《{title}》")
    tags = tags or []

    with sync_playwright() as p:
        context = _launch_context(p, headless=headless)
        page = context.new_page()

        try:
            # ── 1. 打开上传页 ──
            logger.info("   📄 打开上传页面...")
            page.goto(
                "https://creator.douyin.com/creator-micro/content/upload",
                timeout=60000,
                wait_until="networkidle",
            )
            time.sleep(15)  # 抖音加载较慢

            # ── 2. 处理草稿提示 ──
            try:
                discard = page.locator('text=放弃').first
                if discard.is_visible(timeout=3000):
                    logger.info("   🗑️ 放弃上次未发布的草稿")
                    discard.click()
                    time.sleep(3)
            except Exception:
                pass

            # ── 3. 检查登录 ──
            if not _check_login(page):
                logger.error("   ❌ 未登录或需要身份验证，请先运行 douyin_login()")
                context.close()
                return {
                    "success": False,
                    "message": "未登录，请先完成扫码+身份验证",
                    "platform": "douyin",
                }

            # ── 3. 上传视频文件 ──
            logger.info("   📤 上传视频文件...")
            file_input = page.locator('input[type="file"]').first
            file_input.set_input_files(video_path)

            # ── 4. 等待上传完成 + 表单展开 ──
            logger.info("   ⏳ 等待视频上传...")
            _wait_for_upload(page, timeout=600)
            time.sleep(5)

            # ── 5. 填写标题 ──
            if title:
                logger.info("   ✏️ 填写标题...")
                _fill_title(page, title)

            # ── 6. 填写描述+话题 ──
            if desc or tags:
                logger.info("   ✏️ 填写描述+话题...")
                _fill_description(page, desc, tags)

            # ── 7. 上传封面 ──
            if cover_path and os.path.exists(cover_path):
                logger.info("   🖼️ 上传封面...")
                _upload_cover(page, cover_path)

            time.sleep(3)

            # ── 8. 截图 ──
            ss_dir = Path(__file__).parent.parent / ".douyin_state"
            ss_dir.mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(ss_dir / "before_publish.png"))

            # ── 9. 发布 ──
            logger.info("   🚀 提交发布...")
            result = _click_publish(page)

            context.close()
            return result

        except Exception as e:
            logger.error(f"❌ 抖音上传异常: {e}")
            try:
                ss = Path(__file__).parent.parent / ".douyin_state" / "error_screenshot.png"
                page.screenshot(path=str(ss))
                logger.info(f"   📸 错误截图: {ss}")
            except Exception:
                pass
            context.close()
            return {"success": False, "message": str(e), "platform": "douyin"}


def _wait_for_upload(page, timeout=600):
    """等待视频上传 100% 完成 + 编辑表单展开"""
    start = time.time()
    last_log = 0
    form_ready = False

    # 阶段1：等表单展开
    while time.time() - start < timeout:
        try:
            title_input = page.locator('input[placeholder*="填写作品标题"]').first
            if title_input.is_visible(timeout=1000):
                form_ready = True
                break

            pub = page.locator('button:has-text("发布")').first
            if pub.is_visible(timeout=500):
                form_ready = True
                break

            now = time.time()
            if now - last_log > 30:
                elapsed = int(now - start)
                logger.info(f"   ⏳ 上传中... ({elapsed}s)")
                last_log = now
        except Exception:
            pass
        time.sleep(3)

    if not form_ready:
        logger.warning(f"⚠️ 等待表单超时 ({timeout}s)")
        return

    logger.info("   📝 表单已展开，等待上传 100% 完成...")

    # 阶段2：等上传进度到 100%（取消上传按钮消失 或 进度100%）
    upload_start = time.time()
    while time.time() - upload_start < 300:
        try:
            body = page.locator("body").inner_text(timeout=3000) or ""

            # 如果"取消上传"消失了，说明上传完成
            cancel = page.locator('text=取消上传')
            if cancel.count() == 0 or not cancel.first.is_visible(timeout=500):
                logger.info("   ✅ 视频上传 100% 完成")
                return

            # 或者显示"重新上传"
            if "重新上传" in body or "上传完成" in body:
                logger.info("   ✅ 视频上传完成")
                return

            # 打印进度
            now = time.time()
            if now - last_log > 10:
                # 尝试读取进度百分比
                try:
                    pct_text = page.locator('text=/\\d+%/').first.text_content(timeout=500) or ""
                    logger.info(f"   ⏳ 上传进度: {pct_text}")
                except Exception:
                    logger.info("   ⏳ 等待上传完成...")
                last_log = now

        except Exception:
            pass
        time.sleep(2)

    logger.warning("⚠️ 等待上传100%超时，继续发布...")


def _fill_title(page, title: str):
    """填写作品标题（30字限制）"""
    try:
        title_input = page.locator('input[placeholder*="填写作品标题"]').first
        title_input.wait_for(state="visible", timeout=10000)
        title_input.click()
        title_input.fill(title[:30])  # 抖音标题限30字
        logger.info(f"   ✅ 标题已填写: {title[:30]}")
    except Exception as e:
        logger.warning(f"   ⚠️ 填写标题失败: {e}")


def _fill_description(page, desc: str, tags: list[str]):
    """填写描述+话题（在 contenteditable 编辑器中）"""
    try:
        editor = page.locator(
            '[contenteditable="true"].editor-kit-container, '
            '[contenteditable="true"].zone-container'
        ).first
        editor.wait_for(state="visible", timeout=10000)
        editor.click()

        # 组合描述文本
        parts = []
        if desc:
            parts.append(desc)
        if tags:
            parts.append(" ".join(f"#{t}" for t in tags[:5]))

        full_text = "\n".join(parts)
        # 限制 1000 字
        full_text = full_text[:1000]

        page.keyboard.type(full_text, delay=15)
        logger.info(f"   ✅ 描述已填写 ({len(full_text)}字)")

    except Exception as e:
        logger.warning(f"   ⚠️ 填写描述失败: {e}")


def _upload_cover(page, cover_path: str):
    """上传封面 — 通过隐藏 file input 直接设置，避免触发封面编辑弹窗"""
    try:
        # 方案1: 直接找图片 file input（不点"选择封面"按钮，避免弹窗）
        cover_inputs = page.locator('input[type="file"][accept*="image"]')
        if cover_inputs.count() > 0:
            cover_inputs.first.set_input_files(cover_path)
            time.sleep(5)

            # 如果弹出了封面编辑弹窗，关闭它
            _close_cover_dialog(page)
            logger.info("   ✅ 封面已上传")
            return

        # 方案2: 没有图片 input，尝试点按钮触发
        for sel in ['text=选择封面', 'text=上传封面', 'text=更换封面']:
            try:
                btn = page.locator(sel).first
                if btn.is_visible(timeout=2000):
                    btn.click()
                    time.sleep(3)

                    # 找图片 file input
                    cover_input = page.locator('input[type="file"][accept*="image"]').first
                    if cover_input.count() > 0:
                        cover_input.set_input_files(cover_path)
                        time.sleep(5)

                    _close_cover_dialog(page)
                    logger.info("   ✅ 封面已上传")
                    return
            except Exception:
                continue

    except Exception as e:
        logger.debug(f"   跳过封面: {e}")
    finally:
        # 确保任何封面弹窗都关闭了
        _close_cover_dialog(page)


def _close_cover_dialog(page):
    """关闭封面编辑弹窗（如果存在）"""
    try:
        # 封面弹窗里有"完成"按钮
        for attempt in range(3):
            # 检查是否有封面编辑弹窗的特征元素
            dialog_markers = [
                'text=设置横封面', 'text=设置竖封面', 'text=AI封面',
                'text=封面检测', 'text=生成中'
            ]
            is_cover_dialog = False
            for marker in dialog_markers:
                try:
                    el = page.locator(marker).first
                    if el.is_visible(timeout=1000):
                        is_cover_dialog = True
                        break
                except Exception:
                    continue

            if not is_cover_dialog:
                return  # 没有弹窗，退出

            # 找"完成"按钮关闭弹窗
            try:
                done_btn = page.locator('button:has-text("完成")').first
                if done_btn.is_visible(timeout=2000):
                    done_btn.click()
                    time.sleep(3)
                    logger.info("   🖼️ 关闭封面编辑弹窗")
                    continue  # 再检查一遍是否真的关了
            except Exception:
                pass

            # 试试 ESC 关闭
            try:
                page.keyboard.press("Escape")
                time.sleep(2)
            except Exception:
                pass

    except Exception:
        pass


def _click_publish(page) -> dict:
    """点击发布按钮，处理可能的短信验证码弹窗"""
    try:
        # 先确保没有封面弹窗遮挡
        _close_cover_dialog(page)
        time.sleep(1)

        # 关闭其他可能的弹窗
        for dismiss_text in ["关闭", "我知道了", "稍后再说", "跳过", "取消"]:
            try:
                btn = page.locator(f'button:has-text("{dismiss_text}")').first
                if btn.is_visible(timeout=1000):
                    btn.click()
                    time.sleep(1)
            except Exception:
                pass

        # 滚动到底部确保发布按钮可见
        page.keyboard.press("End")
        time.sleep(1)

        # 找到真正的发布按钮（排除"高清发布"）
        publish_btns = page.locator('button:has-text("发布")').all()
        target_btn = None
        for btn in publish_btns:
            try:
                text = btn.text_content(timeout=500) or ""
                if text.strip() == "发布" and btn.is_visible(timeout=500):
                    target_btn = btn
                    break
            except Exception:
                continue

        if not target_btn:
            # 截图看看当前什么状态
            ss = Path(__file__).parent.parent / ".douyin_state" / "no_publish_btn.png"
            page.screenshot(path=str(ss))
            return {"success": False, "message": "找不到发布按钮", "platform": "douyin"}

        target_btn.click()
        logger.info("   🔘 已点击发布按钮")
        time.sleep(5)

        # 截图记录点击后的状态
        ss_dir = Path(__file__).parent.parent / ".douyin_state"
        page.screenshot(path=str(ss_dir / "after_publish.png"))

        # 检查是否弹出短信验证码弹窗
        sms_handled = _handle_sms_verification(page)

        # 等待发布完成（最多90秒）
        start = time.time()
        while time.time() - start < 90:
            url = page.url

            # URL 跳转到作品管理页 = 发布成功
            if "manage" in url or "content/manage" in url:
                logger.info("✅ 抖音视频发布成功！（跳转到作品管理）")
                return {"success": True, "message": "发布成功", "platform": "douyin"}

            # URL 跳转离开 upload 页（非封面弹窗导致的 URL 变化）
            if "upload" not in url and "creator" in url:
                logger.info("✅ 抖音视频发布成功！")
                return {"success": True, "message": "发布成功", "platform": "douyin"}

            # 检查 toast 提示
            try:
                body = page.locator("body").text_content(timeout=2000) or ""
                if "发布成功" in body:
                    logger.info("✅ 抖音视频发布成功！")
                    return {"success": True, "message": "发布成功", "platform": "douyin"}
                # 检查发布失败提示
                if "发布失败" in body or "发送失败" in body:
                    logger.error("❌ 抖音发布失败")
                    page.screenshot(path=str(ss_dir / "publish_failed.png"))
                    return {"success": False, "message": "发布失败（页面提示）", "platform": "douyin"}
            except Exception:
                pass

            time.sleep(3)

        # 超时 — 再截图确认
        page.screenshot(path=str(ss_dir / "publish_timeout.png"))
        current_url = page.url
        logger.warning(f"   ⚠️ 等待发布确认超时，当前URL: {current_url}")

        # 如果还在 upload 页面，说明没发出去
        if "upload" in current_url:
            return {"success": False, "message": "发布超时，仍在上传页（可能被弹窗阻挡）", "platform": "douyin"}

        return {"success": True, "message": "已点击发布（请到后台确认）", "platform": "douyin"}

    except Exception as e:
        return {"success": False, "message": str(e), "platform": "douyin"}


def _handle_sms_verification(page, timeout=120) -> bool:
    """处理发布时的短信验证码弹窗，等待用户手动完成验证"""
    try:
        # 检查是否有验证码弹窗（逐个检查不同文本）
        sms_keywords = ['接收短信验证码', '短信验证码', '请输入验证码', '输入验证码']
        found = False
        for kw in sms_keywords:
            try:
                el = page.locator(f'text={kw}').first
                if el.is_visible(timeout=2000):
                    found = True
                    break
            except Exception:
                continue

        if not found:
            return False

        print()
        print("=" * 50)
        print("📱 抖音需要短信验证码确认发布")
        print("   请在浏览器弹窗中：")
        print("   1. 点击「获取验证码」")
        print("   2. 输入收到的短信验证码")
        print("   3. 点击「验证」")
        print(f"   等待中... ({timeout}秒)")
        print("=" * 50)
        print()

        logger.info("   📱 检测到短信验证弹窗，等待手动验证...")

        start = time.time()
        while time.time() - start < timeout:
            # 弹窗消失 = 验证完成
            still_visible = False
            for kw in sms_keywords:
                try:
                    el = page.locator(f'text={kw}').first
                    if el.is_visible(timeout=1000):
                        still_visible = True
                        break
                except Exception:
                    continue

            if not still_visible:
                logger.info("   ✅ 短信验证完成！")
                time.sleep(3)
                return True

            time.sleep(3)

        logger.warning("   ⚠️ 等待验证超时")
        return False

    except Exception:
        return False
