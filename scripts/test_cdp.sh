#!/bin/bash
# CDP 方案：通过 Chrome DevTools Protocol 导出 cookies 并测试下载
set -x

cd ~/hermes/video-factor-auto || exit 1
export PATH="$HOME/.deno/bin:$HOME/.local/bin:/opt/homebrew/bin:$PATH"

# 导出 cookies
python3 scripts/cdp_export_cookies.py

# 测试下载
echo "=== 测试下载 ==="
if [ -f www.youtube.com_cookies.txt ] && [ $(wc -c < www.youtube.com_cookies.txt) -gt 1000 ]; then
    .venv/bin/yt-dlp \
      --cookies www.youtube.com_cookies.txt \
      --js-runtimes deno \
      --remote-components ejs:github \
      --skip-download \
      "https://www.youtube.com/watch?v=1FxydtMSorg" 2>&1
    echo "=== yt-dlp exit: $? ==="
else
    echo "❌ cookies 文件不存在或太小"
    wc -c www.youtube.com_cookies.txt 2>/dev/null
fi

echo "=== 完毕 ==="
