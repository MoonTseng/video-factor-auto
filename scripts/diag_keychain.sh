#!/bin/bash
# 诊断 Keychain 状态并尝试解锁
set -x

echo "=== Keychain 状态 ==="
security show-keychain-info ~/Library/Keychains/login.keychain-db 2>&1

echo "=== 尝试解锁 Keychain ==="
security unlock-keychain ~/Library/Keychains/login.keychain-db 2>&1
echo "解锁结果: $?"

echo "=== 再次检查状态 ==="
security show-keychain-info ~/Library/Keychains/login.keychain-db 2>&1

echo "=== 尝试获取 Chrome Safe Storage 密钥 ==="
timeout 10 security find-generic-password -w -s "Chrome Safe Storage" -a "Chrome" 2>&1
echo "获取结果: $?"
