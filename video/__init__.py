"""视频合成模块 — 源视频去原音 + 新配音 + 中文硬字幕 + 防版权处理"""

import json
import logging
import os
import subprocess

logger = logging.getLogger(__name__)


def compose_video(config: dict, source_video_path: str,
                  output_path: str,
                  audio_track_path: str = None,
                  srt_path: str = None) -> str:
    """
    合成最终视频。支持多种音频模式：
    - original: 保留源视频原声 + 中文硬字幕
    - dubbed:   去原声 + 中文配音 + 硬字幕
    - mixed:    原声降低音量 + 中文配音叠加 + 硬字幕

    参数:
        source_video_path: 下载的源视频
        audio_track_path:  拼接好的中文配音音轨（original 模式可为 None）
        srt_path:          SRT 字幕文件（None 则不烧字幕）
        output_path:       输出路径
    """
    video_cfg = config.get("video", {})
    anti_cr = video_cfg.get("anti_copyright", {})
    sub_cfg = video_cfg.get("subtitle", {})

    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    audio_mode = video_cfg.get("audio_mode", "dubbed")
    # 获取源视频信息
    src_info = _get_video_info(source_video_path)
    src_w = src_info.get("width", 1920)
    src_h = src_info.get("height", 1080)
    src_duration = src_info.get("duration", 0)

    logger.info(f"🎬 合成视频: {src_w}x{src_h}, {src_duration:.0f}s, 音频模式: {audio_mode}")

    # ── subtitle_only 模式：预告片专用，只加字幕，不做任何画面处理 ──
    if audio_mode == "subtitle_only":
        return _compose_subtitle_only(
            source_video_path, output_path, srt_path, sub_cfg, video_cfg)

    # ── 构建 FFmpeg 滤镜链 ──
    filters = []

    # 1) 防版权处理
    crop_pct = anti_cr.get("crop_percent", 0.95)
    brightness = anti_cr.get("brightness_adjust", 0.02)
    mirror = anti_cr.get("mirror", False)

    # 裁切（去掉边缘，去水印）
    if crop_pct < 1.0:
        crop_w = int(src_w * crop_pct)
        crop_h = int(src_h * crop_pct)
        # 确保偶数
        crop_w = crop_w - (crop_w % 2)
        crop_h = crop_h - (crop_h % 2)
        x_off = (src_w - crop_w) // 2
        y_off = (src_h - crop_h) // 2
        filters.append(f"crop={crop_w}:{crop_h}:{x_off}:{y_off}")

    # 缩放回标准分辨率
    target_res = video_cfg.get("resolution", "1920x1080")
    tw, th = target_res.split("x")
    filters.append(f"scale={tw}:{th}:flags=lanczos")

    # 亮度/对比度微调
    if brightness > 0:
        filters.append(f"eq=brightness={brightness}:contrast=1.02")

    # 镜像翻转
    if mirror:
        filters.append("hflip")

    # 2) 字幕烧录
    if srt_path and os.path.exists(srt_path):
        # 先对 SRT 路径转义（FFmpeg subtitles 过滤器的特殊字符）
        srt_escaped = srt_path.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")

        font_name = sub_cfg.get("font", "PingFang SC")
        font_size = sub_cfg.get("font_size", 18)
        font_color = sub_cfg.get("font_color", "&H00FFFFFF")
        outline_width = sub_cfg.get("outline_width", 2)
        shadow_depth = sub_cfg.get("shadow_depth", 0)
        margin_bottom = sub_cfg.get("margin_bottom", 30)
        border_style = sub_cfg.get("border_style", 1)
        bold = sub_cfg.get("bold", 1)
        back_colour = sub_cfg.get("back_colour", "&H00000000")

        # Netflix 风格：描边白字，不遮挡画面
        subtitle_filter = (
            f"subtitles='{srt_escaped}'"
            f":force_style='FontName={font_name},"
            f"FontSize={font_size},"
            f"PrimaryColour={font_color},"
            f"OutlineColour=&H00000000,"
            f"BackColour={back_colour},"
            f"BorderStyle={border_style},"
            f"Outline={outline_width},"
            f"Shadow={shadow_depth},"
            f"Bold={bold},"
            f"MarginV={margin_bottom},"
            f"Alignment=2'"  # 底部居中
        )
        filters.append(subtitle_filter)

    filter_complex = ",".join(filters)

    # ── FFmpeg 命令 ──
    if audio_mode == "original":
        # 保留原声模式：只处理视频画面 + 烧字幕，音频保持原样
        cmd = [
            "ffmpeg", "-y",
            "-i", source_video_path,
            "-filter_complex",
            f"[0:v]{filter_complex}[vout]",
            "-map", "[vout]",
            "-map", "0:a",              # 保留源视频音频
            "-c:v", "libx264",
            "-preset", "medium",
            "-crf", "23",               # CRF 23 更合理（文件更小）
            "-c:a", "aac",
            "-b:a", "192k",
            "-movflags", "+faststart",
            output_path,
        ]
    elif audio_mode == "mixed" and audio_track_path:
        # 混合模式：原声降低 + 配音叠加
        cmd = [
            "ffmpeg", "-y",
            "-i", source_video_path,
            "-i", audio_track_path,
            "-filter_complex",
            f"[0:v]{filter_complex}[vout];"
            f"[0:a]volume=0.15[bg];"     # 原声降到 15%
            f"[1:a]volume=1.0[fg];"      # 配音保持原音量
            f"[bg][fg]amix=inputs=2:duration=longest[aout]",
            "-map", "[vout]",
            "-map", "[aout]",
            "-c:v", "libx264",
            "-preset", "medium",
            "-crf", "23",
            "-c:a", "aac",
            "-b:a", "192k",
            "-movflags", "+faststart",
            "-shortest",
            output_path,
        ]
    else:
        # dubbed 模式（默认）：完全替换音轨
        if not audio_track_path:
            raise ValueError("dubbed 模式需要提供 audio_track_path")
        cmd = [
            "ffmpeg", "-y",
            "-i", source_video_path,
            "-i", audio_track_path,
            "-filter_complex",
            f"[0:v]{filter_complex}[vout]",
            "-map", "[vout]",
            "-map", "1:a",              # 用新配音替换
            "-c:v", "libx264",
            "-preset", "medium",
            "-crf", "23",
            "-c:a", "aac",
            "-b:a", "192k",
            "-movflags", "+faststart",
            "-shortest",
            output_path,
        ]

    logger.info("🔧 FFmpeg 合成中...")
    _run_ffmpeg(cmd)

    # 验证输出
    out_info = _get_video_info(output_path)
    logger.info(f"✅ 视频合成完成: {output_path}")
    logger.info(f"   分辨率: {out_info.get('width')}x{out_info.get('height')}, "
                f"时长: {out_info.get('duration', 0):.0f}s, "
                f"大小: {os.path.getsize(output_path) / 1024 / 1024:.1f}MB")

    return output_path


def compose_video_simple(config: dict, source_video_path: str,
                         audio_track_path: str, output_path: str) -> str:
    """
    简化合成（不烧字幕）— 用于字幕文件有问题时的降级方案。
    """
    video_cfg = config.get("video", {})
    anti_cr = video_cfg.get("anti_copyright", {})

    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    filters = []

    # 防版权处理
    crop_pct = anti_cr.get("crop_percent", 0.95)
    if crop_pct < 1.0:
        filters.append(f"crop=iw*{crop_pct}:ih*{crop_pct}")

    target_res = video_cfg.get("resolution", "1920x1080")
    tw, th = target_res.split("x")
    filters.append(f"scale={tw}:{th}:flags=lanczos")

    brightness = anti_cr.get("brightness_adjust", 0.02)
    if brightness > 0:
        filters.append(f"eq=brightness={brightness}:contrast=1.02")

    if anti_cr.get("mirror", False):
        filters.append("hflip")

    vf = ",".join(filters) if filters else "null"

    cmd = [
        "ffmpeg", "-y",
        "-i", source_video_path,
        "-i", audio_track_path,
        "-vf", vf,
        "-map", "0:v",
        "-map", "1:a",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        "-shortest",
        output_path,
    ]

    _run_ffmpeg(cmd)
    return output_path


def add_watermark(config: dict, video_path: str, output_path: str = None) -> str:
    """
    添加自己的水印（可选步骤，声明二创来源）。
    """
    video_cfg = config.get("video", {})
    watermark_text = video_cfg.get("watermark_text", "")

    if not watermark_text:
        return video_path

    if output_path is None:
        base, ext = os.path.splitext(video_path)
        output_path = f"{base}_wm{ext}"

    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-vf", (
            f"drawtext=text='{watermark_text}':"
            f"fontsize=18:fontcolor=white@0.5:"
            f"x=w-tw-20:y=20:"
            f"fontfile=/System/Library/Fonts/PingFang.ttc"
        ),
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-c:a", "copy",
        output_path,
    ]

    _run_ffmpeg(cmd)
    return output_path


# ══════════════════════════════════════════════════════════
#  subtitle_only 模式（预告片专用）
# ══════════════════════════════════════════════════════════

def _compose_subtitle_only(source_video_path: str, output_path: str,
                           srt_path: str, sub_cfg: dict,
                           video_cfg: dict) -> str:
    """
    预告片专用：保留原声 + 保持原始画质 + 只烧中文字幕。
    不做任何防版权处理（不裁切、不调亮度、不镜像）。
    使用 CRF 18 保持高画质。
    """
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    if srt_path and os.path.exists(srt_path):
        # 字幕滤镜 — B站风格
        srt_escaped = srt_path.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")

        font_name = sub_cfg.get("font", "PingFang SC")
        font_size = sub_cfg.get("font_size", 18)
        font_color = sub_cfg.get("font_color", "&H00FFFFFF")
        outline_width = sub_cfg.get("outline_width", 2)
        shadow_depth = sub_cfg.get("shadow_depth", 0)
        margin_bottom = sub_cfg.get("margin_bottom", 30)
        border_style = sub_cfg.get("border_style", 1)
        bold = sub_cfg.get("bold", 1)
        back_colour = sub_cfg.get("back_colour", "&H00000000")

        # Netflix 风格字幕：描边白字，不遮挡画面
        subtitle_filter = (
            f"subtitles='{srt_escaped}'"
            f":force_style='FontName={font_name},"
            f"FontSize={font_size},"
            f"PrimaryColour={font_color},"
            f"OutlineColour=&H00000000,"
            f"BackColour={back_colour},"
            f"BorderStyle={border_style},"
            f"Outline={outline_width},"
            f"Shadow={shadow_depth},"
            f"Bold={bold},"
            f"MarginV={margin_bottom},"
            f"Alignment=2'"
        )

        cmd = [
            "ffmpeg", "-y",
            "-i", source_video_path,
            "-vf", subtitle_filter,
            "-c:v", "libx264",
            "-preset", "medium",
            "-crf", "18",           # 高画质
            "-c:a", "aac",
            "-b:a", "192k",
            "-movflags", "+faststart",
            output_path,
        ]
    else:
        # 无字幕 — 直接 copy（零损耗）
        cmd = [
            "ffmpeg", "-y",
            "-i", source_video_path,
            "-c", "copy",
            "-movflags", "+faststart",
            output_path,
        ]

    logger.info("🔧 FFmpeg 合成中（subtitle_only 模式）...")
    _run_ffmpeg(cmd)

    out_info = _get_video_info(output_path)
    logger.info(f"✅ 预告片合成完成: {output_path}")
    logger.info(f"   分辨率: {out_info.get('width')}x{out_info.get('height')}, "
                f"时长: {out_info.get('duration', 0):.0f}s, "
                f"大小: {os.path.getsize(output_path) / 1024 / 1024:.1f}MB")

    return output_path


# ══════════════════════════════════════════════════════════
#  辅助函数
# ══════════════════════════════════════════════════════════

def _get_video_info(filepath: str) -> dict:
    """获取视频信息（分辨率、时长等）"""
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json",
         "-show_format", "-show_streams", filepath],
        capture_output=True, text=True,
    )
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {}

    info = {
        "duration": float(data.get("format", {}).get("duration", 0)),
    }

    for stream in data.get("streams", []):
        if stream.get("codec_type") == "video":
            info["width"] = int(stream.get("width", 0))
            info["height"] = int(stream.get("height", 0))
            info["fps"] = _parse_fps(stream.get("r_frame_rate", "30/1"))
            info["codec"] = stream.get("codec_name", "")
            break

    return info


def _parse_fps(fps_str: str) -> float:
    """解析 ffprobe 的帧率字符串"""
    try:
        if "/" in fps_str:
            num, den = fps_str.split("/")
            return float(num) / float(den)
        return float(fps_str)
    except (ValueError, ZeroDivisionError):
        return 30.0


def _run_ffmpeg(cmd: list[str], timeout: int = 1800):
    """执行 FFmpeg 命令"""
    logger.debug(f"FFmpeg: {' '.join(cmd[:6])}...")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        error_msg = result.stderr[-500:] if result.stderr else "未知错误"
        logger.error(f"FFmpeg 失败:\n{error_msg}")
        raise RuntimeError(f"FFmpeg 失败: {error_msg}")
    return result


def _get_duration(filepath: str) -> float:
    """获取媒体文件时长"""
    info = _get_video_info(filepath)
    return info.get("duration", 0)
