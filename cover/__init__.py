"""封面提取模块 — 从视频中选取高光帧作为B站封面

策略：
1. 场景变化检测 → 选最有冲击力的画面
2. 裁掉黑边
3. 输出 16:9 比例的 JPG
"""

import json
import logging
import os
import subprocess

logger = logging.getLogger(__name__)


def extract_cover(video_path: str, output_path: str,
                  strategy: dict = None) -> str:
    """
    从视频提取封面图。

    参数：
        video_path: 视频文件路径
        output_path: 封面输出路径 (.jpg)
        strategy: 封面策略 dict（从 theme.get_cover_strategy() 获取）
    返回：
        封面图片路径
    """
    if strategy is None:
        strategy = {}

    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    skip_start = strategy.get("skip_first_seconds", 2)
    skip_end = strategy.get("skip_last_seconds", 3)
    crop_black = strategy.get("crop_black_bars", True)

    # 获取视频时长
    duration = _get_duration(video_path)
    if duration <= 0:
        duration = 60

    # 计算候选时间点
    usable_start = skip_start
    usable_end = max(duration - skip_end, skip_start + 1)

    # 策略1: 在 20%/35%/50%/65%/80% 位置各截一帧，选最好的
    candidates = []
    for pct in [0.2, 0.35, 0.5, 0.65, 0.8]:
        t = usable_start + (usable_end - usable_start) * pct
        candidates.append(round(t, 1))

    # 截取候选帧
    best_path = None
    best_score = -1
    temp_dir = os.path.dirname(output_path)

    for i, t in enumerate(candidates):
        temp_path = os.path.join(temp_dir, f"_cover_candidate_{i}.jpg")

        # 截帧
        vf_filters = []
        if crop_black:
            # 自动检测并裁掉黑边
            vf_filters.append("cropdetect=24:16:0")

        cmd = [
            "ffmpeg", "-y", "-ss", str(t),
            "-i", video_path,
            "-vframes", "1", "-q:v", "1",
        ]

        # 先截一帧（不裁切），用于评分
        cmd.extend(["-update", "1", temp_path])

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0 or not os.path.exists(temp_path):
            continue

        # 计算 "视觉丰富度" 分数
        score = _score_frame(temp_path)
        logger.debug(f"  候选帧 {i} (t={t}s): score={score:.0f}")

        if score > best_score:
            best_score = score
            best_path = temp_path

    if not best_path:
        # fallback: 直接截取 30% 位置
        fallback_t = usable_start + (usable_end - usable_start) * 0.3
        cmd = [
            "ffmpeg", "-y", "-ss", str(fallback_t),
            "-i", video_path,
            "-vframes", "1", "-q:v", "1",
            "-update", "1", output_path,
        ]
        subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        logger.info(f"🖼️ 封面提取 (fallback): {output_path}")
        return output_path

    # 对最佳帧做后处理：裁黑边 + 确保 16:9
    _postprocess_cover(best_path, output_path, crop_black)

    # 清理临时文件
    for i in range(len(candidates)):
        temp = os.path.join(temp_dir, f"_cover_candidate_{i}.jpg")
        if os.path.exists(temp) and temp != output_path:
            os.remove(temp)

    size_kb = os.path.getsize(output_path) / 1024
    logger.info(f"🖼️ 封面提取完成: {output_path} ({size_kb:.0f}KB, score={best_score:.0f})")
    return output_path


def _postprocess_cover(input_path: str, output_path: str,
                       crop_black: bool = True):
    """后处理：裁黑边 + 16:9"""
    vf = []

    if crop_black:
        # 用 cropdetect 检测黑边，然后裁掉
        crop_info = _detect_crop(input_path)
        if crop_info:
            vf.append(crop_info)

    # 确保 16:9 比例，至少 1280x720
    vf.append("scale=1280:720:force_original_aspect_ratio=decrease")
    vf.append("pad=1280:720:(ow-iw)/2:(oh-ih)/2:black")

    cmd = [
        "ffmpeg", "-y", "-i", input_path,
        "-vf", ",".join(vf),
        "-q:v", "1",
        "-update", "1",
        output_path,
    ]
    subprocess.run(cmd, capture_output=True, text=True, timeout=30)

    if not os.path.exists(output_path):
        # fallback: 直接复制
        import shutil
        shutil.copy2(input_path, output_path)


def _detect_crop(image_path: str) -> str:
    """检测图片的黑边区域，返回 crop 参数"""
    # 用 ffmpeg cropdetect
    cmd = [
        "ffmpeg", "-i", image_path,
        "-vf", "cropdetect=24:16:0",
        "-vframes", "1", "-f", "null", "-",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)

    # 从 stderr 提取 crop 参数
    import re
    matches = re.findall(r'crop=(\d+:\d+:\d+:\d+)', result.stderr)
    if matches:
        return f"crop={matches[-1]}"
    return ""


def _score_frame(image_path: str) -> float:
    """
    评估一帧的视觉丰富度。
    基于图片文件大小（压缩后更大 = 更多细节/色彩变化）。
    这是一个快速近似方法，避免依赖 PIL/OpenCV。
    """
    try:
        size = os.path.getsize(image_path)
        return float(size)
    except OSError:
        return 0.0


def _get_duration(video_path: str) -> float:
    """获取视频时长"""
    cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_format", video_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    try:
        data = json.loads(result.stdout)
        return float(data.get("format", {}).get("duration", 0))
    except (json.JSONDecodeError, ValueError):
        return 0.0
