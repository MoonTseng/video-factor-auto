"""二创文案生成 - Whisper 转录日语原音 → Claude 生成中文解说脚本"""

import json
import logging
import os
import re
import time

import httpx

logger = logging.getLogger(__name__)


# ── Bedrock 代理 LLM 调用 ────────────────────────────────

def _call_llm(config: dict, messages: list[dict], max_tokens: int = 4000) -> str:
    """
    统一 LLM 调用接口,支持 Bedrock 代理模式.
    返回纯文本响应.
    """
    llm_cfg = config.get("llm", {})
    backend = llm_cfg.get("backend", "bedrock_proxy")

    if backend == "anthropic_proxy":
        return _call_anthropic_proxy(llm_cfg, messages, max_tokens)
    elif backend == "bedrock_proxy":
        return _call_bedrock_proxy(llm_cfg, messages, max_tokens)
    elif backend == "openai":
        return _call_openai(llm_cfg, messages, max_tokens)
    elif backend == "sakura":
        return _call_sakura(llm_cfg, messages, max_tokens)
    else:
        # 直接 Anthropic API 兜底
        import anthropic
        api_key = llm_cfg.get("api_key", "")
        model = llm_cfg.get("model", "claude-sonnet-4-20250514")
        client = anthropic.Anthropic(api_key=api_key)
        resp = client.messages.create(model=model, max_tokens=max_tokens, messages=messages)
        return resp.content[0].text.strip()


def _call_anthropic_proxy(llm_cfg: dict, messages: list[dict], max_tokens: int) -> str:
    """通过公司 Anthropic 代理调用 Claude (Messages API 格式), 自动重试"""
    proxy_cfg = llm_cfg.get("anthropic_proxy", {})
    base_url = proxy_cfg.get("base_url", "").rstrip("/")
    api_key = proxy_cfg.get("api_key", "")
    model = proxy_cfg.get("model", "claude-opus-4-6")

    url = f"{base_url}/v1/messages"

    # 分离 system message
    system_text = None
    user_messages = []
    for m in messages:
        if m.get("role") == "system":
            system_text = m["content"]
        else:
            user_messages.append(m)

    body = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": user_messages or messages,
    }
    if system_text:
        body["system"] = system_text

    headers = {
        "Content-Type": "application/json",
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
    }

    logger.info(f"   🤖 调用 Anthropic 代理: {model}")

    max_retries = 3
    for attempt in range(max_retries):
        try:
            resp = httpx.post(url, headers=headers, json=body, timeout=120)

            if resp.status_code == 200:
                data = resp.json()
                return data["content"][0]["text"].strip()

            if resp.status_code in (502, 503, 429, 529) and attempt < max_retries - 1:
                wait = (attempt + 1) * 10
                logger.warning(f"   ⚠️ Anthropic {resp.status_code}, {wait}s 后重试 ({attempt+1}/{max_retries})...")
                import time
                time.sleep(wait)
                continue

            raise RuntimeError(f"Anthropic 代理调用失败 ({resp.status_code}): {resp.text[:500]}")

        except (httpx.ConnectError, httpx.ReadTimeout) as e:
            if attempt < max_retries - 1:
                wait = (attempt + 1) * 10
                logger.warning(f"   ⚠️ 网络错误: {e}, {wait}s 后重试 ({attempt+1}/{max_retries})...")
                import time
                time.sleep(wait)
                continue
            raise


def _call_bedrock_proxy(llm_cfg: dict, messages: list[dict], max_tokens: int) -> str:
    """通过公司 Bedrock 代理调用 Claude(/model/{modelId}/invoke),自动重试"""
    proxy_cfg = llm_cfg.get("bedrock_proxy", {})
    base_url = proxy_cfg.get("base_url", "")
    auth_token = proxy_cfg.get("auth_token", "")
    model = proxy_cfg.get("model", "us.anthropic.claude-opus-4-6-v1")

    url = f"{base_url}/model/{model}/invoke"

    body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": max_tokens,
        "messages": messages,
    }

    logger.info(f"   🤖 调用 Bedrock 代理: {model}")

    max_retries = 3
    for attempt in range(max_retries):
        try:
            resp = httpx.post(
                url,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {auth_token}",
                },
                json=body,
                timeout=120,
            )

            if resp.status_code == 200:
                data = resp.json()
                return data["content"][0]["text"].strip()

            if resp.status_code in (502, 503, 429) and attempt < max_retries - 1:
                wait = (attempt + 1) * 10
                logger.warning(f"   ⚠️ Bedrock {resp.status_code}, {wait}s 后重试 ({attempt+1}/{max_retries})...")
                import time
                time.sleep(wait)
                continue

            raise RuntimeError(f"Bedrock 代理调用失败 ({resp.status_code}): {resp.text[:500]}")

        except (httpx.ConnectError, httpx.ReadTimeout) as e:
            if attempt < max_retries - 1:
                wait = (attempt + 1) * 10
                logger.warning(f"   ⚠️ 网络错误: {e}, {wait}s 后重试 ({attempt+1}/{max_retries})...")
                import time
                time.sleep(wait)
                continue
            raise


def _call_openai(llm_cfg: dict, messages: list[dict], max_tokens: int) -> str:
    """通过 OpenAI 兼容接口调用 LLM(支持小米 MiMo 等)"""
    import os
    from openai import OpenAI

    oa_cfg = llm_cfg.get("openai", {})
    api_key = oa_cfg.get("api_key") or os.getenv("XIAOMI_API_KEY", "")
    base_url = oa_cfg.get("base_url") or os.getenv("XIAOMI_BASE_URL", "")
    model = oa_cfg.get("model", "mimo-v2-pro")

    client = OpenAI(api_key=api_key, base_url=base_url)

    # 转换消息格式:Anthropic 用 system 角色,OpenAI 也支持
    resp = client.chat.completions.create(
        model=model,
        messages=messages,
        max_tokens=max_tokens,
    )
    return resp.choices[0].message.content.strip()


# SakuraLLM server 健康检查状态缓存
_sakura_health_checked = {"ok": False, "last_check": 0}


def _check_sakura_health(base_url: str) -> bool:
    """检查 SakuraLLM server 是否存活（带缓存，60s 内不重复检查）"""
    import time
    now = time.time()
    if _sakura_health_checked["ok"] and now - _sakura_health_checked["last_check"] < 60:
        return True

    try:
        resp = httpx.get(f"{base_url.rstrip('/').replace('/v1', '')}/health",
                         timeout=5)
        alive = resp.status_code == 200
    except Exception:
        # 有些 server 没有 /health，尝试 /v1/models
        try:
            resp = httpx.get(f"{base_url}/models", timeout=5)
            alive = resp.status_code == 200
        except Exception:
            alive = False

    _sakura_health_checked["ok"] = alive
    _sakura_health_checked["last_check"] = now
    return alive


def _call_sakura(llm_cfg: dict, messages: list[dict], max_tokens: int) -> str:
    """通过 SakuraLLM 翻译日语 → 中文 (OpenAI 兼容接口)

    SakuraLLM 是专门针对日语→中文翻译训练的模型,比通用 LLM 的日中翻译
    质量更高、成功率更稳定,特别适合动漫/影视/日常用语。

    增强特性：
    - 预检查 server 存活（避免超时等待）
    - 动态超时（根据输入文本长度自动调整）
    - 请求耗时日志
    - 区分可重试/不可重试错误
    """
    import time

    sakura_cfg = llm_cfg.get("sakura", {})
    base_url = sakura_cfg.get("base_url", "http://127.0.0.1:8080/v1")
    api_key = sakura_cfg.get("api_key", "sakura")
    model = sakura_cfg.get("model", "sakura-7b-qwen2.5-v1.0-q6k")
    temperature = sakura_cfg.get("temperature", 0.1)
    top_p = sakura_cfg.get("top_p", 0.3)
    frequency_penalty = sakura_cfg.get("frequency_penalty", 0.2)
    repetition_penalty = sakura_cfg.get("repetition_penalty", 1.1)  # 抑制重复输出

    # SakuraLLM 使用特定的 system prompt
    sakura_system = ("你是一个轻小说翻译模型，可以流畅通顺地以日本轻小说的风格"
                     "将日文翻译成简体中文，并联系上下文正确使用人称代词，"
                     "不擅自添加原文中没有的内容，也不擅自增加或减少换行。")

    # 转换消息格式：如果传入的是通用 prompt，提取日文内容用 SakuraLLM 格式
    sakura_messages = [{"role": "system", "content": sakura_system}]
    for m in messages:
        if m.get("role") == "system":
            continue  # 用 SakuraLLM 自己的 system prompt
        sakura_messages.append(m)

    # 预检查 server 存活
    if not _check_sakura_health(base_url):
        raise RuntimeError(f"SakuraLLM server 不可用 ({base_url})，请检查是否已启动: scripts/start_sakura.sh")

    # 动态超时：基于输入文本长度，基准 60s + 每 100 字符 +10s，上限 300s
    input_chars = sum(len(m.get("content", "")) for m in sakura_messages)
    timeout_sec = min(300, max(60, 60 + input_chars // 10))

    logger.info(f"   🌸 调用 SakuraLLM: {model} @ {base_url} (timeout={timeout_sec}s)")

    max_retries = 3
    for attempt in range(max_retries):
        t_start = time.time()
        try:
            resp = httpx.post(
                f"{base_url}/chat/completions",
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {api_key}",
                },
                json={
                    "model": model,
                    "messages": sakura_messages,
                    "temperature": temperature,
                    "top_p": top_p,
                    "max_tokens": max_tokens,
                    "frequency_penalty": frequency_penalty,
                    "repetition_penalty": repetition_penalty,
                    "do_sample": temperature > 0,
                },
                timeout=timeout_sec,
            )
            elapsed = time.time() - t_start

            if resp.status_code == 200:
                data = resp.json()
                result = data["choices"][0]["message"]["content"].strip()
                # 计算翻译速度
                usage = data.get("usage", {})
                tokens = usage.get("completion_tokens", len(result))
                tps = tokens / elapsed if elapsed > 0 else 0
                logger.info(f"   🌸 SakuraLLM 响应: {elapsed:.1f}s, ~{tps:.1f} tok/s, {len(result)} chars")
                return result

            # 不可重试错误：400(格式错误) 401(认证) 404(接口不存在)
            if resp.status_code in (400, 401, 404):
                raise RuntimeError(f"SakuraLLM 不可重试错误 ({resp.status_code}): {resp.text[:500]}")

            # 可重试错误：502/503(server 问题) 429(限流)
            if resp.status_code in (502, 503, 429) and attempt < max_retries - 1:
                wait = (2 ** attempt) * 5  # 5s, 10s, 20s 指数退避
                logger.warning(f"   ⚠️ SakuraLLM {resp.status_code} ({elapsed:.1f}s), "
                               f"{wait}s 后重试 ({attempt+1}/{max_retries})...")
                time.sleep(wait)
                _sakura_health_checked["ok"] = False  # 标记需要重新检查
                continue

            raise RuntimeError(f"SakuraLLM 调用失败 ({resp.status_code}): {resp.text[:500]}")

        except (httpx.ConnectError, httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout) as e:
            elapsed = time.time() - t_start
            if attempt < max_retries - 1:
                wait = (2 ** attempt) * 5
                logger.warning(f"   ⚠️ SakuraLLM 网络错误 ({elapsed:.1f}s): {e}, "
                               f"{wait}s 后重试 ({attempt+1}/{max_retries})...")
                time.sleep(wait)
                _sakura_health_checked["ok"] = False
                continue
            raise RuntimeError(f"SakuraLLM 连接失败 (重试 {max_retries} 次, 最后耗时 {elapsed:.1f}s): {e}")


# ── 语音转录 ──────────────────────────────────────────────
# 支持两种后端:
#   1. deepgram (推荐): Deepgram nova-3 API, 快速准确, 适合 16GB Mac
#   2. local (fallback): 本地 faster-whisper large-v3, 需要大内存/GPU


def _transcribe_deepgram(config: dict, audio_path: str) -> tuple[list[dict], dict]:
    """
    使用 Deepgram nova-3 API 转录日语音频.

    返回 (segments, info) 其中:
      segments: [{start, end, text}, ...]
      info: {language, language_probability, duration}

    优势: 9分钟音频约40秒完成, 无需本地GPU/大内存
    """
    whisper_cfg = config.get("whisper", {})
    dg_cfg = whisper_cfg.get("deepgram", {})
    api_key = dg_cfg.get("api_key", "")
    if not api_key:
        raise ValueError("Deepgram API key 未配置 (whisper.deepgram.api_key)")

    model = dg_cfg.get("model", "nova-3")
    language = dg_cfg.get("language", "ja")

    logger.info(f"🎙️ Deepgram 转录中 (model={model}, lang={language})...")
    logger.info(f"   音频文件: {audio_path}")

    # 读取音频文件
    file_size = os.path.getsize(audio_path)
    logger.info(f"   文件大小: {file_size / 1024 / 1024:.1f} MB")

    # 根据文件扩展名确定 Content-Type
    ext = os.path.splitext(audio_path)[1].lower()
    content_types = {
        ".mp3": "audio/mpeg",
        ".wav": "audio/wav",
        ".m4a": "audio/mp4",
        ".flac": "audio/flac",
        ".ogg": "audio/ogg",
        ".opus": "audio/opus",
        ".aac": "audio/aac",
    }
    content_type = content_types.get(ext, "audio/mpeg")

    # 构建 API 请求
    url = "https://api.deepgram.com/v1/listen"
    params = {
        "model": model,
        "language": language,
        "smart_format": "true",
        "utterances": "true",
        "punctuate": "true",
    }
    headers = {
        "Authorization": f"Token {api_key}",
        "Content-Type": content_type,
    }

    t_start = time.time()

    with open(audio_path, "rb") as f:
        audio_data = f.read()

    # 发送请求（超时5分钟，大文件需要时间上传+处理）
    resp = httpx.post(
        url,
        params=params,
        headers=headers,
        content=audio_data,
        timeout=300.0,
    )

    if resp.status_code != 200:
        raise RuntimeError(
            f"Deepgram API 错误 {resp.status_code}: {resp.text[:500]}"
        )

    result = resp.json()
    t_elapsed = time.time() - t_start

    # 解析 utterances
    segments = []
    utterances = result.get("results", {}).get("utterances", [])
    for u in utterances:
        text = u.get("transcript", "").strip()
        if text:
            segments.append({
                "start": round(u["start"], 2),
                "end": round(u["end"], 2),
                "text": text,
            })

    # 提取元信息
    metadata = result.get("metadata", {})
    duration = metadata.get("duration", 0)
    channels = result.get("results", {}).get("channels", [])
    detected_lang = language
    lang_prob = 0.95  # Deepgram 不返回概率，给高置信度

    if channels:
        alt = channels[0].get("alternatives", [{}])
        if alt and alt[0].get("languages"):
            detected_lang = alt[0]["languages"][0]

    info = {
        "language": detected_lang,
        "language_probability": lang_prob,
        "duration": duration,
    }

    logger.info(f"✅ Deepgram 转录完成: {len(segments)} 段, "
                f"{sum(len(s['text']) for s in segments)} 字符")
    logger.info(f"   耗时: {t_elapsed:.1f}s, 音频时长: {duration:.1f}s")
    if duration > 0:
        logger.info(f"   速度: {t_elapsed/duration:.2f}x 实时率 "
                     f"(比本地 Whisper 快 ~{int(duration/t_elapsed)}x)")

    return segments, info


# ── 本地 Whisper 模型实例缓存 ──
# large-v3 加载约 30s（CPU int8），缓存后同一进程多次转录无需重新加载
_whisper_model_cache: dict = {}  # key: "model_size:device:compute_type" -> WhisperModel


def _get_whisper_model(model_size: str, device: str, compute_type: str):
    """获取或缓存 Whisper 模型实例，避免重复加载"""
    from faster_whisper import WhisperModel

    cache_key = f"{model_size}:{device}:{compute_type}"
    if cache_key in _whisper_model_cache:
        logger.info(f"   💾 复用已缓存的 Whisper 模型 ({cache_key})")
        return _whisper_model_cache[cache_key]

    logger.info(f"   📥 首次加载 Whisper 模型 ({cache_key})...")
    t_start = time.time()
    model = WhisperModel(model_size, device=device, compute_type=compute_type)
    elapsed = time.time() - t_start
    logger.info(f"   ✅ 模型加载完成: {elapsed:.1f}s")

    _whisper_model_cache[cache_key] = model
    return model


def transcribe_source(config: dict, audio_path: str, cache_dir: str = "") -> list[dict]:
    """
    日语音频转文字(带时间戳). 返回 [{start, end, text}, ...]

    后端选择 (config.whisper.backend):
      - "deepgram" (默认/推荐): Deepgram nova-3 API, 9min≈40s, 无需本地资源
      - "local": 本地 faster-whisper large-v3, 需大内存, 16GB Mac 会卡死

    特性:
      - 自动 fallback: Deepgram 失败自动降级到本地 Whisper
      - 转录缓存: 音频未变化时跳过重复转录
      - 后处理: hallucination 检测、去重、短句合并
      - 质量评估: 日语字符比例、语音覆盖率
    """
    # ── 转录缓存检查 ──
    if cache_dir:
        cache_file = os.path.join(cache_dir, "transcript_cache.json")
    else:
        cache_file = audio_path + ".transcript_cache.json"

    if os.path.exists(cache_file):
        try:
            with open(cache_file, "r") as f:
                cache_data = json.load(f)
            audio_stat = os.stat(audio_path)
            if (cache_data.get("audio_mtime") == audio_stat.st_mtime
                    and cache_data.get("audio_size") == audio_stat.st_size
                    and cache_data.get("transcript")):
                logger.info(f"💾 使用转录缓存 ({len(cache_data['transcript'])} 段)")
                return cache_data["transcript"]
        except Exception:
            pass  # 缓存损坏，重新转录

    whisper_cfg = config.get("whisper", {})
    backend = whisper_cfg.get("backend", "deepgram")

    # ── 分发到对应后端 ──
    raw_transcript = []
    audio_duration = 0
    info = {"language": "ja", "language_probability": 0.0, "duration": 0}

    if backend == "deepgram":
        try:
            segments, info = _transcribe_deepgram(config, audio_path)
            raw_transcript = segments
            audio_duration = info.get("duration", 0)
        except Exception as e:
            logger.warning(f"⚠️ Deepgram 转录失败: {e}")
            logger.info("🔄 降级到本地 Whisper...")
            backend = "local"  # fallback

    if backend == "local":
        raw_transcript, info, audio_duration = _transcribe_local_whisper(
            config, audio_path
        )

    # ── 后处理：提升转录质量 ──
    transcript = _postprocess_transcript(raw_transcript)

    # ── 质量评估 ──
    if transcript:
        all_text = "".join(s["text"] for s in transcript)
        total_chars = len(all_text)
        if total_chars > 0:
            ja_chars = len(re.findall(r'[぀-ゟ゠-ヿ]', all_text))  # 假名
            zh_chars = len(re.findall(r'[一-鿿]', all_text))  # 汉字
            ja_ratio = (ja_chars + zh_chars) / total_chars
            logger.info(f"   📊 语言分析: 假名 {ja_chars} + 汉字 {zh_chars} / 总 {total_chars} "
                         f"= {ja_ratio:.1%} 日语相关字符")
            if ja_ratio < 0.3:
                logger.warning(f"   ⚠️ 日语字符比例偏低 ({ja_ratio:.1%})，可能不是日语内容")

        if audio_duration > 0:
            speech_time = sum(s["end"] - s["start"] for s in transcript)
            coverage = speech_time / audio_duration
            logger.info(f"   📊 语音覆盖: {speech_time:.1f}s / {audio_duration:.1f}s = {coverage:.1%}")

    # ── 保存转录缓存 ──
    try:
        audio_stat = os.stat(audio_path)
        cache_data = {
            "audio_mtime": audio_stat.st_mtime,
            "audio_size": audio_stat.st_size,
            "transcript": transcript,
            "backend": backend,
            "info": {
                "language": info.get("language", "ja") if isinstance(info, dict) else getattr(info, "language", "ja"),
                "language_probability": info.get("language_probability", 0) if isinstance(info, dict) else getattr(info, "language_probability", 0),
                "duration": audio_duration,
            }
        }
        cache_path = cache_dir + "/transcript_cache.json" if cache_dir else audio_path + ".transcript_cache.json"
        os.makedirs(os.path.dirname(cache_path) if os.path.dirname(cache_path) else ".", exist_ok=True)
        with open(cache_path, "w") as f:
            json.dump(cache_data, f, ensure_ascii=False)
        logger.info(f"   💾 转录缓存已保存")
    except Exception as e:
        logger.debug(f"   转录缓存保存失败: {e}")

    return transcript


def _transcribe_local_whisper(config: dict, audio_path: str) -> tuple[list[dict], dict, float]:
    """
    本地 faster-whisper 转录（fallback 后端）.
    返回 (segments, info_dict, audio_duration)
    """
    whisper_cfg = config.get("whisper", {})
    model_size = whisper_cfg.get("model_size", "large-v3")
    device = whisper_cfg.get("device", "cpu")
    compute_type = whisper_cfg.get("compute_type", "int8")

    logger.info(f"🎙️ 本地 Whisper 转录中 (model={model_size}, device={device})...")
    logger.info(f"   音频文件: {audio_path}")

    beam_size = whisper_cfg.get("beam_size", 5)
    condition_on_previous = whisper_cfg.get("condition_on_previous_text", True)
    no_speech_threshold = whisper_cfg.get("no_speech_threshold", 0.6)
    compression_ratio_threshold = whisper_cfg.get("compression_ratio_threshold", 2.4)
    initial_prompt = whisper_cfg.get("initial_prompt", "字幕 テレビ ドラマ 映画 Netflix 予告編")

    vad_cfg = whisper_cfg.get("vad_parameters", {})
    vad_params = {
        "min_silence_duration_ms": vad_cfg.get("min_silence_duration_ms", 300),
        "speech_pad_ms": vad_cfg.get("speech_pad_ms", 200),
        "threshold": vad_cfg.get("threshold", 0.35),
    }

    model = _get_whisper_model(model_size, device, compute_type)

    # 语言检测
    detected_lang = "ja"
    try:
        _, detect_info = model.transcribe(
            audio_path, language=None, beam_size=1,
            vad_filter=True, word_timestamps=False,
        )
        if detect_info.language_probability > 0.8:
            detected_lang = detect_info.language
            logger.info(f"   🔍 语言检测: {detected_lang} ({detect_info.language_probability:.1%})")
            if detected_lang != "ja":
                logger.warning(f"   ⚠️ 检测到非日语({detected_lang})，强制日语转录")
                detected_lang = "ja"
    except Exception:
        pass

    t_start = time.time()
    segments, info = model.transcribe(
        audio_path,
        language=detected_lang,
        beam_size=beam_size,
        vad_filter=True,
        vad_parameters=vad_params,
        condition_on_previous_text=condition_on_previous,
        no_speech_threshold=no_speech_threshold,
        compression_ratio_threshold=compression_ratio_threshold,
        initial_prompt=initial_prompt,
        word_timestamps=True,
    )

    raw_transcript = []
    for seg in segments:
        text = seg.text.strip()
        if text:
            raw_transcript.append({
                "start": round(seg.start, 2),
                "end": round(seg.end, 2),
                "text": text,
            })

    t_elapsed = time.time() - t_start
    audio_duration = info.duration if hasattr(info, "duration") and info.duration else 0

    total_text = sum(len(s["text"]) for s in raw_transcript)
    logger.info(f"✅ 本地转录完成: {len(raw_transcript)} 段, {total_text} 字符")
    logger.info(f"   检测语言: {info.language} ({info.language_probability:.1%}), 耗时: {t_elapsed:.1f}s")
    if audio_duration > 0:
        logger.info(f"   音频: {audio_duration:.1f}s, RTF: {t_elapsed/audio_duration:.2f}x")

    info_dict = {
        "language": info.language,
        "language_probability": info.language_probability,
        "duration": audio_duration,
    }

    return raw_transcript, info_dict, audio_duration


def _postprocess_transcript(segments: list[dict]) -> list[dict]:
    """
    转录后处理（增强版）：
    1. Hallucination 检测与过滤（重复模式、Whisper 幻觉、纯数字/极短段）
    2. 去除纯标点/符号段
    3. 去除连续重复段（含近似重复检测）
    4. 日语短句智能合并（<5字 + 间隔<1s → 合并，上限40字）
    5. 合并过短段
    6. 质量评估日志
    """
    if not segments:
        return segments

    stats = {"hallucination": 0, "punct_only": 0, "duplicate": 0,
             "too_short": 0, "merged_short": 0, "original": len(segments)}

    # ── Whisper 常见幻觉模式（扩充版）──
    hallucination_patterns = [
        re.compile(r'(ご視聴ありがとうございまし|ご覧いただきありがとうございま)'),  # 感谢观看（多次重复）
        re.compile(r'(チャンネル登録|高評価|グッドボタン|お気に入り登録)'),  # YouTube套话
        re.compile(r'(字幕|テロップ).*(提供|協力)'),  # 字幕提供
        re.compile(r'^(ん+|あ+|え+|う+|お+)$'),  # 纯语气词
        re.compile(r'^[\d\s\.\,]+$'),  # 纯数字
        # ── 扩充模式 ──
        re.compile(r'(お問い合わせ|お問合せ|ホームページ|ウェブサイト)'),  # 广告/引流
        re.compile(r'(サブスク|いいね|コメント).*(お願い|してね|よろしく)'),  # 求订阅
        re.compile(r'(www\.|\.com|\.jp|\.net|http)'),  # URL 残留
        re.compile(r'^(はい|ええ|うん|そう|ああ|ねえ|まあ)$'),  # 单独语气词（非对话上下文）
        re.compile(r'(\w)\1{4,}'),  # 连续重复字符 >=5 次（乱码）
        re.compile(r'^(Music|music|♪|BGM|SE)$', re.IGNORECASE),  # 音效标记
        re.compile(r'(最後まで|見てくれて).*(ありがとう)'),  # 片尾感谢
        re.compile(r'(次回|次の動画|また来週|また明日)'),  # 预告下集
    ]

    # 纯标点/符号正则
    punct_only_re = re.compile(
        r'^[\s\u3000'
        r'\u3001-\u3003'     # 、。〃
        r'\u3008-\u3011'     # 〈〉《》「」『』
        r'\u3014-\u301F'     # 〔〕〖〗【】〘〙〚〛〜〝〞〟
        r'\uFF01-\uFF0F'     # ！＂＃＄％＆＇（）＊＋，－．／
        r'\uFF1A-\uFF20'     # ：；＜＝＞？＠
        r'\uFF3B-\uFF40'     # ［＼］＾＿｀
        r'\uFF5B-\uFF65'     # ｛｜｝～
        r'!-/:-@\[-`{-~'    # ASCII 标点
        r'♪♫♬♩…─━─'         # 音乐符号和装饰线
        r']+$'
    )

    # ── 步骤0: Hallucination 检测 ──
    # 0a. 检测连续重复出现(>=3次)的文本 → 只保留第一次
    from collections import Counter
    text_counts = Counter(seg["text"] for seg in segments)
    seen_repeated = {}  # text -> count

    pre_filtered = []
    for seg in segments:
        text = seg["text"]

        # 幻觉模式匹配
        is_hallucination = False
        for pat in hallucination_patterns:
            if pat.search(text):
                is_hallucination = True
                break

        # 极短段过滤（<2字符，排除有意义的单字如"はい""ええ"）
        if len(text) < 2 and not re.search(r'[\u3040-\u309F\u30A0-\u30FF\u4e00-\u9fff]', text):
            stats["too_short"] += 1
            continue

        # 连续重复检测：同一文本出现 >=3 次，只保留前 2 次
        if text_counts[text] >= 3:
            seen_repeated[text] = seen_repeated.get(text, 0) + 1
            if seen_repeated[text] > 2:
                stats["hallucination"] += 1
                continue

        if is_hallucination:
            stats["hallucination"] += 1
            continue

        pre_filtered.append(seg)

    # ── 步骤1: 去除纯标点/符号段 ──
    filtered = []
    for seg in pre_filtered:
        if punct_only_re.match(seg["text"]):
            stats["punct_only"] += 1
        else:
            filtered.append(seg)

    # ── 步骤2: 去除连续重复段（含近似重复：前段包含后段或后段包含前段）──
    deduped = []
    for seg in filtered:
        if deduped:
            prev_text = deduped[-1]["text"]
            curr_text = seg["text"]
            # 完全相同
            if curr_text == prev_text:
                deduped[-1]["end"] = max(deduped[-1]["end"], seg["end"])
                stats["duplicate"] += 1
                continue
            # 近似重复：一个包含另一个且长度差 <30%
            if (len(curr_text) > 3 and len(prev_text) > 3):
                if (curr_text in prev_text or prev_text in curr_text):
                    shorter = min(len(curr_text), len(prev_text))
                    longer = max(len(curr_text), len(prev_text))
                    if shorter / longer > 0.7:
                        # 保留较长的
                        if len(curr_text) > len(prev_text):
                            deduped[-1]["text"] = curr_text
                        deduped[-1]["end"] = max(deduped[-1]["end"], seg["end"])
                        stats["duplicate"] += 1
                        continue
        deduped.append(seg)

    # ── 步骤3: 日语短句智能合并 ──
    # 两个短句（各<5字）间隔<1s → 合并为一句（上限40字）
    ja_merged = []
    for seg in deduped:
        if (ja_merged
                and len(seg["text"]) < 5
                and len(ja_merged[-1]["text"]) < 5
                and seg["start"] - ja_merged[-1]["end"] < 1.0
                and len(ja_merged[-1]["text"]) + len(seg["text"]) <= 40):
            ja_merged[-1]["end"] = seg["end"]
            ja_merged[-1]["text"] += seg["text"]
            stats["merged_short"] += 1
        else:
            ja_merged.append(seg)

    # ── 步骤4: 合并过短段（时长<0.5s 且文字<3字符 → 合并到前一段）──
    merged = []
    for seg in ja_merged:
        duration = seg["end"] - seg["start"]
        if (merged
                and duration < 0.5
                and len(seg["text"]) < 3):
            merged[-1]["end"] = seg["end"]
            merged[-1]["text"] += seg["text"]
            stats["merged_short"] += 1
        else:
            merged.append(seg)

    # ── 质量评估日志 ──
    removed = stats["original"] - len(merged)
    if removed > 0:
        details = []
        if stats["hallucination"]: details.append(f"幻觉{stats['hallucination']}")
        if stats["punct_only"]: details.append(f"纯标点{stats['punct_only']}")
        if stats["duplicate"]: details.append(f"重复{stats['duplicate']}")
        if stats["too_short"]: details.append(f"极短{stats['too_short']}")
        if stats["merged_short"]: details.append(f"短句合并{stats['merged_short']}")
        logger.info(f"   🔍 后处理: {stats['original']}→{len(merged)} 段 "
                     f"(清理 {removed}: {', '.join(details)})")

    return merged


# ── 话题选择(保留兼容接口) ──────────────────────────────

def select_topic(config: dict, topics: list[dict]) -> dict:
    """
    从候选视频中选择最适合二创的话题.
    优先选有 URL 的,播放量适中的.
    """
    # 先过滤有 URL 的
    with_url = [t for t in topics if t.get("url")]
    candidates = with_url if with_url else topics

    if not candidates:
        raise ValueError("没有可用的候选视频")

    # 尝试让 Claude 选
    try:
        return _ai_select_topic(config, candidates)
    except Exception as e:
        logger.warning(f"⚠️ AI 选题失败: {e}, 使用默认选择")

    # 兜底:选播放量最高的那个
    return max(candidates, key=lambda t: t.get("view_count", 0))


def _ai_select_topic(config: dict, candidates: list[dict]) -> dict:
    """用 Claude 选择最适合二创的视频"""
    candidate_text = "\n".join(
        f"{i+1}. [{t['view_count']:,}播放] {t['title']}\n   {t.get('description', '')[:100]}"
        for i, t in enumerate(candidates[:10])
    )

    prompt = f"""从以下日本美食/旅行视频中，选择一个最适合做中文二创解说的。
选择标准：
1. 有故事性（匠人精神、个人奋斗、文化传承）
2. 画面会丰富（料理制作过程、街景、食物特写）
3. 容易引起中国观众共鸣

候选视频:
{candidate_text}

只回复数字编号（如: 3）"""

    raw = _call_llm(config, [{
        "role": "user",
        "content": prompt
    }], max_tokens=2000)

    try:
        choice = int(re.search(r'\d+', raw).group()) - 1
        if 0 <= choice < len(candidates):
            selected = candidates[choice]
            logger.info(f"🎯 AI 选择: {selected['title']}")
            return selected
    except (ValueError, AttributeError, IndexError):
        pass

    return candidates[0]


# ── 二创解说脚本生成 ──────────────────────────────────────

def generate_script(config: dict, topic: dict, transcript: list[dict]) -> dict:
    """
    Generate Chinese commentary script based on original video content + Japanese transcription using Claude.
    Style reference: Bilibili uploader 'Da Peng and You Watch World Food Together'.
    
    Returns {title, tags, description, segments: [{start_time, end_time, text, scene_desc}, ...]}
    """
    # 准备转录文本(带时间戳)
    transcript_text = _format_transcript(transcript)
    video_duration = transcript[-1]["end"] if transcript else 600

    # 计算合适的分段数(每 15-25 秒一段解说)
    segment_count = max(8, int(video_duration / 20))

    prompt = f"""You are a Chinese commentary scriptwriter for Bilibili food/travel videos.
Your style is similar to uploader 'Da Peng and You Watch World Food Together' - conversational, friendly, like chatting with friends,
while including knowledge popularization, occasional humor but not excessive.

Now there is a Japanese video that needs Chinese commentary:

[Video Title]{topic.get('title', '')}
[Video Description]{topic.get('description', '')}
[Video Theme]{topic.get('suggested_theme', topic.get('title', ''))}
[Video Duration]{video_duration} seconds
[Channel]{topic.get('channel', '')}

[Japanese Transcript (with timestamps)]
{transcript_text}

Please write a complete Chinese commentary script based on the above content.

Requirements:
1. [Not translation] - It is a brand new Chinese commentary based on the video content, tell the story in your own way
2. [Conversational] - Like chatting with the audience, 'Look at this master', 'To be honest, the first time I saw...'
3. [Rhythmic] - The beginning must be attractive (hook), the middle has ups and downs, and the ending has summary and reflection
4. [With knowledge] - Appropriately introduce ingredients, craftsmanship, cultural background, but don't read like a textbook
5. [Match the picture] - Each commentary corresponds to a time interval, must match the picture content
6. [Time alignment] - Divide into about {segment_count} segments, covering the entire video duration
7. [Moderate speed] - About 3-4 Chinese characters per second, not too dense, leave time for the audience to watch the picture

Output format (strict JSON):
{{
  "title": "Chinese video title (must be attractive, can use question marks or exclamation marks)",
  "tags": ["tag1", "tag2", ...],
  "description": "Video introduction (2-3 sentences)",
  "segments": [
    {{
      "start_time": 0.0,
      "end_time": 15.0,
      "text": "Chinese commentary text for this segment",
      "scene_desc": "Scene description (what's happening in the video)"
    }},
    ...
  ]
}}

Note:
- start_time and end_time are in seconds (floating point)
- The first segment starts at 0 or close to 0
- The last segment's end_time should be close to total video duration {video_duration:.0f}
- There can be short intervals between segments (let the audience watch the picture)
- The number of characters in each text segment = (end_time - start_time) * 3 approximately

Only output JSON, no other content."""

    logger.info(f"✍️ 生成二创解说脚本... (目标 ~{segment_count} 段)")

    raw_text = _call_llm(config, [{"role": "user", "content": prompt}], max_tokens=8000)

    # 解析 JSON(兼容 markdown 包裹)
    script = _parse_json_response(raw_text)

    if not script or "segments" not in script:
        raise ValueError(f"文案生成失败,无法解析 JSON:\n{raw_text[:500]}")

    # 验证和修正
    script = _validate_script(script, video_duration)

    logger.info(f"✅ 文案生成完成: {script['title']}")
    logger.info(f"   {len(script['segments'])} 段解说, "
                f"总字数 {sum(len(s['text']) for s in script['segments'])}")

    return script


# ── 预告片字幕翻译(日语 → 中文 SRT) ────────────────────

def translate_transcript_to_srt(config: dict, transcript: list[dict],
                                 output_path: str,
                                 batch_size: int = 10,
                                 video_title: str = "",
                                 video_theme: str = "") -> str:
    """
    将 Whisper 日语转录翻译成中文,并输出 SRT 字幕文件.
    分批翻译以避免超出 token 限制.

    参数:
        config: 配置字典
        transcript: [{start, end, text}, ...] - transcribe_source 的输出
        output_path: SRT 文件输出路径
        batch_size: 每批翻译多少条(默认 20)
        video_title: 视频标题(帮助 LLM 理解上下文,纠正转录错误)
        video_theme: 视频主题/类型(如 "Netflix预告片","日本美食")
    返回:
        SRT 文件路径
    """
    if not transcript:
        raise ValueError("转录为空,无法翻译")

    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    logger.info(f"🈶→🈳 翻译日语字幕: {len(transcript)} 条")

    # 检查是否使用 SakuraLLM 翻译后端
    llm_cfg = config.get("llm", {})
    translation_backend = llm_cfg.get("translation_backend", "")
    use_sakura = (translation_backend == "sakura" or llm_cfg.get("backend") == "sakura")

    if use_sakura:
        # 预检查 SakuraLLM 可用性
        sakura_base = llm_cfg.get("sakura", {}).get("base_url", "http://127.0.0.1:8080/v1")
        if _check_sakura_health(sakura_base):
            logger.info("   🌸 使用 SakuraLLM 专用日中翻译模型 (server 已确认存活)")
        else:
            logger.warning("   ⚠️ SakuraLLM server 不可用，自动降级为 Claude 翻译")
            use_sakura = False

        # SakuraLLM 7B 模型上下文窗口有限（~4K tokens），根据文本长度自适应 batch_size
        if use_sakura:
            avg_chars = sum(len(s["text"]) for s in transcript) / max(len(transcript), 1)
            if avg_chars > 40:
                sakura_batch = 3   # 长句，每批3条
            elif avg_chars > 20:
                sakura_batch = 5   # 中等，每批5条
            else:
                sakura_batch = 8   # 短句，每批8条
            if batch_size != sakura_batch:
                logger.info(f"   📐 SakuraLLM 自适应 batch_size: {batch_size} → {sakura_batch}"
                            f"（平均 {avg_chars:.0f} 字/条）")
                batch_size = sakura_batch

    # 翻译缓存：避免重试时重复翻译已成功的行
    cache_file = output_path + ".cache.json"
    translation_cache = {}
    if os.path.exists(cache_file):
        try:
            with open(cache_file, "r") as f:
                translation_cache = json.load(f)
            cached_count = len(translation_cache)
            if cached_count > 0:
                logger.info(f"   💾 加载翻译缓存: {cached_count} 条已翻译")
        except Exception:
            translation_cache = {}

    # 分批翻译
    all_translated = []
    total_batches = (len(transcript) + batch_size - 1) // batch_size
    t_translate_start = time.time()

    for i in range(0, len(transcript), batch_size):
        batch = transcript[i:i + batch_size]
        batch_num = i // batch_size + 1
        progress_pct = batch_num / total_batches * 100

        # 检查缓存命中
        uncached_batch = []
        cached_results = {}
        for j, seg in enumerate(batch):
            cache_key = f"{seg['start']:.2f}_{seg['text']}"
            if cache_key in translation_cache:
                cached_results[j] = translation_cache[cache_key]
            else:
                uncached_batch.append((j, seg))

        if not uncached_batch:
            # 全部命中缓存
            translated = []
            for j, seg in enumerate(batch):
                cache_key = f"{seg['start']:.2f}_{seg['text']}"
                translated.append({
                    "start": seg["start"], "end": seg["end"],
                    "ja_text": seg["text"], "zh_text": translation_cache[cache_key],
                })
            all_translated.extend(translated)
            logger.info(f"   [{progress_pct:5.1f}%] 批次 {batch_num}/{total_batches} - 全部缓存命中")
            continue

        logger.info(f"   [{progress_pct:5.1f}%] 批次 {batch_num}/{total_batches} "
                     f"({len(batch)} 条, {len(uncached_batch)} 需翻译)...")

        if use_sakura:
            translated = _translate_batch_sakura(config, batch,
                                                  video_title=video_title,
                                                  video_theme=video_theme)
        else:
            translated = _translate_batch(config, batch,
                                           video_title=video_title,
                                           video_theme=video_theme)

        # 更新缓存（只缓存有效翻译）
        for j, item in enumerate(translated):
            if j < len(batch):
                seg = batch[j]
                zh = item.get("zh_text", "")
                # 不缓存原文兜底的结果
                if zh and zh != seg["text"]:
                    cache_key = f"{seg['start']:.2f}_{seg['text']}"
                    translation_cache[cache_key] = zh

        all_translated.extend(translated)

        # 定期保存缓存（每 5 批或最后一批）
        if batch_num % 5 == 0 or batch_num == total_batches:
            try:
                with open(cache_file, "w") as f:
                    json.dump(translation_cache, f, ensure_ascii=False)
            except Exception:
                pass

    # 写入 SRT 文件
    _write_srt(all_translated, output_path)

    t_translate_total = time.time() - t_translate_start
    total_chars = sum(len(t.get("zh_text", "")) for t in all_translated)
    effective_count = sum(1 for t in all_translated if t.get("zh_text") != t.get("ja_text"))
    logger.info(f"✅ 字幕翻译完成: {output_path}")
    logger.info(f"   📊 {len(all_translated)} 条, {total_chars} 中文字符, "
                f"{effective_count} 有效翻译, 耗时 {t_translate_total:.1f}s")

    # 清理缓存文件
    if os.path.exists(cache_file):
        os.unlink(cache_file)

    return output_path


def _translate_batch(config: dict, batch: list[dict],
                     video_title: str = "", video_theme: str = "") -> list[dict]:
    """用 Claude 翻译一批日语字幕段"""
    lines = []
    for i, seg in enumerate(batch):
        lines.append(f"{i+1}. [{seg['start']:.1f}s-{seg['end']:.1f}s] {seg['text']}")

    # 构建上下文提示(帮助 LLM 理解内容,纠正转录错误)
    context_hint = ""
    if video_title or video_theme:
        context_hint = "\n\n背景信息(用于理解上下文和纠正语音识别错误):\n"
        if video_title:
            context_hint += f"- 视频标题: {video_title}\n"
        if video_theme:
            context_hint += f"- 视频类型: {video_theme}\n"
        context_hint += "注意: 原文是语音识别(Whisper)自动生成的,可能有错字/错词.请根据上下文和视频主题智能纠正后再翻译.\n"

    prompt = f"""请将以下日语字幕翻译成中文.这是一段影视预告片的字幕.
{context_hint}
要求:
1. 翻译要自然流畅,符合中文观众的观看习惯
2. 保持原句的语气和情感(紧张,悬疑,热血等)
3. 人名保留日文/韩文原名,后面括号加中文注音(仅首次出现时)
4. 如果原文是旁白/画外音,翻译时保持旁白语气
5. 短句保持简短,不要过度意译
6. 如果发现明显的语音识别错误(如乱码,不通顺的词),请根据上下文推断正确含义后翻译

原文:
{chr(10).join(lines)}

输出格式(严格 JSON 数组):
[
  {{"id": 1, "zh": "中文翻译"}},
  {{"id": 2, "zh": "中文翻译"}},
  ...
]

只输出 JSON 数组,不要其他内容."""

    raw = _call_llm(config, [{"role": "user", "content": prompt}], max_tokens=4000)

    # 解析翻译结果
    translations = _parse_json_response(raw)
    if translations is None:
        # 尝试解析为数组
        try:
            translations = json.loads(raw)
        except json.JSONDecodeError:
            start = raw.find('[')
            end = raw.rfind(']')
            if start >= 0 and end > start:
                try:
                    translations = json.loads(raw[start:end+1])
                except json.JSONDecodeError:
                    pass

    # 合并翻译结果
    if isinstance(translations, list):
        zh_map = {}
        for item in translations:
            if isinstance(item, dict):
                idx = item.get("id", 0)
                zh_map[idx] = item.get("zh", "")

        result = []
        for i, seg in enumerate(batch):
            zh_text = zh_map.get(i + 1, seg["text"])  # fallback 用原文
            result.append({
                "start": seg["start"],
                "end": seg["end"],
                "ja_text": seg["text"],
                "zh_text": zh_text,
            })
        return result
    else:
        # 翻译失败,返回原文
        logger.warning("⚠️ 翻译解析失败,使用日语原文")
        return [{"start": s["start"], "end": s["end"],
                 "ja_text": s["text"], "zh_text": s["text"]} for s in batch]


def _translate_batch_sakura(config: dict, batch: list[dict],
                            video_title: str = "", video_theme: str = "") -> list[dict]:
    """用 SakuraLLM 翻译一批日语字幕段（专用日中翻译模型，更准确）

    SakuraLLM 使用特定的 prompt 格式：
    - 将原文用换行分隔发送
    - 模型逐行翻译，保持行数一致
    - 不需要复杂的 JSON 解析

    优化策略：
    - 翻译后检查行数是否匹配
    - 翻译质量快速检查（空行、纯标点、未翻译检测）
    - 质量不合格的行单独重试，再不行 fallback Claude
    """
    # 构建上下文提示
    context = ""
    if video_title:
        context += f"（视频：{video_title}）"
    if video_theme:
        context += f"（类型：{video_theme}）"

    # 用 sakura 后端调用
    sakura_config = config.copy()
    sakura_config["llm"] = config.get("llm", {}).copy()
    sakura_config["llm"]["backend"] = "sakura"

    def _sakura_translate_lines(segments: list[dict]) -> list[str]:
        """调用 SakuraLLM 翻译多行，返回翻译结果列表。
        使用编号行格式(1. xxx → 1. yyy)提高行数对齐率，
        SakuraLLM 在带编号时更稳定地保持行数一致。
        """
        ja_lines = [f"{i+1}. {seg['text']}" for i, seg in enumerate(segments)]
        ja_text = "\n".join(ja_lines)
        prompt = f"将下面的日文翻译成中文，保持行数和编号一致{context}：\n{ja_text}"
        raw = _call_llm(sakura_config, [{"role": "user", "content": prompt}], max_tokens=4000)
        # 解析编号行格式，兜底用原始分行
        result_lines = []
        for line in raw.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            # 去掉编号前缀 "1. " "2. " 等
            cleaned = re.sub(r'^\d+\.\s*', '', line)
            if cleaned:
                result_lines.append(cleaned)
        return result_lines if result_lines else [line.strip() for line in raw.strip().split("\n") if line.strip()]

    def _sakura_translate_single(text: str) -> str:
        """用 SakuraLLM 翻译单行文本（使用官方格式）"""
        prompt = f"将下面的日文翻译成中文{context}：\n{text}"
        raw = _call_llm(sakura_config, [{"role": "user", "content": prompt}], max_tokens=1000)
        # 单行翻译取第一行非空结果，去掉可能的编号前缀
        lines = [line.strip() for line in raw.strip().split("\n") if line.strip()]
        if lines:
            cleaned = __import__("re").sub(r"^\d+\.\s*", "", lines[0])
            return cleaned if cleaned else lines[0]
        return ""

    def _check_translation_quality(ja_text: str, zh_text: str) -> bool:
        """快速检查单条翻译质量，返回 True 表示合格"""
        # 检查1: 翻译为空
        if not zh_text or not zh_text.strip():
            return False
        # 检查2: 翻译结果全是标点符号（中日英标点）
        punct_pattern = re.compile(r'^[\s.,!?\;:"\'\(\)\[\]\{\}（）「」『』【】、。！？，；：\u201c\u201d\u2018\u2019\u2026\u2014\uff5e\xb7\-\+\=<>/\\]+$')
        if punct_pattern.match(zh_text):
            return False
        # 检查3: 翻译和原文完全相同（说明没翻译，排除短标点/数字/英文的情况）
        if zh_text.strip() == ja_text.strip() and len(ja_text.strip()) > 3:
            # 检查原文是否含日语假名/汉字（排除纯数字/英文/符号的情况）
            has_japanese = bool(re.search(r'[\u3040-\u309F\u30A0-\u30FF]', ja_text))
            if has_japanese:
                return False
        # 检查4: 中文字符比例（翻译结果应包含一定比例的中文字符）
        if len(zh_text.strip()) > 3:
            zh_chars = len(re.findall(r'[\u4e00-\u9fff]', zh_text))
            total_chars = len(zh_text.strip())
            # 原文有假名时，翻译结果应有 >20% 中文字符
            has_kana = bool(re.search(r'[\u3040-\u309F\u30A0-\u30FF]', ja_text))
            if has_kana and zh_chars / total_chars < 0.2:
                return False
        return True

    try:
        # 第一步：批量翻译
        zh_lines = _sakura_translate_lines(batch)

        # 第二步：检查行数是否匹配
        if len(zh_lines) != len(batch):
            logger.warning(f"⚠️ SakuraLLM 行数不匹配: 期望 {len(batch)} 行, 实际 {len(zh_lines)} 行")

            # 策略A: 如果多了行，尝试合并相邻短行对齐
            if len(zh_lines) > len(batch):
                merged_lines = []
                zh_idx = 0
                ratio = len(zh_lines) / len(batch)
                for b_idx in range(len(batch)):
                    # 每个原文行可能对应 1~2 个翻译行
                    parts = [zh_lines[zh_idx]] if zh_idx < len(zh_lines) else [""]
                    zh_idx += 1
                    # 如果翻译行数明显多于原文，合并多余行
                    while zh_idx < len(zh_lines) and len(merged_lines) + (len(batch) - b_idx) < len(zh_lines) - zh_idx + len(merged_lines) + 1:
                        parts.append(zh_lines[zh_idx])
                        zh_idx += 1
                        if len(merged_lines) + (len(batch) - b_idx) >= len(zh_lines) - zh_idx + len(merged_lines) + 1:
                            break
                    merged_lines.append("".join(parts))
                if len(merged_lines) == len(batch):
                    zh_lines = merged_lines
                    logger.info(f"   ✅ 合并对齐成功: {len(zh_lines)} 行")

            # 策略B: 如果少了行，尝试按比例分配
            elif len(zh_lines) < len(batch) and len(zh_lines) > 0:
                padded = list(zh_lines)
                # 复制最后一行填充（通常是模型截断了）
                while len(padded) < len(batch):
                    padded.append("")
                zh_lines = padded
                logger.info(f"   📏 填充对齐: {len(zh_lines)} 行（空行后续单条重试）")

            # 策略C: 仍不匹配，逐条重试
            if len(zh_lines) != len(batch):
                logger.info(f"   🔄 切换为逐条翻译模式（{len(batch)} 条）")
                zh_lines_retry = []
                for seg in batch:
                    try:
                        zh = _sakura_translate_single(seg["text"])
                        zh_lines_retry.append(zh)
                    except Exception as e:
                        logger.warning(f"   ⚠️ 单条 SakuraLLM 翻译失败: {e}")
                        zh_lines_retry.append("")  # 标记为空，后续质量检查会处理
                zh_lines = zh_lines_retry

        # 第三步：质量检查，标记不合格的行
        bad_indices = []  # 质量不合格的行索引
        result = []
        for i, seg in enumerate(batch):
            zh_text = zh_lines[i] if i < len(zh_lines) else ""
            if not _check_translation_quality(seg["text"], zh_text):
                bad_indices.append(i)
                zh_text = ""  # 先标记为空，后续重试
            result.append({
                "start": seg["start"],
                "end": seg["end"],
                "ja_text": seg["text"],
                "zh_text": zh_text,
            })

        # 第四步：对不合格的行，用 SakuraLLM 单条重试一次
        if bad_indices:
            logger.info(f"   🔍 质量检查: {len(bad_indices)}/{len(batch)} 行不合格，逐条重试")
            still_bad = []
            for idx in bad_indices:
                seg = batch[idx]
                try:
                    zh = _sakura_translate_single(seg["text"])
                    if _check_translation_quality(seg["text"], zh):
                        result[idx]["zh_text"] = zh
                        logger.debug(f"   ✅ 第 {idx+1} 行重试成功")
                    else:
                        still_bad.append(idx)
                except Exception:
                    still_bad.append(idx)

            # 第五步：仍然不合格的行，fallback 到 Claude
            if still_bad:
                logger.info(f"   🔄 {len(still_bad)} 行 SakuraLLM 重试仍不合格，fallback 到 Claude")
                fallback_segs = [batch[idx] for idx in still_bad]
                try:
                    fallback_results = _translate_batch(config, fallback_segs,
                                                        video_title=video_title,
                                                        video_theme=video_theme)
                    for j, idx in enumerate(still_bad):
                        if j < len(fallback_results):
                            result[idx]["zh_text"] = fallback_results[j]["zh_text"]
                except Exception as e:
                    logger.warning(f"   ⚠️ Claude fallback 也失败: {e}，使用原文")
                    for idx in still_bad:
                        result[idx]["zh_text"] = batch[idx]["text"]

        # 最终兜底：确保没有空翻译
        for i, item in enumerate(result):
            if not item["zh_text"] or not item["zh_text"].strip():
                result[i]["zh_text"] = batch[i]["text"]
                logger.warning(f"   ⚠️ 第 {i+1} 行翻译为空，使用日语原文兜底")

        quality_ok = sum(1 for i, r in enumerate(result)
                         if r["zh_text"] != batch[i]["text"])
        logger.info(f"   📊 SakuraLLM 翻译完成: {quality_ok}/{len(batch)} 行有效翻译")

        return result

    except Exception as e:
        logger.warning(f"⚠️ SakuraLLM 批量翻译整体失败: {e}, fallback 到 Claude")
        return _translate_batch(config, batch,
                                video_title=video_title,
                                video_theme=video_theme)


def _write_srt(segments: list[dict], output_path: str):
    """写入 SRT 字幕文件"""
    with open(output_path, "w", encoding="utf-8") as f:
        for i, seg in enumerate(segments, 1):
            start = _srt_timestamp(seg["start"])
            end = _srt_timestamp(seg["end"])
            text = seg.get("zh_text", seg.get("ja_text", ""))
            f.write(f"{i}\n{start} --> {end}\n{text}\n\n")


def _srt_timestamp(seconds: float) -> str:
    """秒数转 SRT 时间格式 HH:MM:SS,mmm"""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


# ── 保存脚本到文件 ────────────────────────────────────────

def save_script(script: dict, run_dir: str) -> str:
    """保存脚本到文件"""
    script_dir = os.path.join(run_dir, "script")
    os.makedirs(script_dir, exist_ok=True)

    # 保存 JSON(程序用)
    json_path = os.path.join(script_dir, "script.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(script, f, ensure_ascii=False, indent=2)

    # 保存可读文本(人看)
    txt_path = os.path.join(script_dir, "script.txt")
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(f"标题: {script['title']}\n")
        f.write(f"标签: {', '.join(script.get('tags', []))}\n")
        f.write(f"简介: {script.get('description', '')}\n")
        f.write(f"\n{'='*60}\n\n")
        for i, seg in enumerate(script["segments"], 1):
            f.write(f"[{_fmt_time(seg['start_time'])} → {_fmt_time(seg['end_time'])}]\n")
            f.write(f"画面: {seg.get('scene_desc', '')}\n")
            f.write(f"解说: {seg['text']}\n\n")

    # 保存转录(如果有)
    return json_path


def save_transcript(transcript: list[dict], run_dir: str) -> str:
    """保存原始转录"""
    script_dir = os.path.join(run_dir, "script")
    os.makedirs(script_dir, exist_ok=True)
    path = os.path.join(script_dir, "transcript_ja.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(transcript, f, ensure_ascii=False, indent=2)
    return path


# ── 内部工具函数 ──────────────────────────────────────────

def _format_transcript(transcript: list[dict]) -> str:
    """格式化转录文本供 prompt 使用"""
    lines = []
    for seg in transcript:
        lines.append(f"[{seg['start']:.1f}s-{seg['end']:.1f}s] {seg['text']}")
    return "\n".join(lines)


def _parse_json_response(text: str) -> dict | None:
    """解析可能被 markdown 包裹的 JSON"""
    # 尝试直接解析
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 去掉 markdown ```json ... ```
    match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    # 尝试找第一个 { 到最后一个 }
    start = text.find('{')
    end = text.rfind('}')
    if start >= 0 and end > start:
        try:
            return json.loads(text[start:end+1])
        except json.JSONDecodeError:
            pass

    return None


def _validate_script(script: dict, video_duration: float) -> dict:
    """验证和修正脚本"""
    segments = script.get("segments", [])

    # 确保时间类型正确
    for seg in segments:
        seg["start_time"] = float(seg.get("start_time", 0))
        seg["end_time"] = float(seg.get("end_time", 0))

    # 按时间排序
    segments.sort(key=lambda s: s["start_time"])

    # 确保不超过视频时长
    if segments and segments[-1]["end_time"] > video_duration * 1.1:
        ratio = video_duration / segments[-1]["end_time"]
        for seg in segments:
            seg["start_time"] = round(seg["start_time"] * ratio, 2)
            seg["end_time"] = round(seg["end_time"] * ratio, 2)

    # 确保每段有必要字段
    for seg in segments:
        seg.setdefault("scene_desc", "")
        seg.setdefault("text", "")

    # 移除空段
    segments = [s for s in segments if s["text"].strip()]

    script["segments"] = segments
    script.setdefault("title", "日本美食探访")
    script.setdefault("tags", ["日本美食", "纪录片", "美食探店"])
    script.setdefault("description", "")

    return script


def _fmt_time(seconds: float) -> str:
    """秒转 MM:SS 格式"""
    m, s = divmod(int(seconds), 60)
    return f"{m:02d}:{s:02d}"
