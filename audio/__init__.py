"""TTS 配音模块 — 多后端：GLM-TTS / TTSMaker / CosyVoice2 / Edge-TTS（兜底）"""

import asyncio
import base64
import json
import logging
import os
import subprocess
import struct
import time
import wave
from pathlib import Path

import httpx

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
        backend = _detect_backend(tts_cfg)

    logger.info(f"🔊 TTS 后端: {backend}")

    if backend == "glm-tts":
        return _generate_glm_tts(tts_cfg, segments, output_dir)
    elif backend == "ttsmaker":
        return _generate_ttsmaker(tts_cfg, segments, output_dir)
    elif backend == "cosyvoice2":
        return _generate_cosyvoice2(tts_cfg, segments, output_dir)
    elif backend == "edge-tts":
        return _generate_edge_tts(tts_cfg, segments, output_dir)
    else:
        raise ValueError(f"不支持的 TTS 后端: {backend}")


def _detect_backend(tts_cfg: dict = None) -> str:
    """检测可用的 TTS 后端，优先级: GLM-TTS > CosyVoice2 > Edge-TTS"""
    tts_cfg = tts_cfg or {}

    # 0. 检查 GLM-TTS（有 API key 即可用）
    glm_cfg = tts_cfg.get("glm_tts", {})
    if glm_cfg.get("api_key"):
        logger.info("✅ 检测到 GLM-TTS 配置（智谱 API）")
        return "glm-tts"

    # 0.5 检查 TTSMaker（有 token 即可用）
    ttsmaker_cfg = tts_cfg.get("ttsmaker", {})
    if ttsmaker_cfg.get("token"):
        logger.info("✅ 检测到 TTSMaker 配置")
        return "ttsmaker"

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

    raise RuntimeError("没有可用的 TTS 后端！请配置 GLM-TTS API Key、安装 CosyVoice2 或 edge-tts")


# ══════════════════════════════════════════════════════════
#  GLM-TTS 后端（智谱 AI — 超拟人语音合成）
# ══════════════════════════════════════════════════════════

GLM_TTS_URL = "https://open.bigmodel.cn/api/paas/v4/audio/speech"
GLM_MAX_INPUT_LEN = 1024  # API 限制单次最大 1024 字符


def _generate_glm_tts(tts_cfg: dict, segments: list[dict], output_dir: str) -> list[dict]:
    """
    GLM-TTS 语音合成 — 智谱 AI 的超拟人 TTS。
    特性：情感表达强、语调自然、支持多音色。
    API: POST /paas/v4/audio/speech, 返回 wav 二进制。
    """
    glm_cfg = tts_cfg.get("glm_tts", {})
    api_key = glm_cfg.get("api_key", "")
    voice = glm_cfg.get("voice", "tongtong")  # 默认彤彤
    speed = glm_cfg.get("speed", 1.0)
    volume = glm_cfg.get("volume", 1.0)

    if not api_key:
        raise ValueError("GLM-TTS 需要 API Key，请在 config.yaml tts.glm_tts.api_key 中配置")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    results = []

    for i, seg in enumerate(segments):
        text = seg["text"].strip()
        if not text:
            continue

        output_path = os.path.join(output_dir, f"tts_{i:03d}.wav")
        logger.info(f"  🎤 [{i+1}/{len(segments)}] GLM-TTS ({voice}): {text[:40]}...")

        try:
            # GLM-TTS 限制 1024 字符，超长则截断（一般一段不会超）
            if len(text) > GLM_MAX_INPUT_LEN:
                logger.warning(f"  ⚠️ 文本超长 ({len(text)} 字)，截断至 {GLM_MAX_INPUT_LEN}")
                text = text[:GLM_MAX_INPUT_LEN]

            payload = {
                "model": "glm-tts",
                "input": text,
                "voice": voice,
                "speed": speed,
                "volume": volume,
                "response_format": "wav",
            }

            t0 = time.time()
            resp = httpx.post(
                GLM_TTS_URL,
                headers=headers,
                json=payload,
                timeout=60,
            )
            t1 = time.time()

            if resp.status_code == 200:
                content_type = resp.headers.get("content-type", "")
                if "audio" in content_type or "octet-stream" in content_type:
                    # 直接返回音频二进制
                    with open(output_path, "wb") as f:
                        f.write(resp.content)
                else:
                    # 可能返回 JSON 包含 base64
                    try:
                        data = resp.json()
                        if "audio" in data:
                            audio_bytes = base64.b64decode(data["audio"])
                            with open(output_path, "wb") as f:
                                f.write(audio_bytes)
                        else:
                            logger.error(f"  GLM-TTS 返回格式异常: {str(data)[:200]}")
                            continue
                    except Exception:
                        # 尝试直接写入
                        with open(output_path, "wb") as f:
                            f.write(resp.content)

                size_kb = os.path.getsize(output_path) / 1024
                logger.info(f"  ✅ [{i+1}] {t1-t0:.1f}s, {size_kb:.0f}KB")
            else:
                error_text = resp.text[:300]
                logger.error(f"  ❌ GLM-TTS API 错误 [{resp.status_code}]: {error_text}")
                # 如果是限流，等一下再试
                if resp.status_code == 429:
                    logger.info("  ⏳ 限流，等待 5 秒...")
                    time.sleep(5)
                    # 重试一次
                    resp = httpx.post(GLM_TTS_URL, headers=headers, json=payload, timeout=60)
                    if resp.status_code == 200:
                        with open(output_path, "wb") as f:
                            f.write(resp.content)
                    else:
                        continue
                else:
                    continue

        except httpx.TimeoutException:
            logger.error(f"  ⏰ GLM-TTS 超时 (段 {i})")
            continue
        except Exception as e:
            logger.error(f"  ❌ GLM-TTS 合成失败 (段 {i}): {e}")
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

    logger.info(f"✅ GLM-TTS 合成完成: {len(results)}/{len(segments)} 段")
    return results


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
#  TTSMaker 后端（免费 API，语音质量较高）
# ══════════════════════════════════════════════════════════

TTSMAKER_API_URL = "https://api.ttsmaker.cn/v1/create-tts-order"
TTSMAKER_TOKEN_STATUS_URL = "https://api.ttsmaker.cn/v1/get-token-status"


def _generate_ttsmaker(tts_cfg: dict, segments: list[dict], output_dir: str) -> list[dict]:
    """
    TTSMaker TTS — 免费在线 API，质量优于 Edge-TTS。
    免费配额：50,000 字符/周期（约 28 天）。
    API: POST /v1/create-tts-order，返回音频 URL。
    推荐音色：15032 (阿伟v2 长文版, 单次 ≤10000 字符)
    """
    ttsmaker_cfg = tts_cfg.get("ttsmaker", {})
    token = ttsmaker_cfg.get("token", "ttsmaker_demo_token")
    voice_id = ttsmaker_cfg.get("voice_id", 15032)  # 阿伟v2 长文版
    audio_format = ttsmaker_cfg.get("audio_format", "mp3")
    audio_speed = ttsmaker_cfg.get("audio_speed", 1.0)
    audio_volume = ttsmaker_cfg.get("audio_volume", 0)  # 0=原始音量
    paragraph_pause = ttsmaker_cfg.get("paragraph_pause_time", 0)

    # 先查配额
    try:
        status_resp = httpx.post(TTSMAKER_TOKEN_STATUS_URL,
                                  json={"token": token}, timeout=10)
        status = status_resp.json().get("token_status", {})
        available = status.get("current_cycle_characters_available", 0)
        total_chars = sum(len(seg["text"].strip()) for seg in segments)
        logger.info(f"📊 TTSMaker 配额: 剩余 {available} 字符, 本次需要 {total_chars} 字符")
        if available < total_chars:
            logger.warning(f"⚠️ TTSMaker 配额不足 (需 {total_chars}, 剩 {available})，"
                          f"将在配额耗尽后降级到 Edge-TTS")
    except Exception as e:
        logger.warning(f"⚠️ TTSMaker 配额查询失败: {e}")

    results = []
    fallback_to_edge = False

    for i, seg in enumerate(segments):
        text = seg["text"].strip()
        if not text:
            continue

        ext = audio_format if audio_format in ("mp3", "wav", "ogg") else "mp3"
        output_path = os.path.join(output_dir, f"tts_{i:03d}.{ext}")
        logger.info(f"  🎤 [{i+1}/{len(segments)}] TTSMaker: {text[:40]}...")

        if fallback_to_edge:
            # 配额耗尽，降级到 Edge-TTS
            _edge_tts_single(tts_cfg, seg, output_path, i, len(segments))
            duration = _get_audio_duration(output_path)
            results.append({
                "index": i, "audio_path": output_path, "text": text,
                "start_time": seg["start_time"], "end_time": seg["end_time"],
                "duration": duration,
            })
            continue

        try:
            payload = {
                "token": token,
                "text": text,
                "voice_id": voice_id,
                "audio_format": audio_format,
                "audio_speed": audio_speed,
                "audio_volume": audio_volume,
                "text_paragraph_pause_time": paragraph_pause,
            }

            t0 = time.time()
            resp = httpx.post(TTSMAKER_API_URL, json=payload, timeout=60)
            data = resp.json()
            t1 = time.time()

            if data.get("status") == "success" and data.get("audio_file_url"):
                # 下载音频文件
                audio_url = data["audio_file_url"]
                audio_resp = httpx.get(audio_url, timeout=30)
                with open(output_path, "wb") as f:
                    f.write(audio_resp.content)

                size_kb = os.path.getsize(output_path) / 1024
                chars_used = data.get("tts_order_characters", len(text))
                logger.info(f"  ✅ [{i+1}] {t1-t0:.1f}s, {size_kb:.0f}KB, {chars_used}字符")
            else:
                error_code = data.get("error_code", "UNKNOWN")
                error_msg = data.get("error_details", "未知错误")
                logger.warning(f"  ⚠️ TTSMaker 错误 [{error_code}]: {error_msg}")

                if "QUOTA" in str(error_code).upper() or "CHARACTER" in str(error_code).upper():
                    logger.info("  🔄 配额耗尽，后续段降级到 Edge-TTS")
                    fallback_to_edge = True
                    _edge_tts_single(tts_cfg, seg, output_path, i, len(segments))
                else:
                    # 其他错误也用 Edge-TTS 兜底
                    _edge_tts_single(tts_cfg, seg, output_path, i, len(segments))

        except httpx.TimeoutException:
            logger.error(f"  ⏰ TTSMaker 超时 (段 {i})，降级 Edge-TTS")
            _edge_tts_single(tts_cfg, seg, output_path, i, len(segments))
        except Exception as e:
            logger.error(f"  ❌ TTSMaker 失败 (段 {i}): {e}，降级 Edge-TTS")
            _edge_tts_single(tts_cfg, seg, output_path, i, len(segments))

        duration = _get_audio_duration(output_path)
        results.append({
            "index": i, "audio_path": output_path, "text": text,
            "start_time": seg["start_time"], "end_time": seg["end_time"],
            "duration": duration,
        })

        # TTSMaker 限流：每次请求间隔避免太快
        if i < len(segments) - 1 and not fallback_to_edge:
            time.sleep(1)

    logger.info(f"✅ TTSMaker 合成完成: {len(results)}/{len(segments)} 段")
    return results


def _edge_tts_single(tts_cfg: dict, seg: dict, output_path: str, idx: int, total: int):
    """单段 Edge-TTS 降级合成"""
    import edge_tts

    edge_cfg = tts_cfg.get("edge_tts", {})
    voice = edge_cfg.get("voice", "zh-CN-YunjianNeural")
    rate = edge_cfg.get("rate", "+5%")
    pitch = edge_cfg.get("pitch", "-5Hz")

    text = seg["text"].strip()
    logger.info(f"  🎤 [{idx+1}/{total}] Edge-TTS (降级): {text[:30]}...")

    # 确保输出是 mp3
    if not output_path.endswith(".mp3"):
        output_path = output_path.rsplit(".", 1)[0] + ".mp3"

    communicate = edge_tts.Communicate(text=text, voice=voice, rate=rate, pitch=pitch)
    asyncio.run(communicate.save(output_path))


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
