#!/bin/bash
# 尝试修改 Chrome Safe Storage 的 ACL 分区列表
set -x

cd ~/hermes/video-factor-auto || exit 1

# 先解锁
security unlock-keychain -p "ss391922704" ~/Library/Keychains/login.keychain-db

# 尝试修改 ACL —— 添加 teamid 和 apple-tool 到分区列表
# 这允许命令行工具读取 Chrome Safe Storage
echo "=== 修改 ACL ==="
security set-generic-password-partition-list \
  -s "Chrome Safe Storage" \
  -a "Chrome" \
  -S "apple-tool:,apple:,teamid:KL8N8XSYF4" \
  -k "ss391922704" \
  ~/Library/Keychains/login.keychain-db 2>&1
echo "partition-list exit: $?"

# 验证
echo "=== 验证读取 ==="
timeout 10 security find-generic-password -w -a "Chrome" -s "Chrome Safe Storage" ~/Library/Keychains/login.keychain-db 2>&1
echo "verify exit: $?"
