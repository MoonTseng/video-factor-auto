#!/usr/bin/env python3
"""
从 Chrome Cookies SQLite 数据库导出 YouTube cookies 为 Netscape 格式。
用 macOS security 命令行获取解密密钥（不弹 GUI 弹窗），openssl 解密。
无需额外 Python 依赖。

用法: python3 scripts/export_chrome_cookies.py
输出: www.youtube.com_cookies.txt
"""
import os
import sys
import shutil
import sqlite3
import subprocess
import tempfile
import hashlib

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_FILE = os.path.join(PROJECT_ROOT, "www.youtube.com_cookies.txt")


def find_chrome_cookies_db() -> str:
    """找到 Chrome Cookies 数据库文件"""
    chrome_dir = os.path.expanduser("~/Library/Application Support/Google/Chrome")
    if not os.path.isdir(chrome_dir):
        # Linux
        chrome_dir = os.path.expanduser("~/.config/google-chrome")
    
    # 常见 profile 路径
    for profile in ("Default", "Profile 1", "Profile 2", "Profile 3"):
        candidate = os.path.join(chrome_dir, profile, "Cookies")
        if os.path.isfile(candidate):
            return candidate
    
    # 搜索所有子目录
    if os.path.isdir(chrome_dir):
        for entry in sorted(os.listdir(chrome_dir)):
            candidate = os.path.join(chrome_dir, entry, "Cookies")
            if os.path.isfile(candidate):
                return candidate
    
    return ""


def get_aes_key() -> bytes | None:
    """获取 Chrome cookie 解密用的 AES key（macOS）"""
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-w", "-s", "Chrome Safe Storage", "-a", "Chrome"],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0 and result.stdout.strip():
            password = result.stdout.strip()
            key = hashlib.pbkdf2_hmac("sha1", password.encode(), b"saltysalt", 1003, dklen=16)
            return key
    except Exception as e:
        print(f"⚠️ security 命令失败: {e}")
    return None


def decrypt_value(encrypted_value: bytes, aes_key: bytes) -> str:
    """用 openssl 命令行解密 Chrome cookie value（无需 Python cryptography 库）"""
    if not encrypted_value:
        return ""
    
    # 未加密的 value
    if not encrypted_value.startswith(b"v10") and not encrypted_value.startswith(b"v11"):
        try:
            return encrypted_value.decode("utf-8", errors="replace")
        except Exception:
            return ""
    
    # 去掉 v10/v11 前缀
    data = encrypted_value[3:]
    if not data:
        return ""
    
    # AES-128-CBC, IV = 16 个空格 (0x20)
    iv_hex = "20" * 16
    key_hex = aes_key.hex()
    
    try:
        proc = subprocess.run(
            ["openssl", "enc", "-aes-128-cbc", "-d", "-K", key_hex, "-iv", iv_hex, "-nopad"],
            input=data, capture_output=True, timeout=5,
        )
        if proc.returncode == 0 and proc.stdout:
            decrypted = proc.stdout
            # PKCS7 padding removal
            pad = decrypted[-1]
            if 0 < pad <= 16 and decrypted[-pad:] == bytes([pad]) * pad:
                decrypted = decrypted[:-pad]
            # Chrome CBC 解密：第一个 block (16 bytes) 可能是垃圾（IV 不精确）
            # 跳过前导非 ASCII 字节，找到实际 cookie value 的开始
            # Chrome 会 prepend 随机 padding，通常 32 字节
            for i in range(len(decrypted)):
                if all(32 <= b < 127 for b in decrypted[i:i+4]):
                    return decrypted[i:].decode("ascii", errors="replace")
            return decrypted[32:].decode("ascii", errors="replace")
    except Exception:
        pass
    
    return ""


def chrome_ts_to_unix(chrome_ts: int) -> int:
    """Chrome timestamp → Unix timestamp"""
    if chrome_ts == 0:
        return 0
    return max(0, int(chrome_ts / 1_000_000) - 11644473600)


def main():
    print("🍪 Chrome YouTube Cookies 导出工具")
    print("=" * 50)
    
    # 1. 找数据库
    db_path = find_chrome_cookies_db()
    if not db_path:
        print("❌ 找不到 Chrome Cookies 数据库！")
        print("   确保已安装 Google Chrome 并登录了 YouTube")
        sys.exit(1)
    print(f"📂 数据库: {db_path}")
    
    # 2. 获取解密密钥
    aes_key = get_aes_key()
    if aes_key:
        print("🔑 解密密钥获取成功")
    else:
        print("⚠️ 无法获取解密密钥，只能读取未加密的 cookies")
    
    # 3. 复制数据库（避免锁冲突）
    tmp_db = tempfile.mktemp(suffix=".db")
    shutil.copy2(db_path, tmp_db)
    
    # 同时复制 WAL 和 SHM 文件（如果存在）
    for suffix in ("-wal", "-shm"):
        src = db_path + suffix
        if os.path.exists(src):
            shutil.copy2(src, tmp_db + suffix)
    
    try:
        conn = sqlite3.connect(tmp_db)
        cursor = conn.cursor()
        
        # 4. 查询 YouTube cookies（只要 .youtube.com 域名）
        cursor.execute("""
            SELECT host_key, name, value, encrypted_value, path,
                   expires_utc, is_secure, is_httponly
            FROM cookies
            WHERE host_key LIKE '%.youtube.com'
               OR host_key = '.youtube.com'
               OR host_key = 'www.youtube.com'
            ORDER BY host_key, name
        """)
        
        rows = cursor.fetchall()
        print(f"📊 找到 {len(rows)} 条 YouTube/Google cookies")
        
        cookies = []
        decrypted_count = 0
        failed_count = 0
        
        for host, name, value, encrypted_value, path, expires_utc, secure, httponly in rows:
            # 解密
            if not value and encrypted_value and aes_key:
                value = decrypt_value(encrypted_value, aes_key)
                if value:
                    decrypted_count += 1
                else:
                    failed_count += 1
                    continue
            elif not value:
                continue
            
            # 过滤掉仍然有问题的 value
            if not value or len(value) < 2:
                failed_count += 1
                continue
            
            expires = chrome_ts_to_unix(expires_utc)
            secure_str = "TRUE" if secure else "FALSE"
            domain_str = "TRUE" if host.startswith(".") else "FALSE"
            
            cookies.append(f"{host}\t{domain_str}\t{path}\t{secure_str}\t{expires}\t{name}\t{value}")
        
        conn.close()
    finally:
        for f in (tmp_db, tmp_db + "-wal", tmp_db + "-shm"):
            if os.path.exists(f):
                os.unlink(f)
    
    if not cookies:
        print("❌ 没有成功解密任何 cookies！")
        sys.exit(1)
    
    # 5. 写入文件
    with open(OUTPUT_FILE, "w") as f:
        f.write("# Netscape HTTP Cookie File\n")
        f.write(f"# Exported from Chrome by export_chrome_cookies.py\n")
        f.write(f"# Date: {__import__('datetime').datetime.now().isoformat()}\n\n")
        for c in cookies:
            f.write(c + "\n")
    
    size = os.path.getsize(OUTPUT_FILE)
    print(f"\n✅ 导出 {len(cookies)} 个 cookies → {OUTPUT_FILE}")
    print(f"   文件大小: {size} 字节")
    print(f"   解密成功: {decrypted_count}, 解密失败: {failed_count}")
    
    # 6. 验证关键字段
    with open(OUTPUT_FILE) as f:
        content = f.read()
    print("\n📋 关键字段检查:")
    for key in ("__Secure-3PSID", "__Secure-3PAPISID", "__Secure-3PSIDCC", "SID", "HSID"):
        status = "✅" if key in content else "❌"
        print(f"   {status} {key}")
    
    print(f"\n🎉 完成！现在可以运行流水线了。")


if __name__ == "__main__":
    main()
