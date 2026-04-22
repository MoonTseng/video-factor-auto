#!/bin/bash
# SakuraLLM 本地翻译服务 — 基于 llama-server
# 模型: Sakura-7B-Qwen2.5-v1.0 Q6K (专用日中翻译)
# 端口: 8080 (与 config.yaml sakura.base_url 一致)
#
# 用法:
#   ./start_sakura.sh          # 前台运行
#   ./start_sakura.sh --bg     # 后台运行
#   ./start_sakura.sh --stop   # 停止后台服务

set -e
cd "$(dirname "$0")"

MODEL="models/sakura-7b-qwen2.5-v1.0-q6k.gguf"
PORT=8080
THREADS=8       # Apple Silicon 通常 8 核效率核
CONTEXT=4096    # SakuraLLM 翻译不需要超长上下文
GPU_LAYERS=99   # Metal GPU 全部 offload

PID_FILE=".sakura.pid"

# ── 停止 ──
if [ "$1" = "--stop" ]; then
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE")
        if kill -0 "$PID" 2>/dev/null; then
            echo "🛑 停止 SakuraLLM (PID: $PID)..."
            kill "$PID"
            rm "$PID_FILE"
            echo "✅ 已停止"
        else
            echo "⚠️ 进程 $PID 已不存在"
            rm "$PID_FILE"
        fi
    else
        echo "⚠️ 没有找到运行中的 SakuraLLM"
        # 尝试按端口杀
        PID=$(lsof -ti :$PORT 2>/dev/null || true)
        if [ -n "$PID" ]; then
            echo "🛑 发现端口 $PORT 占用 (PID: $PID),停止..."
            kill "$PID"
        fi
    fi
    exit 0
fi

# ── 检查模型文件 ──
if [ ! -f "$MODEL" ]; then
    echo "❌ 模型文件不存在: $MODEL"
    echo "   请先下载:"
    echo "   export HF_ENDPOINT=https://hf-mirror.com"
    echo "   hf download SakuraLLM/Sakura-7B-Qwen2.5-v1.0-GGUF sakura-7b-qwen2.5-v1.0-q6k.gguf --local-dir models/"
    exit 1
fi

# ── 检查 llama-server ──
if ! command -v llama-server &>/dev/null; then
    echo "❌ llama-server 未安装"
    echo "   brew install llama.cpp"
    exit 1
fi

# ── 检查端口 ──
if lsof -ti :$PORT &>/dev/null; then
    echo "⚠️ 端口 $PORT 已被占用"
    echo "   $(lsof -i :$PORT | head -2)"
    echo "   如需重启: $0 --stop && $0"
    exit 1
fi

echo "🌸 启动 SakuraLLM 翻译服务"
echo "   模型: $MODEL"
echo "   端口: http://127.0.0.1:$PORT"
echo "   线程: $THREADS, 上下文: $CONTEXT, GPU层: $GPU_LAYERS"
echo ""

CMD="llama-server -m $MODEL --port $PORT -t $THREADS -c $CONTEXT -ngl $GPU_LAYERS --chat-template chatml"

if [ "$1" = "--bg" ]; then
    echo "📌 后台运行模式..."
    nohup $CMD > .sakura.log 2>&1 &
    echo $! > "$PID_FILE"
    echo "✅ 已启动 (PID: $(cat $PID_FILE))"
    echo "   日志: tail -f .sakura.log"
    echo "   停止: $0 --stop"

    # 等待就绪
    echo -n "   等待服务就绪"
    for i in $(seq 1 30); do
        if curl -s "http://127.0.0.1:$PORT/health" | grep -q "ok" 2>/dev/null; then
            echo ""
            echo "✅ SakuraLLM 就绪!"
            exit 0
        fi
        echo -n "."
        sleep 1
    done
    echo ""
    echo "⚠️ 服务未在30s内就绪,请检查日志: tail -f .sakura.log"
else
    echo "🚀 前台运行 (Ctrl+C 停止)..."
    echo ""
    exec $CMD
fi
