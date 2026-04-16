#!/bin/bash
# CDP v5: 复制 Chrome profile 到临时目录，用临时目录启动 debug Chrome
# Chrome 要求 non-default data directory 才开 debug 端口
# 所以我们把原始 profile 复制过去，Chrome 会用自己的 Keychain 访问解密 cookies
set -x

cd ~/hermes/video-factor-auto || exit 1
export PATH="$HOME/.deno/bin:$HOME/.local/bin:/opt/homebrew/bin:$PATH"

CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
ORIG_DATA="$HOME/Library/Application Support/Google/Chrome"
TEMP_DATA="/tmp/chrome-debug-profile"

# Step 0: 清理
echo "=== Step 0: 清理 ==="
pkill -f "security find-generic-password" 2>/dev/null || true

# Step 1: 关闭 Chrome
echo "=== Step 1: 关闭 Chrome ==="
osascript -e 'tell application "Google Chrome" to quit' 2>/dev/null || true
sleep 3
pkill -9 -f "Google Chrome" 2>/dev/null || true
sleep 2
pgrep -fl "Google Chrome" && { echo "Chrome 无法关闭!"; exit 1; } || echo "Chrome 已关闭"

# Step 2: 复制 Chrome profile（只复制 Cookies 和必要文件）
echo "=== Step 2: 复制 Chrome profile ==="
rm -rf "$TEMP_DATA"
mkdir -p "$TEMP_DATA/Default"

# 复制关键文件
cp "$ORIG_DATA/Local State" "$TEMP_DATA/" 2>/dev/null || true
cp "$ORIG_DATA/Default/Cookies" "$TEMP_DATA/Default/" 2>/dev/null || true
cp "$ORIG_DATA/Default/Cookies-journal" "$TEMP_DATA/Default/" 2>/dev/null || true
# WAL 模式的文件
cp "$ORIG_DATA/Default/Cookies-wal" "$TEMP_DATA/Default/" 2>/dev/null || true
cp "$ORIG_DATA/Default/Cookies-shm" "$TEMP_DATA/Default/" 2>/dev/null || true
cp "$ORIG_DATA/Default/Preferences" "$TEMP_DATA/Default/" 2>/dev/null || true
cp "$ORIG_DATA/Default/Secure Preferences" "$TEMP_DATA/Default/" 2>/dev/null || true
cp "$ORIG_DATA/Default/Login Data" "$TEMP_DATA/Default/" 2>/dev/null || true

echo "临时目录内容:"
ls -la "$TEMP_DATA/Default/"

# Step 3: 用临时目录启动 Chrome debug
echo "=== Step 3: 启动 Chrome (临时 profile + debug 端口) ==="
"$CHROME" \
    --remote-debugging-port=9222 \
    --user-data-dir="$TEMP_DATA" \
    --no-first-run \
    --no-default-browser-check \
    --headless=new \
    2>/dev/null &
CHROME_PID=$!
echo "Chrome PID: $CHROME_PID"

# 等待端口就绪
for i in $(seq 1 20); do
    if curl -s http://127.0.0.1:9222/json/version > /dev/null 2>&1; then
        echo "✅ Chrome debug 端口就绪 (第 ${i} 次)"
        curl -s http://127.0.0.1:9222/json/version | python3 -m json.tool 2>/dev/null
        break
    fi
    echo "   等待... ($i/20)"
    sleep 2
done

# Step 4: 导出 cookies
echo "=== Step 4: 导出 cookies ==="
python3 scripts/cdp_export_cookies.py

# Step 5: 关闭 debug Chrome
echo "=== Step 5: 关闭 debug Chrome ==="
kill $CHROME_PID 2>/dev/null || true
sleep 1

# 重启正常 Chrome（恢复用户体验）
echo "=== Step 6: 重启正常 Chrome ==="
open -a "Google Chrome"

# Step 7: 测试下载
echo "=== Step 7: 测试下载 ==="
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

# 清理
rm -rf "$TEMP_DATA"
echo "=== 完毕 ==="
