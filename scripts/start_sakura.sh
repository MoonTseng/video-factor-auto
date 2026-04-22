#!/bin/bash
# 启动 SakuraLLM 翻译服务 (llama-server)
# 用法: ./scripts/start_sakura.sh [--background]
#
# Apple M4 Metal GPU 加速, 端口 8080
# API 兼容 OpenAI: http://127.0.0.1:8080/v1/chat/completions

set -e

MODEL_PATH="$(dirname "$0")/../models/sakura-7b-qwen2.5-v1.0-q6k.gguf"
PORT=8080
CTX=4096
GPU_LAYERS=99  # 全部offload到GPU

if [ ! -f "$MODEL_PATH" ]; then
    echo "❌ 模型不存在: $MODEL_PATH"
    echo "   请先下载: aria2c -x 16 -s 16 -o sakura-7b-qwen2.5-v1.0-q6k.gguf https://hf-mirror.com/SakuraLLM/Sakura-7B-Qwen2.5-v1.0-GGUF/resolve/main/sakura-7b-qwen2.5-v1.0-q6k.gguf"
    exit 1
fi

if ! command -v llama-server &> /dev/null; then
    echo "❌ llama-server 未安装"
    echo "   brew install llama.cpp"
    exit 1
fi

# 检查端口是否已被占用
if lsof -i :$PORT -sTCP:LISTEN &>/dev/null; then
    echo "⚠️ 端口 $PORT 已被占用，可能 llama-server 已在运行"
    echo "   测试: curl http://127.0.0.1:$PORT/v1/models"
    exit 0
fi

echo "🚀 启动 SakuraLLM 翻译服务..."
echo "   模型: $(basename $MODEL_PATH)"
echo "   端口: $PORT"
echo "   上下文: $CTX"
echo "   GPU层数: $GPU_LAYERS (Metal)"
echo ""

if [ "$1" = "--background" ]; then
    nohup llama-server \
        -m "$MODEL_PATH" \
        --port $PORT \
        -c $CTX \
        -ngl $GPU_LAYERS \
        --threads 8 \
        > /tmp/sakura-server.log 2>&1 &
    echo "✅ 后台启动, PID: $!"
    echo "   日志: /tmp/sakura-server.log"
    echo "   停止: kill $!"
    sleep 2
    if curl -s http://127.0.0.1:$PORT/v1/models | grep -q model; then
        echo "   状态: 🟢 就绪"
    else
        echo "   状态: 🔄 加载中... (等几秒后再试: curl http://127.0.0.1:$PORT/v1/models)"
    fi
else
    exec llama-server \
        -m "$MODEL_PATH" \
        --port $PORT \
        -c $CTX \
        -ngl $GPU_LAYERS \
        --threads 8
fi
