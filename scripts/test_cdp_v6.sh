#!/bin/bash
# CDP v6: 复制 profile + headless + 纯标准库（无需 websockets）
set -x

cd ~/hermes/video-factor-auto || exit 1
export PATH="$HOME/.deno/bin:$HOME/.local/bin:/opt/homebrew/bin:$PATH"

CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
ORIG_DATA="$HOME/Library/Application Support/Google/Chrome"
TEMP_DATA="/tmp/chrome-debug-profile"

# Step 0: 清理僵尸进程
pkill -f "security find-generic-password" 2>/dev/null || true

# Step 1: 关闭 Chrome
echo "=== Step 1: 关闭 Chrome ==="
osascript -e 'tell application "Google Chrome" to quit' 2>/dev/null || true
sleep 3
pkill -9 -f "Google Chrome" 2>/dev/null || true
sleep 2
pgrep -fl "Google Chrome" && { echo "Chrome 无法关闭!"; exit 1; } || echo "Chrome 已关闭"

# Step 2: 复制 Chrome profile
echo "=== Step 2: 复制 profile ==="
rm -rf "$TEMP_DATA"
mkdir -p "$TEMP_DATA/Default"
cp "$ORIG_DATA/Local State" "$TEMP_DATA/" 2>/dev/null || true
cp "$ORIG_DATA/Default/Cookies" "$TEMP_DATA/Default/" 2>/dev/null || true
cp "$ORIG_DATA/Default/Cookies-journal" "$TEMP_DATA/Default/" 2>/dev/null || true
cp "$ORIG_DATA/Default/Preferences" "$TEMP_DATA/Default/" 2>/dev/null || true
cp "$ORIG_DATA/Default/Secure Preferences" "$TEMP_DATA/Default/" 2>/dev/null || true

# Step 3: 启动 headless Chrome
echo "=== Step 3: 启动 Chrome ==="
"$CHROME" \
    --remote-debugging-port=9222 \
    --user-data-dir="$TEMP_DATA" \
    --no-first-run \
    --no-default-browser-check \
    --headless=new \
    2>/dev/null &
CHROME_PID=$!

# 等待就绪
for i in $(seq 1 15); do
    if curl -s http://127.0.0.1:9222/json/version > /dev/null 2>&1; then
        echo "✅ Chrome 就绪 (${i})"
        break
    fi
    sleep 2
done

# Step 4: 用纯 Python 标准库通过 CDP 获取 cookies
echo "=== Step 4: 导出 cookies ==="
python3 << 'PYEOF'
import http.client
import json
import hashlib
import struct
import os
import socket
import ssl
import base64

OUTPUT = os.path.expanduser("~/hermes/video-factor-auto/www.youtube.com_cookies.txt")

# 获取 WebSocket URL
conn = http.client.HTTPConnection("127.0.0.1", 9222, timeout=5)
conn.request("GET", "/json")
tabs = json.loads(conn.getresponse().read())
conn.close()

if not tabs:
    print("❌ 没有 tab")
    exit(1)

ws_url = tabs[0].get("webSocketDebuggerUrl", "")
print(f"找到 {len(tabs)} 个 tab, ws: {ws_url}")

# 解析 ws URL
# ws://127.0.0.1:9222/devtools/page/XXXXX
host = "127.0.0.1"
port = 9222
path = ws_url.split(f":{port}")[1]

# 纯标准库 WebSocket 客户端
sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.settimeout(10)
sock.connect((host, port))

# WebSocket 握手
ws_key = base64.b64encode(os.urandom(16)).decode()
handshake = (
    f"GET {path} HTTP/1.1\r\n"
    f"Host: {host}:{port}\r\n"
    f"Upgrade: websocket\r\n"
    f"Connection: Upgrade\r\n"
    f"Sec-WebSocket-Key: {ws_key}\r\n"
    f"Sec-WebSocket-Version: 13\r\n"
    f"\r\n"
)
sock.sendall(handshake.encode())

# 读取握手响应
response = b""
while b"\r\n\r\n" not in response:
    response += sock.recv(4096)

if b"101" not in response.split(b"\r\n")[0]:
    print(f"❌ WebSocket 握手失败: {response[:200]}")
    exit(1)

print("✅ WebSocket 连接成功")

def ws_send(sock, message):
    """发送 WebSocket frame（带 mask）"""
    data = message.encode()
    frame = bytearray()
    frame.append(0x81)  # FIN + text
    
    length = len(data)
    if length < 126:
        frame.append(0x80 | length)  # MASK bit set
    elif length < 65536:
        frame.append(0x80 | 126)
        frame.extend(struct.pack(">H", length))
    else:
        frame.append(0x80 | 127)
        frame.extend(struct.pack(">Q", length))
    
    # 4-byte mask
    mask = os.urandom(4)
    frame.extend(mask)
    
    # mask data
    for i, b in enumerate(data):
        frame.append(b ^ mask[i % 4])
    
    sock.sendall(bytes(frame))

def ws_recv(sock):
    """接收 WebSocket frame"""
    header = sock.recv(2)
    if len(header) < 2:
        return ""
    
    opcode = header[0] & 0x0F
    masked = header[1] & 0x80
    length = header[1] & 0x7F
    
    if length == 126:
        length = struct.unpack(">H", sock.recv(2))[0]
    elif length == 127:
        length = struct.unpack(">Q", sock.recv(8))[0]
    
    if masked:
        mask = sock.recv(4)
    
    # 读取完整 payload
    data = bytearray()
    while len(data) < length:
        chunk = sock.recv(min(length - len(data), 65536))
        if not chunk:
            break
        data.extend(chunk)
    
    if masked:
        data = bytearray(b ^ mask[i % 4] for i, b in enumerate(data))
    
    return data.decode("utf-8", errors="replace")

# 发送 Network.getAllCookies
ws_send(sock, json.dumps({"id": 1, "method": "Network.getAllCookies"}))
resp_text = ws_recv(sock)
sock.close()

resp = json.loads(resp_text)
all_cookies = resp.get("result", {}).get("cookies", [])
print(f"总 cookies: {len(all_cookies)}")

# 过滤 YouTube/Google
yt = [c for c in all_cookies
      if ".youtube.com" in c.get("domain", "")
      or ".google.com" in c.get("domain", "")]
print(f"YouTube/Google: {len(yt)}")

if not yt:
    print("❌ 没有 YouTube cookies")
    exit(1)

# 写入 Netscape 格式
with open(OUTPUT, "w") as f:
    f.write("# Netscape HTTP Cookie File\n# via CDP\n\n")
    for c in yt:
        v = c.get("value", "")
        if not v:
            continue
        d = c["domain"]
        flag = "TRUE" if d.startswith(".") else "FALSE"
        sec = "TRUE" if c.get("secure") else "FALSE"
        exp = int(c.get("expires", 0))
        f.write(f"{d}\t{flag}\t{c.get('path','/')}\t{sec}\t{exp}\t{c['name']}\t{v}\n")

size = os.path.getsize(OUTPUT)
print(f"✅ 写入 {OUTPUT} ({size} 字节)")

with open(OUTPUT) as f:
    content = f.read()
for k in ("__Secure-3PSID", "__Secure-3PAPISID", "SID", "HSID"):
    s = "✅" if k in content else "❌"
    print(f"  {s} {k}")
PYEOF

# Step 5: 关闭 debug Chrome + 恢复正常 Chrome
echo "=== Step 5: 清理 ==="
kill $CHROME_PID 2>/dev/null || true
sleep 1
open -a "Google Chrome"

# Step 6: 测试下载
echo "=== Step 6: 测试下载 ==="
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

rm -rf "$TEMP_DATA"
echo "=== 完毕 ==="
