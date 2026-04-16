#!/usr/bin/env python3
"""
通过 Chrome DevTools Protocol 导出 YouTube cookies。
完全不依赖 Keychain，远程可用。

前提：Chrome 需要以 --remote-debugging-port 启动。
本脚本会先检查是否已有 debug 端口，没有的话用 open 命令启动新实例。
"""
import http.client
import json
import os
import subprocess
import sys
import time

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_FILE = os.path.join(PROJECT_ROOT, "www.youtube.com_cookies.txt")
DEBUG_PORT = 9222


def check_debug_port():
    """检查 Chrome debug 端口是否已开启"""
    try:
        conn = http.client.HTTPConnection("127.0.0.1", DEBUG_PORT, timeout=3)
        conn.request("GET", "/json/version")
        resp = conn.getresponse()
        data = json.loads(resp.read())
        conn.close()
        return True
    except Exception:
        return False


def start_chrome_debug():
    """启动带 debug 端口的 Chrome"""
    # macOS
    chrome_path = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    if not os.path.exists(chrome_path):
        print("❌ Chrome 未安装")
        return False

    # 检查 Chrome 是否正在运行
    result = subprocess.run(["pgrep", "-f", "Google Chrome"], capture_output=True)
    chrome_running = result.returncode == 0

    if chrome_running:
        print("⚠️ Chrome 正在运行但没有 debug 端口")
        print("   需要重启 Chrome 以启用 debug 模式...")
        # 用 AppleScript 优雅关闭（保留 tabs）
        subprocess.run([
            "osascript", "-e",
            'tell application "Google Chrome" to quit'
        ], timeout=10)
        time.sleep(3)

    # 启动带 debug 端口的 Chrome
    print(f"🚀 启动 Chrome (debug port: {DEBUG_PORT})...")
    subprocess.Popen([
        chrome_path,
        f"--remote-debugging-port={DEBUG_PORT}",
        "--no-first-run",
        "--no-default-browser-check",
        "--restore-last-session",  # 恢复之前的 tabs
    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # 等待启动
    for i in range(15):
        time.sleep(2)
        if check_debug_port():
            print("✅ Chrome debug 端口已就绪")
            return True
        print(f"   等待 Chrome 启动... ({i+1}/15)")

    print("❌ Chrome 启动超时")
    return False


def get_cookies_via_cdp():
    """通过 CDP 获取 cookies"""
    # 获取 tab 列表
    conn = http.client.HTTPConnection("127.0.0.1", DEBUG_PORT, timeout=10)
    conn.request("GET", "/json")
    tabs = json.loads(conn.getresponse().read())
    conn.close()

    if not tabs:
        print("❌ 没有可用的 Chrome tab")
        return []

    ws_url = tabs[0].get("webSocketDebuggerUrl", "")
    if not ws_url:
        print("❌ 无法获取 WebSocket URL")
        return []

    print(f"📡 连接 CDP: {ws_url}")

    # 用 websockets 库通过 CDP 获取 cookies
    # 先确保 websockets 可用
    try:
        import websockets
    except ImportError:
        print("📦 安装 websockets...")
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "websockets", "-q",
             "-i", "https://pypi.tuna.tsinghua.edu.cn/simple"],
            timeout=30,
        )
        import websockets

    import asyncio

    async def _fetch():
        async with websockets.connect(ws_url) as ws:
            # 发送 Network.getAllCookies
            await ws.send(json.dumps({
                "id": 1,
                "method": "Network.getAllCookies"
            }))
            resp = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
            return resp.get("result", {}).get("cookies", [])

    return asyncio.run(_fetch())


def main():
    print("🍪 Chrome CDP Cookies 导出工具")
    print("=" * 50)

    # 检查/启动 debug 端口
    if not check_debug_port():
        print("Chrome debug 端口未开启，尝试启动...")
        if not start_chrome_debug():
            sys.exit(1)

    # 获取 cookies
    all_cookies = get_cookies_via_cdp()
    print(f"📊 获取到 {len(all_cookies)} 个 cookies")

    # 过滤 YouTube + Google
    yt_cookies = [
        c for c in all_cookies
        if ".youtube.com" in c.get("domain", "")
        or ".google.com" in c.get("domain", "")
    ]
    print(f"🎯 YouTube/Google cookies: {len(yt_cookies)}")

    if not yt_cookies:
        print("❌ 没有找到 YouTube cookies，请确保 Chrome 已登录 YouTube")
        sys.exit(1)

    # 写入 Netscape 格式
    with open(OUTPUT_FILE, "w") as f:
        f.write("# Netscape HTTP Cookie File\n")
        f.write("# Exported via Chrome DevTools Protocol\n\n")
        for c in yt_cookies:
            domain = c["domain"]
            flag = "TRUE" if domain.startswith(".") else "FALSE"
            path = c.get("path", "/")
            secure = "TRUE" if c.get("secure") else "FALSE"
            expires = int(c.get("expires", 0))
            name = c["name"]
            value = c["value"]
            if value:  # 跳过空值
                f.write(f"{domain}\t{flag}\t{path}\t{secure}\t{expires}\t{name}\t{value}\n")

    size = os.path.getsize(OUTPUT_FILE)
    print(f"\n✅ 导出 → {OUTPUT_FILE} ({size} 字节)")

    # 验证关键字段
    with open(OUTPUT_FILE) as f:
        content = f.read()
    count = content.count("\n") - 3  # 减去头部注释行
    print(f"   有效 cookies: {count}")
    print("\n📋 关键字段:")
    for key in ("__Secure-3PSID", "__Secure-3PAPISID", "__Secure-3PSIDCC", "SID", "HSID"):
        s = "✅" if key in content else "❌"
        print(f"   {s} {key}")

    print(f"\n🎉 完成！")


if __name__ == "__main__":
    main()
