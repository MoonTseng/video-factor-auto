#!/bin/bash
# ============================================================
# YouTube 下载修复脚本
# 诊断并修复 yt-dlp "Sign in to confirm you're not a bot" 错误
# ============================================================
set -e

echo "=========================================="
echo "🔧 YouTube 下载诊断 & 修复"
echo "=========================================="

# 找到项目根目录
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"
echo "📁 项目目录: $PROJECT_DIR"

# Step 1: 确保代码是最新的
echo ""
echo "📦 Step 1: 更新代码..."
git pull origin main 2>&1 || echo "⚠️ git pull 失败，继续使用当前代码"
echo "   当前 commit: $(git log --oneline -1)"

# Step 2: 检查 yt-dlp 版本
echo ""
echo "📦 Step 2: 检查 yt-dlp..."
YTDLP=""
if [ -f "$PROJECT_DIR/.venv/bin/yt-dlp" ]; then
    YTDLP="$PROJECT_DIR/.venv/bin/yt-dlp"
elif command -v yt-dlp &>/dev/null; then
    YTDLP="$(which yt-dlp)"
fi
if [ -z "$YTDLP" ]; then
    echo "❌ yt-dlp 未安装！安装中..."
    pip install yt-dlp -i https://pypi.tuna.tsinghua.edu.cn/simple 2>&1 | tail -3
    YTDLP="$(which yt-dlp)"
fi
echo "   yt-dlp 路径: $YTDLP"
echo "   yt-dlp 版本: $($YTDLP --version)"

# Step 3: 检查 JS 运行时（关键！）
echo ""
echo "🔧 Step 3: 检查 JS 运行时（解决 bot 验证的关键）..."
JS_RUNTIME=""

# 扩展 PATH
export PATH="$HOME/.deno/bin:$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
# NVM
if [ -d "$HOME/.nvm/versions/node" ]; then
    NODE_DIR=$(ls -d "$HOME/.nvm/versions/node"/*/bin 2>/dev/null | sort -V | tail -1)
    [ -n "$NODE_DIR" ] && export PATH="$NODE_DIR:$PATH"
fi

for rt in deno node bun; do
    if command -v $rt &>/dev/null; then
        JS_RUNTIME=$rt
        echo "   ✅ 找到 $rt: $(which $rt) (版本: $($rt --version 2>&1 | head -1))"
        break
    fi
done

if [ -z "$JS_RUNTIME" ]; then
    echo "   ❌ 没有找到任何 JS 运行时！"
    echo "   🔄 自动安装 deno..."
    curl -fsSL https://deno.land/install.sh | sh 2>&1 | tail -5
    export PATH="$HOME/.deno/bin:$PATH"
    if command -v deno &>/dev/null; then
        JS_RUNTIME="deno"
        echo "   ✅ deno 安装成功: $(deno --version | head -1)"
    else
        echo "   ❌ deno 安装失败，尝试安装 node..."
        # macOS
        if command -v brew &>/dev/null; then
            brew install node 2>&1 | tail -3
        else
            curl -fsSL https://deb.nodesource.com/setup_lts.x | sudo bash - 2>&1 | tail -3
            sudo apt-get install -y nodejs 2>&1 | tail -3
        fi
        if command -v node &>/dev/null; then
            JS_RUNTIME="node"
            echo "   ✅ node 安装成功: $(node --version)"
        else
            echo "   ❌❌ 无法安装任何 JS 运行时，下载将失败！"
            exit 1
        fi
    fi
fi

# Step 4: 检查 cookies 文件
echo ""
echo "🍪 Step 4: 检查 cookies..."
COOKIES_FILE="$PROJECT_DIR/www.youtube.com_cookies.txt"
if [ -f "$COOKIES_FILE" ]; then
    SIZE=$(wc -c < "$COOKIES_FILE" | tr -d ' ')
    echo "   文件: $COOKIES_FILE"
    echo "   大小: $SIZE 字节"
    if [ "$SIZE" -lt 1000 ]; then
        echo "   ⚠️ cookies 文件可能不完整，从 git 恢复..."
        git checkout origin/main -- www.youtube.com_cookies.txt 2>&1
        SIZE=$(wc -c < "$COOKIES_FILE" | tr -d ' ')
        echo "   恢复后大小: $SIZE 字节"
    fi
    echo "   关键字段检查:"
    grep -c "__Secure-3PSID" "$COOKIES_FILE" && echo "   ✅ __Secure-3PSID 存在" || echo "   ❌ __Secure-3PSID 缺失"
    grep -c "__Secure-3PAPISID" "$COOKIES_FILE" && echo "   ✅ __Secure-3PAPISID 存在" || echo "   ❌ __Secure-3PAPISID 缺失"
else
    echo "   ❌ cookies 文件不存在: $COOKIES_FILE"
fi

# Step 5: 实际测试下载
echo ""
echo "🧪 Step 5: 测试 YouTube 下载..."
TEST_URL="https://www.youtube.com/watch?v=1FxydtMSorg"
echo "   测试 URL: $TEST_URL"

CMD="$YTDLP --cookies $COOKIES_FILE --js-runtimes $JS_RUNTIME --remote-components ejs:github --skip-download -v $TEST_URL"
echo "   命令: $CMD"
echo ""

if $CMD 2>&1 | tail -20; then
    echo ""
    echo "✅✅✅ 测试成功！YouTube 下载正常工作！"
else
    echo ""
    echo "❌ 测试失败。完整日志："
    $YTDLP --cookies "$COOKIES_FILE" --js-runtimes "$JS_RUNTIME" --remote-components ejs:github --skip-download -v "$TEST_URL" 2>&1 || true
fi

echo ""
echo "=========================================="
echo "📋 环境总结:"
echo "   yt-dlp: $($YTDLP --version)"
echo "   JS Runtime: $JS_RUNTIME ($(which $JS_RUNTIME))"
echo "   Cookies: $COOKIES_FILE ($(wc -c < "$COOKIES_FILE" | tr -d ' ') bytes)"
echo "   PATH: $PATH"
echo "=========================================="
