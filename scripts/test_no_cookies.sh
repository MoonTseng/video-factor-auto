#!/bin/bash
# 测试：不用 cookies，只靠 deno 能不能下载
set -x

cd ~/hermes/video-factor-auto || exit 1
export PATH="$HOME/.deno/bin:$HOME/.local/bin:/opt/homebrew/bin:$PATH"

echo "=== 测试 1: 不用 cookies 下载 ==="
.venv/bin/yt-dlp \
  --js-runtimes deno \
  --remote-components ejs:github \
  --skip-download \
  -v \
  "https://www.youtube.com/watch?v=1FxydtMSorg" 2>&1

echo "=== exit: $? ==="

echo ""
echo "=== 测试 2: 用 Safari cookies (如果有的话) ==="
# Safari 的 cookies 不需要 Keychain 解密
.venv/bin/yt-dlp \
  --cookies-from-browser safari \
  --js-runtimes deno \
  --remote-components ejs:github \
  --skip-download \
  -v \
  "https://www.youtube.com/watch?v=1FxydtMSorg" 2>&1

echo "=== exit: $? ==="
echo "=== 完毕 ==="
