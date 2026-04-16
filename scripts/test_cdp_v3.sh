#!/bin/bash
# CDP v3: 直接用 Chrome 可执行文件启动 debug 模式
set -x

cd ~/hermes/video-factor-auto || exit 1
export PATH="$HOME/.deno/bin:$HOME/.local/bin:/opt/homebrew/bin:$PATH"

CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

# Step 1: 彻底关闭 Chrome
echo "=== Step 1: 关闭 Chrome ==="
osascript -e 'tell application "Google Chrome" to quit' 2>/dev/null || true
sleep 3
# 如果还在跑就强杀
pkill -9 -f "Google Chrome" 2>/dev/null || true
sleep 2

# 确认关了
pgrep -fl "Google Chrome" || echo "Chrome 已完全关闭"

# Step 2: 直接启动 Chrome（不用 open 命令）
echo "=== Step 2: 启动 Chrome ==="
"$CHROME" \
    --remote-debugging-port=9222 \
    --no-first-run \
    --no-default-browser-check \
    --restore-last-session &
CHROME_PID=$!
echo "Chrome PID: $CHROME_PID"

# 等一会让它初始化
sleep 10

# 诊断
echo "=== 诊断 ==="
echo "Chrome 进程:"
ps aux | grep -i "chrome" | grep -v grep | head -5
echo ""
echo "9222 端口:"
lsof -i :9222 2>/dev/null || echo "端口未监听"
echo ""
echo "Chrome 命令行参数:"
ps -p $CHROME_PID -o args= 2>/dev/null || echo "PID $CHROME_PID 不存在"

# 尝试连接
echo ""
echo "curl 测试:"
curl -v http://127.0.0.1:9222/json/version 2>&1 || echo "连接失败"

echo "=== 诊断完毕 ==="
