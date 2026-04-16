#!/bin/bash
# 用 AppleScript 让 Chrome 执行 JS 读取 YouTube cookies
# 完全不涉及 Keychain！
set -x

cd ~/hermes/video-factor-auto || exit 1

# Step 1: 确保 Chrome 在运行
echo "=== Step 1: 确保 Chrome 运行 ==="
open -a "Google Chrome" 2>/dev/null
sleep 3

# Step 2: 让 Chrome 打开 YouTube（如果没有 YouTube tab 的话）
echo "=== Step 2: 打开 YouTube ==="
osascript -e '
tell application "Google Chrome"
    activate
    -- 检查有没有 YouTube tab
    set found to false
    repeat with w in windows
        repeat with t in tabs of w
            if URL of t contains "youtube.com" then
                set found to true
                set active tab index of w to (index of t)
                exit repeat
            end if
        end repeat
        if found then exit repeat
    end repeat
    
    -- 如果没有就打开一个
    if not found then
        tell window 1
            set newTab to make new tab with properties {URL:"https://www.youtube.com"}
        end tell
        delay 5
    end if
end tell
'
echo "open youtube: $?"
sleep 3

# Step 3: 通过 AppleScript 执行 JavaScript 获取 cookies
echo "=== Step 3: 读取 cookies ==="
COOKIES=$(osascript -e '
tell application "Google Chrome"
    tell active tab of window 1
        set jsResult to execute javascript "document.cookie"
        return jsResult
    end tell
end tell
' 2>&1)
echo "cookies length: ${#COOKIES}"
echo "cookies preview: ${COOKIES:0:200}"

if [ ${#COOKIES} -lt 50 ]; then
    echo "❌ cookies 太少，可能没有登录 YouTube 或 AppleScript 权限不够"
    echo "完整内容: $COOKIES"
    exit 1
fi

# Step 4: 转换为 Netscape 格式
echo "=== Step 4: 转换格式 ==="
python3 << PYEOF
import os

raw = """$COOKIES"""
output = os.path.expanduser("~/hermes/video-factor-auto/www.youtube.com_cookies.txt")

# document.cookie 格式: "name1=value1; name2=value2; ..."
pairs = [p.strip() for p in raw.split(";") if "=" in p]
print(f"解析到 {len(pairs)} 个 cookies")

with open(output, "w") as f:
    f.write("# Netscape HTTP Cookie File\n# via AppleScript\n\n")
    for pair in pairs:
        name, _, value = pair.partition("=")
        name = name.strip()
        value = value.strip()
        if not name or not value:
            continue
        # YouTube cookies 都是 .youtube.com 域
        f.write(f".youtube.com\tTRUE\t/\tTRUE\t0\t{name}\t{value}\n")

size = os.path.getsize(output)
print(f"✅ 写入 {output} ({size} 字节)")

with open(output) as f:
    content = f.read()
for k in ("__Secure-3PSID", "__Secure-3PAPISID", "SID", "HSID", "LOGIN_INFO"):
    s = "✅" if k in content else "❌"
    print(f"  {s} {k}")
PYEOF

# Step 5: 测试下载
echo "=== Step 5: 测试下载 ==="
if [ -f www.youtube.com_cookies.txt ] && [ $(wc -c < www.youtube.com_cookies.txt) -gt 500 ]; then
    .venv/bin/yt-dlp \
      --cookies www.youtube.com_cookies.txt \
      --js-runtimes deno \
      --remote-components ejs:github \
      --skip-download \
      "https://www.youtube.com/watch?v=1FxydtMSorg" 2>&1
    echo "=== yt-dlp exit: $? ==="
else
    echo "❌ cookies 文件太小"
    wc -c www.youtube.com_cookies.txt 2>/dev/null
fi

echo "=== 完毕 ==="
