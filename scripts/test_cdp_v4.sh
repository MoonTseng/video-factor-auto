#!/bin/bash
# CDP v4: 指定 --user-data-dir 为 Chrome 默认目录
set -x

cd ~/hermes/video-factor-auto || exit 1
export PATH="$HOME/.deno/bin:$HOME/.local/bin:/opt/homebrew/bin:$PATH"

CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
CHROME_DATA="$HOME/Library/Application Support/Google/Chrome"

# Step 0: 清理之前的僵尸 security 进程
echo "=== Step 0: 清理僵尸进程 ==="
pkill -f "security find-generic-password" 2>/dev/null || true
sleep 1

# Step 1: 关闭 Chrome
echo "=== Step 1: 关闭 Chrome ==="
osascript -e 'tell application "Google Chrome" to quit' 2>/dev/null || true
sleep 3
pkill -9 -f "Google Chrome" 2>/dev/null || true
sleep 2
pgrep -fl "Google Chrome" || echo "Chrome 已关闭"

# Step 2: 启动带 debug 端口的 Chrome（关键：指定 user-data-dir）
echo "=== Step 2: 启动 Chrome ==="
"$CHROME" \
    --remote-debugging-port=9222 \
    --user-data-dir="$CHROME_DATA" \
    --no-first-run \
    --no-default-browser-check \
    --restore-last-session &
CHROME_PID=$!
echo "Chrome PID: $CHROME_PID"
sleep 10

# 诊断
echo "=== 诊断 ==="
lsof -i :9222 2>/dev/null | head -5 || echo "端口未监听"
curl -s http://127.0.0.1:9222/json/version 2>&1 | head -10

# Step 3: 导出 cookies
echo "=== Step 3: 导出 cookies ==="
python3 scripts/cdp_export_cookies.py

# Step 4: 测试下载
echo "=== Step 4: 测试下载 ==="
if [ -f www.youtube.com_cookies.txt ] && [ $(wc -c < www.youtube.com_cookies.txt) -gt 1000 ]; then
    .venv/bin/yt-dlp \
      --cookies www.youtube.com_cookies.txt \
      --js-runtimes deno \
      --remote-components ejs:github \
      --skip-download \
      "https://www.youtube.com/watch?v=1FxydtMSorg" 2>&1
    echo "=== yt-dlp exit: $? ==="
else
    echo "❌ cookies 文件太小或不存在"
    wc -c www.youtube.com_cookies.txt 2>/dev/null
fi

echo "=== 完毕 ==="
