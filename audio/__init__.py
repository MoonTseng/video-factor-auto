"""TTS 配音模块 — 多后端：CosyVoice2（首选）/ Edge-TTS（兜底）"""

import asyncio
import json
import logging
import os
import subprocess
import struct
import wave
from pathlib import Path

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════
#  统一入口
# ══════════════════════════════════════════════════════════

def generate_audio_segments(config: dict, segments: list[dict], output_dir: str) -> list[dict]:
    """
    为每段解说文案生成 TTS 配音。
    自动选择可用的 TTS 后端：CosyVoice2 > Edge-TTS

    segments 格式: [{start_time, end_time, text, scene_desc}, ...]
    返回: [{index, audio_path, text, start_time, end_time, duration}, ...]
    """
    os.makedirs(output_dir, exist_ok=True)
    tts_cfg = config.get("tts", {})
    backend = tts_cfg.get("backend", "auto")

    if backend == "auto":
        backend = _detect_backend()

    logger.info(f"🔊 TTS 后端: {backend}")

    if backend == "cosyvoice2":
        return _generate_cosyvoice2(tts_cfg, segments, output_dir)
    elif backend == "edge-tts":
        return _generate_edge_tts(tts_cfg, segments, output_dir)
    else:
        raise ValueError(f"不支持的 TTS 后端: {backend}")


def _detect_backend() -> str:
    """检测可用的 TTS 后端"""
    # 1. 检查 CosyVoice2
    try:
        # 检查 CosyVoice2 HTTP 服务是否在运行
        import httpx
        resp = httpx.get("http://127.0.0.1:50000/inference_list", timeout=3)
        if resp.status_code == 200:
            logger.info("✅ 检测到 CosyVoice2 服务")
            return "cosyvoice2"
    except Exception:
        pass

    # 2. 检查 CosyVoice2 Python 模块
    try:
        import cosyvoice  # noqa: F401
        logger.info("✅ 检测到 CosyVoice2 模块（本地推理模式）")
        return "cosyvoice2"
    except ImportError:
        pass

    # 3. 兜底 Edge-TTS
    try:
        import edge_tts  # noqa: F401
        logger.info("⚠️ CosyVoice2 不可用，使用 Edge-TTS 兜底")
        return "edge-tts"
    except ImportError:
        pass

    raise RuntimeError("没有可用的 TTS 后端！请安装 CosyVoice2 或 edge-tts")


# ══════════════════════════════════════════════════════════
#  CosyVoice2 后端
# ══════════════════════════════════════════════════════════

def _generate_cosyvoice2(tts_cfg: dict, segments: list[dict], output_dir: str) -> list[dict]:
    """
    CosyVoice2 TTS — 支持两种模式：
    1. HTTP API（推荐）：通过 CosyVoice2 WebUI/API 服务
    2. 本地推理：直接加载模型

    CosyVoice2 支持的模式：
    - zero_shot: 基于参考音频克隆音色（最自然）
    - cross_lingual: 跨语言合成
    - sft: 预训练音色
    - instruct: 指令控制
    """
    cosyvoice_cfg = tts_cfg.get("cosyvoice2", {})
    api_url = cosyvoice_cfg.get("api_url", "http://127.0.0.1:50000")
    mode = cosyvoice_cfg.get("mode", "sft")  # sft / zero_shot / cross_lingual
    speaker = cosyvoice_cfg.get("speaker", "中文男")
    ref_audio = cosyvoice_cfg.get("ref_audio", "")  # zero_shot 模式需要参考音频
    ref_text = cosyvoice_cfg.get("ref_text", "")

    results = []

    # 尝试 HTTP API 模式
    try:
        import httpx
        resp = httpx.get(f"{api_url}/inference_list", timeout=5)
        if resp.status_code == 200:
            return _cosyvoice2_http(api_url, mode, speaker, ref_audio, ref_text,
                                     segments, output_dir)
    except Exception:
        pass

    # 本地推理模式
    return _cosyvoice2_local(cosyvoice_cfg, mode, speaker, ref_audio, ref_text,
                              segments, output_dir)


def _cosyvoice2_http(api_url: str, mode: str, speaker: str, ref_audio: str,
                      ref_text: str, segments: list[dict], output_dir: str) -> list[dict]:
    """通过 HTTP API 调用 CosyVoice2"""
    import httpx

    results = []

    for i, seg in enumerate(segments):
        text = seg["text"].strip()
        if not text:
            continue

        output_path = os.path.join(output_dir, f"tts_{i:03d}.wav")
        logger.info(f"  🎤 [{i+1}/{len(segments)}] CosyVoice2 合成: {text[:30]}...")

        try:
            if mode == "zero_shot" and ref_audio:
                # 零样本克隆模式
                with open(ref_audio, "rb") as f:
                    files = {"ref_audio": f}
                    data = {
                        "tts_text": text,
                        "prompt_text": ref_text or "这是一段参考语音。",
                        "mode": "zero_shot",
                    }
                    resp = httpx.post(f"{api_url}/inference_zero_shot",
                                     data=data, files=files, timeout=120)
            elif mode == "cross_lingual" and ref_audio:
                # 跨语言模式
                with open(ref_audio, "rb") as f:
                    files = {"ref_audio": f}
                    data = {"tts_text": text, "mode": "cross_lingual"}
                    resp = httpx.post(f"{api_url}/inference_cross_lingual",
                                     data=data, files=files, timeout=120)
            else:
                # SFT 预训练音色模式
                data = {
                    "tts_text": text,
                    "spk_id": speaker,
                    "mode": "sft",
                }
                resp = httpx.post(f"{api_url}/inference_sft",
                                  data=data, timeout=120)

            if resp.status_code == 200:
                with open(output_path, "wb") as f:
                    f.write(resp.content)
            else:
                logger.warning(f"CosyVoice2 API 错误 {resp.status_code}: {resp.text[:200]}")
                continue

        except Exception as e:
            logger.error(f"CosyVoice2 合成失败 (段 {i}): {e}")
            continue

        duration = _get_audio_duration(output_path)
        results.append({
            "index": i,
            "audio_path": output_path,
            "text": text,
            "start_time": seg["start_time"],
            "end_time": seg["end_time"],
            "duration": duration,
        })

    logger.info(f"✅ CosyVoice2 合成完成: {len(results)}/{len(segments)} 段")
    return results


def _cosyvoice2_local(cosyvoice_cfg: dict, mode: str, speaker: str,
                       ref_audio: str, ref_text: str,
                       segments: list[dict], output_dir: str) -> list[dict]:
    """本地加载 CosyVoice2 模型推理"""
    try:
        from cosyvoice.cli.cosyvoice import CosyVoice2
        import torchaudio
    except ImportError:
        raise RuntimeError(
            "CosyVoice2 未安装。请参照安装指南:\n"
            "  git clone https://github.com/FunAudioLLM/CosyVoice.git\n"
            "  cd CosyVoice && pip install -e .\n"
            "或启动 CosyVoice2 HTTP 服务后重试。"
        )

    model_dir = cosyvoice_cfg.get("model_dir", "pretrained_models/CosyVoice2-0.5B")
    logger.info(f"🔄 加载 CosyVoice2 模型: {model_dir}")
    cosyvoice = CosyVoice2(model_dir)

    results = []

    for i, seg in enumerate(segments):
        text = seg["text"].strip()
        if not text:
            continue

        output_path = os.path.join(output_dir, f"tts_{i:03d}.wav")
        logger.info(f"  🎤 [{i+1}/{len(segments)}] 本地合成: {text[:30]}...")

        try:
            if mode == "zero_shot" and ref_audio:
                output = cosyvoice.inference_zero_shot(text, ref_text, ref_audio)
            elif mode == "cross_lingual" and ref_audio:
                output = cosyvoice.inference_cross_lingual(text, ref_audio)
            else:
                output = cosyvoice.inference_sft(text, speaker)

            # CosyVoice2 返回 generator，取第一个结果
            for result in output:
                torchaudio.save(output_path, result["tts_speech"], 22050)
                break

        except Exception as e:
            logger.error(f"本地合成失败 (段 {i}): {e}")
            continue

        duration = _get_audio_duration(output_path)
        results.append({
            "index": i,
            "audio_path": output_path,
            "text": text,
            "start_time": seg["start_time"],
            "end_time": seg["end_time"],
            "duration": duration,
        })

    logger.info(f"✅ CosyVoice2 本地合成完成: {len(results)}/{len(segments)} 段")
    return results


# ══════════════════════════════════════════════════════════
#  Edge-TTS 后端（兜底）
# ══════════════════════════════════════════════════════════

def _generate_edge_tts(tts_cfg: dict, segments: list[dict], output_dir: str) -> list[dict]:
    """Edge-TTS 兜底方案 — 微软 TTS，免费但不够自然"""
    import edge_tts

    edge_cfg = tts_cfg.get("edge_tts", {})
    voice = edge_cfg.get("voice", "zh-CN-YunjianNeural")  # 云健 — 男声，比较沉稳
    rate = edge_cfg.get("rate", "+5%")
    pitch = edge_cfg.get("pitch", "-5Hz")  # 稍低沉

    results = []

    for i, seg in enumerate(segments):
        text = seg["text"].strip()
        if not text:
            continue

        output_path = os.path.join(output_dir, f"tts_{i:03d}.mp3")
        subtitle_path = os.path.join(output_dir, f"tts_{i:03d}.vtt")

        logger.info(f"  🎤 [{i+1}/{len(segments)}] Edge-TTS: {text[:30]}...")

        try:
            communicate = edge_tts.Communicate(
                text=text,
                voice=voice,
                rate=rate,
                pitch=pitch,
            )
            asyncio.run(_edge_tts_save(communicate, output_path, subtitle_path))
        except Exception as e:
            logger.error(f"Edge-TTS 合成失败 (段 {i}): {e}")
            continue

        duration = _get_audio_duration(output_path)
        results.append({
            "index": i,
            "audio_path": output_path,
            "subtitle_path": subtitle_path,
            "text": text,
            "start_time": seg["start_time"],
            "end_time": seg["end_time"],
            "duration": duration,
        })

    logger.info(f"✅ Edge-TTS 合成完成: {len(results)}/{len(segments)} 段")
    return results


async def _edge_tts_save(communicate, output_path: str, subtitle_path: str):
    """异步保存 Edge-TTS 结果"""
    await communicate.save(output_path)
    # 尝试保存字幕
    try:
        sub_maker = communicate.stream()
        # Edge-TTS SubMaker 需要另一种方式
    except Exception:
        pass


# ══════════════════════════════════════════════════════════
#  音频后处理
# ══════════════════════════════════════════════════════════

def concat_audio_segments(audio_segments: list[dict], output_path: str,
                          total_duration: float = 0) -> str:
    """
    按时间轴拼接所有 TTS 音频段为完整音轨。
    在对应的 start_time 位置插入每段音频，中间空白。
    """
    if not audio_segments:
        raise ValueError("没有音频段可拼接")

    # 排序
    sorted_segs = sorted(audio_segments, key=lambda s: s["start_time"])

    # 生成 ffmpeg 拼接的 filter_complex
    inputs = []
    filter_parts = []

    for i, seg in enumerate(sorted_segs):
        inputs.extend(["-i", seg["audio_path"]])

        # 计算该段前面需要多少静音
        delay_ms = int(seg["start_time"] * 1000)
        if delay_ms > 0:
            filter_parts.append(f"[{i}:a]adelay={delay_ms}|{delay_ms}[a{i}]")
        else:
            filter_parts.append(f"[{i}:a]acopy[a{i}]")

    # 混合所有轨道
    mix_inputs = "".join(f"[a{i}]" for i in range(len(sorted_segs)))
    filter_parts.append(f"{mix_inputs}amix=inputs={len(sorted_segs)}:duration=longest[out]")

    filter_complex = ";".join(filter_parts)

    cmd = ["ffmpeg", "-y"] + inputs + [
        "-filter_complex", filter_complex,
        "-map", "[out]",
        "-acodec", "libmp3lame", "-q:a", "2",
        output_path,
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        logger.error(f"音频拼接失败: {result.stderr[-300:]}")
        # 降级：简单顺序拼接
        return _simple_concat(audio_segments, output_path)

    logger.info(f"✅ 音频拼接完成: {output_path}")
    return output_path


def _simple_concat(audio_segments: list[dict], output_path: str) -> str:
    """简单顺序拼接（降级方案）"""
    import tempfile
    sorted_segs = sorted(audio_segments, key=lambda s: s["start_time"])

    list_file = output_path + ".txt"
    with open(list_file, "w") as f:
        for seg in sorted_segs:
            f.write(f"file '{seg['audio_path']}'\n")

    cmd = [
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0",
        "-i", list_file,
        "-acodec", "libmp3lame", "-q:a", "2",
        output_path,
    ]
    subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    os.remove(list_file)
    return output_path


def generate_srt(audio_segments: list[dict], output_path: str) -> str:
    """生成 SRT 字幕文件"""
    with open(output_path, "w", encoding="utf-8") as f:
        for i, seg in enumerate(audio_segments, 1):
            start = _format_srt_time(seg["start_time"])
            end_time = seg["start_time"] + seg.get("duration", seg["end_time"] - seg["start_time"])
            end = _format_srt_time(end_time)
            f.write(f"{i}\n{start} --> {end}\n{seg['text']}\n\n")

    logger.info(f"✅ 字幕文件生成: {output_path}")
    return output_path


# ══════════════════════════════════════════════════════════
#  工具函数
# ══════════════════════════════════════════════════════════

def _get_audio_duration(filepath: str) -> float:
    """获取音频文件时长"""
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json",
         "-show_format", filepath],
        capture_output=True, text=True,
    )
    try:
        data = json.loads(result.stdout)
        return float(data.get("format", {}).get("duration", 0))
    except (json.JSONDecodeError, ValueError):
        return 0.0


def _format_srt_time(seconds: float) -> str:
    """秒数转 SRT 时间格式 HH:MM:SS,mmm"""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds % 1) * 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"
