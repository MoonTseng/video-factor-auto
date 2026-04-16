#!/bin/bash
# 一键修复 YouTube 下载 — 请直接运行此脚本，不要修改任何内容
set -x  # 打印每条命令（防止 AI 篡改执行内容）

cd ~/hermes/video-factor-auto || exit 1
git pull origin main

# 导出 Chrome cookies
python3 scripts/export_chrome_cookies.py

# 扩展 PATH 确保找到 deno
export PATH="$HOME/.deno/bin:$HOME/.local/bin:/opt/homebrew/bin:$PATH"

# 测试下载
.venv/bin/yt-dlp \
  --cookies www.youtube.com_cookies.txt \
  --js-runtimes deno \
  --remote-components ejs:github \
  --skip-download \
  "https://www.youtube.com/watch?v=1FxydtMSorg" 2>&1

echo "=== 脚本执行完毕 ==="
