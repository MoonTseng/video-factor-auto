"""二创文案生成 — Whisper 转录日语原音 → Claude 生成中文解说脚本"""

import json
import logging
import os
import re

import httpx

logger = logging.getLogger(__name__)


# ── Bedrock 代理 LLM 调用 ────────────────────────────────

def _call_llm(config: dict, messages: list[dict], max_tokens: int = 4000) -> str:
    """
    统一 LLM 调用接口，支持 Bedrock 代理模式。
    返回纯文本响应。
    """
    llm_cfg = config.get("llm", {})
    backend = llm_cfg.get("backend", "bedrock_proxy")

    if backend == "bedrock_proxy":
        return _call_bedrock_proxy(llm_cfg, messages, max_tokens)
    else:
        # 直接 Anthropic API 兜底
        import anthropic
        api_key = llm_cfg.get("api_key", "")
        model = llm_cfg.get("model", "claude-sonnet-4-20250514")
        client = anthropic.Anthropic(api_key=api_key)
        resp = client.messages.create(model=model, max_tokens=max_tokens, messages=messages)
        return resp.content[0].text.strip()


def _call_bedrock_proxy(llm_cfg: dict, messages: list[dict], max_tokens: int) -> str:
    """通过公司 Bedrock 代理调用 Claude（/model/{modelId}/invoke）"""
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

    resp = httpx.post(
        url,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {auth_token}",
        },
        json=body,
        timeout=120,
    )

    if resp.status_code != 200:
        raise RuntimeError(f"Bedrock 代理调用失败 ({resp.status_code}): {resp.text[:500]}")

    data = resp.json()
    return data["content"][0]["text"].strip()


# ── Whisper 转录 ──────────────────────────────────────────

def transcribe_source(config: dict, audio_path: str) -> list[dict]:
    """
    用 faster-whisper 提取源视频的日语音频转文字（带时间戳）。
    返回 [{start, end, text}, ...]
    """
    from faster_whisper import WhisperModel

    whisper_cfg = config.get("whisper", {})
    model_size = whisper_cfg.get("model_size", "base")
    device = whisper_cfg.get("device", "cpu")
    compute_type = whisper_cfg.get("compute_type", "int8")

    logger.info(f"🎙️ Whisper 转录中 (model={model_size}, device={device})...")
    logger.info(f"   音频文件: {audio_path}")

    model = WhisperModel(model_size, device=device, compute_type=compute_type)
    segments, info = model.transcribe(
        audio_path,
        language="ja",
        vad_filter=True,           # 过滤静音
        vad_parameters=dict(
            min_silence_duration_ms=500,
        ),
    )

    transcript = []
    for seg in segments:
        transcript.append({
            "start": round(seg.start, 2),
            "end": round(seg.end, 2),
            "text": seg.text.strip(),
        })

    total_text = sum(len(s["text"]) for s in transcript)
    logger.info(f"✅ 转录完成: {len(transcript)} 段, {total_text} 字符")
    logger.info(f"   检测语言: {info.language} (概率 {info.language_probability:.1%})")

    return transcript


# ── 话题选择（保留兼容接口） ──────────────────────────────

def select_topic(config: dict, topics: list[dict]) -> dict:
    """
    从候选视频中选择最适合二创的话题。
    优先选有 URL 的、播放量适中的。
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

    # 兜底：选播放量最高的那个
    return max(candidates, key=lambda t: t.get("view_count", 0))


def _ai_select_topic(config: dict, candidates: list[dict]) -> dict:
    """用 Claude 选择最适合二创的视频"""
    candidate_text = "\n".join(
        f"{i+1}. [{t['view_count']:,}播放] {t['title']}\n   {t.get('description', '')[:100]}"
        for i, t in enumerate(candidates[:10])
    )

    raw = _call_llm(config, [{
        "role": "user",
        "content": f"""从以下日本美食/旅行视频中，选择一个最适合做中文二创解说的。
选择标准：
1. 有故事性（匠人精神、个人奋斗、文化传承）
2. 画面会丰富（料理制作过程、街景、食物特写）
3. 容易引起中国观众共鸣

候选视频：
{candidate_text}

只回复数字编号（如: 3）"""
    }], max_tokens=200)

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
    基于原视频内容 + 日语转录，用 Claude 生成中文二创解说脚本。
    风格参考B站UP主「大鹏和你一起看世界美食」。

    返回 {title, tags, description, segments: [{start_time, end_time, text, scene_desc}, ...]}
    """
    # 准备转录文本（带时间戳）
    transcript_text = _format_transcript(transcript)
    video_duration = transcript[-1]["end"] if transcript else 600

    # 计算合适的分段数（每 15-25 秒一段解说）
    segment_count = max(8, int(video_duration / 20))

    prompt = f"""你是一个B站美食/旅行视频的中文解说文案写手。
你的风格类似UP主「大鹏和你一起看世界美食」——口语化、亲切自然、像朋友聊天，
同时夹带知识科普，偶尔幽默但不过度。

现在有一个日本视频需要你写中文解说：

【视频标题】{topic.get('title', '')}
【视频描述】{topic.get('description', '')}
【视频主题】{topic.get('suggested_theme', topic.get('title', ''))}
【视频时长】{video_duration:.0f} 秒
【频道】{topic.get('channel', '')}

【日语原文转录（带时间戳）】
{transcript_text}

请基于以上内容，写一份完整的中文二创解说脚本。

要求：
1. 【不是翻译】—— 是基于视频内容的全新中文解说，用你自己的方式讲这个故事
2. 【口语化】—— 像在跟观众聊天，"你看这个师傅"、"说实话我第一次看到..."
3. 【有节奏】—— 开头要有吸引力（hook），中间有起伏，结尾有总结感悟
4. 【带知识】—— 适当介绍食材、工艺、文化背景，但不要像念课文
5. 【配合画面】—— 每段解说对应一个时间区间，要和画面内容匹配
6. 【时间对齐】—— 分成 {segment_count} 段左右，覆盖整个视频时长
7. 【语速适中】—— 每秒约 3-4 个汉字，别太密集，留给观众看画面的时间

输出格式（严格 JSON）：
{{
  "title": "中文视频标题（要吸引人，可以用问号或感叹号）",
  "tags": ["标签1", "标签2", ...],
  "description": "视频简介（2-3句话）",
  "segments": [
    {{
      "start_time": 0.0,
      "end_time": 15.0,
      "text": "解说文字",
      "scene_desc": "对应画面的简要描述"
    }},
    ...
  ]
}}

注意：
- start_time 和 end_time 是秒数（浮点数）
- 第一段从 0 或接近 0 开始
- 最后一段的 end_time 应接近视频总时长 {video_duration:.0f}
- segments 之间可以有短暂间隔（让观众看画面）
- 每段 text 的字数 = (end_time - start_time) × 3 左右

只输出 JSON，不要其他内容。"""

    logger.info(f"✍️ 生成二创解说脚本... (目标 ~{segment_count} 段)")

    raw_text = _call_llm(config, [{"role": "user", "content": prompt}], max_tokens=8000)

    # 解析 JSON（兼容 markdown 包裹）
    script = _parse_json_response(raw_text)

    if not script or "segments" not in script:
        raise ValueError(f"文案生成失败，无法解析 JSON:\n{raw_text[:500]}")

    # 验证和修正
    script = _validate_script(script, video_duration)

    logger.info(f"✅ 文案生成完成: {script['title']}")
    logger.info(f"   {len(script['segments'])} 段解说, "
                f"总字数 {sum(len(s['text']) for s in script['segments'])}")

    return script


# ── 保存脚本到文件 ────────────────────────────────────────

def save_script(script: dict, run_dir: str) -> str:
    """保存脚本到文件"""
    script_dir = os.path.join(run_dir, "script")
    os.makedirs(script_dir, exist_ok=True)

    # 保存 JSON（程序用）
    json_path = os.path.join(script_dir, "script.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(script, f, ensure_ascii=False, indent=2)

    # 保存可读文本（人看）
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

    # 保存转录（如果有）
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
