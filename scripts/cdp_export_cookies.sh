#!/bin/bash
# 通过 Chrome DevTools Protocol 导出 YouTube cookies
# 不依赖 Keychain，不需要任何授权弹窗
set -x

cd ~/hermes/video-factor-auto || exit 1

# 确保 PATH
export PATH="$HOME/.deno/bin:$HOME/.local/bin:/opt/homebrew/bin:$PATH"

# Step 1: 关闭 Chrome（如果正在运行）
echo "=== Step 1: 关闭 Chrome ==="
pkill -f "Google Chrome" 2>/dev/null || true
sleep 2

# Step 2: 以 remote debugging 模式启动 Chrome（headless 不行，需要正常模式读 cookies）
echo "=== Step 2: 启动 Chrome（debug 模式）==="
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  --remote-debugging-port=9222 \
  --no-first-run \
  --no-default-browser-check \
  "about:blank" &
CHROME_PID=$!
sleep 5

# Step 3: 通过 CDP 获取 YouTube cookies
echo "=== Step 3: 导出 cookies ==="
python3 -c "
import json, http.client, sys, os

# 连接 Chrome DevTools
conn = http.client.HTTPConnection('127.0.0.1', 9222)

# 获取可用的 tab
conn.request('GET', '/json')
resp = conn.getresponse()
tabs = json.loads(resp.read())
print(f'找到 {len(tabs)} 个 tab')

if not tabs:
    print('❌ 没有可用的 Chrome tab')
    sys.exit(1)

ws_url = tabs[0].get('webSocketDebuggerUrl', '')
print(f'WebSocket URL: {ws_url}')

# 用 websocket 发送 CDP 命令获取 cookies
# 但我们先试试简单的 HTTP endpoint
# Chrome DevTools 没有直接的 HTTP API 获取 cookies
# 需要用 websocket 发 Network.getAllCookies

# 用 Python 的 websocket 或者直接用 curl + websocat
# 最简单：用一个临时 Python 脚本

import subprocess, tempfile

script = '''
import asyncio, json, sys

try:
    import websockets
except ImportError:
    import subprocess
    subprocess.run([sys.executable, '-m', 'pip', 'install', 'websockets', '-q'])
    import websockets

async def get_cookies():
    tabs_url = 'http://127.0.0.1:9222/json'
    import http.client
    conn = http.client.HTTPConnection('127.0.0.1', 9222)
    conn.request('GET', '/json')
    tabs = json.loads(conn.getresponse().read())
    ws_url = tabs[0]['webSocketDebuggerUrl']
    
    async with websockets.connect(ws_url) as ws:
        # 获取所有 cookies
        await ws.send(json.dumps({'id': 1, 'method': 'Network.getAllCookies'}))
        resp = json.loads(await ws.recv())
        all_cookies = resp.get('result', {}).get('cookies', [])
        
        # 过滤 YouTube + Google cookies
        yt_cookies = [c for c in all_cookies 
                      if '.youtube.com' in c.get('domain', '') 
                      or '.google.com' in c.get('domain', '')]
        
        print(f'总 cookies: {len(all_cookies)}, YouTube/Google: {len(yt_cookies)}')
        
        # 写入 Netscape 格式
        output = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath('__file__'))), 'www.youtube.com_cookies.txt')
        output = os.path.expanduser('~/hermes/video-factor-auto/www.youtube.com_cookies.txt')
        with open(output, 'w') as f:
            f.write('# Netscape HTTP Cookie File\\n')
            f.write('# Exported via Chrome DevTools Protocol\\n\\n')
            for c in yt_cookies:
                domain = c['domain']
                flag = 'TRUE' if domain.startswith('.') else 'FALSE'
                path = c.get('path', '/')
                secure = 'TRUE' if c.get('secure') else 'FALSE'
                expires = int(c.get('expires', 0))
                name = c['name']
                value = c['value']
                f.write(f'{domain}\\t{flag}\\t{path}\\t{secure}\\t{expires}\\t{name}\\t{value}\\n')
        
        size = os.path.getsize(output)
        print(f'✅ 导出 {len(yt_cookies)} 个 cookies → {output} ({size} bytes)')
        
        # 验证
        with open(output) as f:
            content = f.read()
        for key in ('__Secure-3PSID', '__Secure-3PAPISID', 'SID', 'HSID'):
            s = '✅' if key in content else '❌'
            print(f'  {s} {key}')

import os
asyncio.run(get_cookies())
'''

# 写入临时文件执行
tmp = tempfile.mktemp(suffix='.py')
with open(tmp, 'w') as f:
    f.write(script)

result = subprocess.run([sys.executable, tmp], capture_output=True, text=True, timeout=30)
print(result.stdout)
if result.stderr:
    print('STDERR:', result.stderr[-500:])
os.unlink(tmp)
"

# Step 4: 关闭 debug Chrome
echo "=== Step 4: 关闭 Chrome ==="
kill $CHROME_PID 2>/dev/null || true

# Step 5: 测试下载
echo "=== Step 5: 测试下载 ==="
if [ -f www.youtube.com_cookies.txt ] && [ $(wc -c < www.youtube.com_cookies.txt) -gt 1000 ]; then
    .venv/bin/yt-dlp \
      --cookies www.youtube.com_cookies.txt \
      --js-runtimes deno \
      --remote-components ejs:github \
      --skip-download \
      "https://www.youtube.com/watch?v=1FxydtMSorg" 2>&1
    echo "=== exit code: $? ==="
else
    echo "❌ cookies 文件不存在或太小"
    ls -la www.youtube.com_cookies.txt 2>/dev/null
fi

echo "=== 完毕 ==="
