#!/bin/bash
# 精确诊断 + 修复
set -x

cd ~/hermes/video-factor-auto || exit 1

# 确保 PATH 包含 deno
export PATH="$HOME/.deno/bin:$HOME/.local/bin:/opt/homebrew/bin:$PATH"

# 验证 deno
which deno
deno --version

# 关键测试：cookies-from-browser + deno（这才是最终方案）
echo "=== 测试 cookies-from-browser chrome ==="
.venv/bin/yt-dlp \
  --cookies-from-browser chrome \
  --js-runtimes deno \
  --remote-components ejs:github \
  --skip-download \
  -v \
  "https://www.youtube.com/watch?v=1FxydtMSorg" 2>&1

echo "=== exit code: $? ==="
