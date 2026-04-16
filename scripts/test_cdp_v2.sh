#!/bin/bash
# CDP 方案 v2：更可靠的 Chrome 重启 + cookies 导出
set -x

cd ~/hermes/video-factor-auto || exit 1
export PATH="$HOME/.deno/bin:$HOME/.local/bin:/opt/homebrew/bin:$PATH"

# Step 1: 关闭 Chrome
echo "=== Step 1: 关闭 Chrome ==="
osascript -e 'tell application "Google Chrome" to quit' 2>/dev/null || true
sleep 3

# 确认真的关了
while pgrep -x "Google Chrome" > /dev/null 2>&1; do
    echo "   Chrome 还在运行，等待..."
    sleep 2
done
echo "   Chrome 已关闭"

# Step 2: 带 debug 端口启动 Chrome
echo "=== Step 2: 启动 Chrome (debug port 9222) ==="
open -a "Google Chrome" --args --remote-debugging-port=9222 --restore-last-session
sleep 8

# 检查端口
for i in $(seq 1 20); do
    if curl -s http://127.0.0.1:9222/json/version > /dev/null 2>&1; then
        echo "   ✅ Chrome debug 端口就绪 (第 ${i} 次检查)"
        curl -s http://127.0.0.1:9222/json/version | python3 -m json.tool 2>/dev/null | head -5
        break
    fi
    echo "   等待 Chrome debug 端口... ($i/20)"
    sleep 3
done

# Step 3: 导出 cookies
echo "=== Step 3: 导出 cookies ==="
python3 -c "
import http.client, json, os, sys, subprocess, asyncio, time

PORT = 9222
OUTPUT = '$PWD/www.youtube.com_cookies.txt'

# 检查端口
try:
    conn = http.client.HTTPConnection('127.0.0.1', PORT, timeout=5)
    conn.request('GET', '/json')
    tabs = json.loads(conn.getresponse().read())
    conn.close()
    print(f'找到 {len(tabs)} 个 tab')
except Exception as e:
    print(f'❌ 无法连接 Chrome debug 端口: {e}')
    sys.exit(1)

# 找有效的 ws url
ws_url = None
for tab in tabs:
    url = tab.get('webSocketDebuggerUrl', '')
    if url:
        ws_url = url
        break

if not ws_url:
    print('❌ 没有可用的 WebSocket URL')
    sys.exit(1)

print(f'WebSocket: {ws_url}')

# 确保 websockets 可用
try:
    import websockets
except ImportError:
    subprocess.run([sys.executable, '-m', 'pip', 'install', 'websockets', '-q',
                    '-i', 'https://pypi.tuna.tsinghua.edu.cn/simple'], timeout=60)
    import websockets

async def fetch_cookies():
    async with websockets.connect(ws_url, max_size=10*1024*1024) as ws:
        await ws.send(json.dumps({'id': 1, 'method': 'Network.getAllCookies'}))
        resp = json.loads(await asyncio.wait_for(ws.recv(), timeout=15))
        return resp.get('result', {}).get('cookies', [])

all_cookies = asyncio.run(fetch_cookies())
print(f'总 cookies: {len(all_cookies)}')

# 过滤
yt = [c for c in all_cookies
      if '.youtube.com' in c.get('domain', '')
      or '.google.com' in c.get('domain', '')]
print(f'YouTube/Google: {len(yt)}')

if not yt:
    print('❌ 没有 YouTube cookies')
    sys.exit(1)

# 写 Netscape 格式
with open(OUTPUT, 'w') as f:
    f.write('# Netscape HTTP Cookie File\n# via CDP\n\n')
    for c in yt:
        if not c.get('value'):
            continue
        d = c['domain']
        flag = 'TRUE' if d.startswith('.') else 'FALSE'
        sec = 'TRUE' if c.get('secure') else 'FALSE'
        exp = int(c.get('expires', 0))
        f.write(f\"{d}\t{flag}\t{c.get('path','/')}\t{sec}\t{exp}\t{c['name']}\t{c['value']}\n\")

size = os.path.getsize(OUTPUT)
print(f'✅ 写入 {OUTPUT} ({size} 字节)')

with open(OUTPUT) as f:
    content = f.read()
for k in ('__Secure-3PSID', '__Secure-3PAPISID', 'SID', 'HSID'):
    s = '✅' if k in content else '❌'
    print(f'  {s} {k}')
"

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
