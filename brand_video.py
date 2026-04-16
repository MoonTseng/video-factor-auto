#!/usr/bin/env python3
"""
brand_video.py — 视频二创品牌化处理
功能:
  1. 叠加角落水印 (全程右上角半透明)
  2. 拼接品牌片头 (3秒鳥居动画)
  3. 拼接品牌片尾 (2秒关注引导)
  4. 统一输出1080p H.264

用法:
  python brand_video.py input.mp4                    # 输出到 input_branded.mp4
  python brand_video.py input.mp4 -o output.mp4      # 指定输出路径
  python brand_video.py input.mp4 --no-intro          # 不加片头
  python brand_video.py input.mp4 --no-outro          # 不加片尾
  python brand_video.py input.mp4 --no-watermark      # 不加水印
"""

import os
import sys
import argparse
import subprocess
import shutil
import tempfile

BRAND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "brand")


def get_video_info(video_path):
    """获取视频的分辨率、帧率、时长等信息"""
    cmd = [
        'ffprobe', '-v', 'quiet',
        '-print_format', 'json',
        '-show_streams', '-show_format',
        video_path
    ]
    import json
    result = subprocess.run(cmd, capture_output=True, text=True)
    info = json.loads(result.stdout)
    
    video_stream = None
    audio_stream = None
    for s in info.get('streams', []):
        if s['codec_type'] == 'video' and not video_stream:
            video_stream = s
        elif s['codec_type'] == 'audio' and not audio_stream:
            audio_stream = s
    
    width = int(video_stream['width']) if video_stream else 1920
    height = int(video_stream['height']) if video_stream else 1080
    
    # 帧率
    fps_str = video_stream.get('r_frame_rate', '30/1') if video_stream else '30/1'
    if '/' in fps_str:
        num, den = fps_str.split('/')
        fps = float(num) / float(den)
    else:
        fps = float(fps_str)
    
    duration = float(info.get('format', {}).get('duration', 0))
    has_audio = audio_stream is not None
    
    return {
        'width': width, 'height': height,
        'fps': fps, 'duration': duration,
        'has_audio': has_audio
    }


def add_watermark(input_path, output_path, watermark_path=None):
    """在视频右上角叠加半透明水印"""
    if watermark_path is None:
        watermark_path = os.path.join(BRAND_DIR, "watermark.png")
    
    if not os.path.exists(watermark_path):
        print(f"  ⚠️  水印文件不存在: {watermark_path}, 跳过水印")
        shutil.copy(input_path, output_path)
        return
    
    # 水印放在右上角, 距边 20px, 缩放到视频宽度的 18%
    cmd = [
        'ffmpeg', '-y',
        '-i', input_path,
        '-i', watermark_path,
        '-filter_complex',
        "[1:v]scale=iw*0.18:-1[wm];"
        "[0:v][wm]overlay=W-w-20:20",
        '-c:v', 'libx264', '-preset', 'medium', '-crf', '20',
        '-c:a', 'copy',
        output_path
    ]
    print(f"  📌 叠加水印...")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  ❌ 水印叠加失败: {result.stderr[:200]}")
        shutil.copy(input_path, output_path)
    else:
        print(f"  ✅ 水印叠加完成")


def normalize_video(input_path, output_path, target_w=1920, target_h=1080, target_fps=30):
    """
    将视频标准化到统一规格 (用于片头片尾拼接前的格式统一)
    - 缩放到 target_w x target_h (不足部分加黑边)
    - 统一帧率
    - 确保音频流存在 (静音填充)
    """
    info = get_video_info(input_path)
    
    filters = []
    # 缩放 + 填充黑边到目标尺寸
    filters.append(f"scale={target_w}:{target_h}:force_original_aspect_ratio=decrease")
    filters.append(f"pad={target_w}:{target_h}:(ow-iw)/2:(oh-ih)/2:black")
    filters.append(f"fps={target_fps}")
    filters.append("setsar=1")
    
    vf = ",".join(filters)
    
    cmd = ['ffmpeg', '-y', '-i', input_path]
    
    if not info['has_audio']:
        # 生成静音音频
        cmd.extend(['-f', 'lavfi', '-i', f'anullsrc=channel_layout=stereo:sample_rate=44100'])
        cmd.extend(['-filter_complex', f"[0:v]{vf}[v]", '-map', '[v]', '-map', '1:a', '-shortest'])
    else:
        cmd.extend(['-vf', vf, '-c:a', 'aac', '-ar', '44100', '-ac', '2'])
    
    cmd.extend(['-c:v', 'libx264', '-preset', 'medium', '-crf', '20', output_path])
    
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"  ⚠️  标准化失败: {result.stderr[:300]}")
        return False
    return True


def concat_videos(parts, output_path):
    """
    使用 ffmpeg concat demuxer 拼接多段视频
    所有 parts 必须已经标准化到相同规格
    """
    # 创建 concat list 文件
    list_path = output_path + ".concat_list.txt"
    with open(list_path, 'w') as f:
        for part in parts:
            f.write(f"file '{os.path.abspath(part)}'\n")
    
    cmd = [
        'ffmpeg', '-y',
        '-f', 'concat', '-safe', '0',
        '-i', list_path,
        '-c:v', 'libx264', '-preset', 'medium', '-crf', '20',
        '-c:a', 'aac',
        output_path
    ]
    
    result = subprocess.run(cmd, capture_output=True, text=True)
    os.unlink(list_path)
    
    if result.returncode != 0:
        print(f"  ❌ 拼接失败: {result.stderr[:300]}")
        return False
    return True


def brand_video(input_path, output_path=None, 
                add_intro=True, add_outro=True, add_wm=False):
    """
    主函数: 对视频进行完整的品牌化处理
    
    流程:
    1. 标准化输入视频 → 1080p 30fps
    2. 叠加水印
    3. 标准化片头/片尾 → 同规格
    4. 拼接: 片头 + 主视频(带水印) + 片尾
    """
    if output_path is None:
        base, ext = os.path.splitext(input_path)
        output_path = f"{base}_branded{ext}"
    
    intro_path = os.path.join(BRAND_DIR, "intro.mp4")
    outro_path = os.path.join(BRAND_DIR, "outro.mp4")
    watermark_path = os.path.join(BRAND_DIR, "watermark.png")
    
    info = get_video_info(input_path)
    print(f"\n{'='*50}")
    print(f"  🎬 品牌化处理: {os.path.basename(input_path)}")
    print(f"{'='*50}")
    print(f"  输入: {info['width']}x{info['height']}, {info['fps']:.1f}fps, {info['duration']:.1f}s")
    print(f"  片头: {'是' if add_intro else '否'}  水印: {'是' if add_wm else '否'}  片尾: {'是' if add_outro else '否'}")
    
    tmpdir = tempfile.mkdtemp(prefix="brand_")
    
    try:
        # Step 1: 标准化输入视频
        print(f"\n  [1/4] 标准化输入视频...")
        norm_main = os.path.join(tmpdir, "main_norm.mp4")
        if not normalize_video(input_path, norm_main):
            print("  ❌ 标准化失败，直接复制")
            shutil.copy(input_path, output_path)
            return output_path
        print(f"  ✅ 标准化完成")
        
        # Step 2: 叠加水印
        if add_wm:
            print(f"\n  [2/4] 叠加水印...")
            wm_main = os.path.join(tmpdir, "main_wm.mp4")
            add_watermark(norm_main, wm_main, watermark_path)
            current_main = wm_main
        else:
            print(f"\n  [2/4] 跳过水印")
            current_main = norm_main
        
        # Step 3: 标准化片头片尾
        parts = []
        if add_intro and os.path.exists(intro_path):
            print(f"\n  [3/4] 标准化片头...")
            norm_intro = os.path.join(tmpdir, "intro_norm.mp4")
            # 检测片头是否自带音频
            probe_cmd = ['ffprobe', '-v', 'quiet', '-select_streams', 'a',
                        '-show_entries', 'stream=codec_type', '-of', 'csv=p=0', intro_path]
            has_audio = subprocess.run(probe_cmd, capture_output=True, text=True).stdout.strip()
            
            if has_audio:
                # 片头自带音频，直接标准化
                normalize_video(intro_path, norm_intro)
                print(f"  🔊 片头自带音频")
            else:
                # 无音频，尝试叠加音效
                sfx_path = os.path.join(BRAND_DIR, "intro_sfx.wav")
                if os.path.exists(sfx_path):
                    intro_tmp = os.path.join(tmpdir, "intro_tmp.mp4")
                    normalize_video(intro_path, intro_tmp)
                    sfx_cmd = [
                        'ffmpeg', '-y', '-i', intro_tmp, '-i', sfx_path,
                        '-map', '0:v', '-map', '1:a', '-c:v', 'copy',
                        '-c:a', 'aac', '-ar', '44100', '-ac', '2',
                        '-shortest', norm_intro
                    ]
                    sfx_result = subprocess.run(sfx_cmd, capture_output=True, text=True)
                    if sfx_result.returncode != 0:
                        print(f"  ⚠️  音效合并失败，使用静音片头")
                        shutil.copy(intro_tmp, norm_intro)
                    else:
                        print(f"  🔊 片头音效已合并")
                else:
                    normalize_video(intro_path, norm_intro)
            parts.append(norm_intro)
            print(f"  ✅ 片头准备就绪")
        else:
            print(f"\n  [3/4] 跳过片头")
        
        parts.append(current_main)
        
        if add_outro and os.path.exists(outro_path):
            norm_outro = os.path.join(tmpdir, "outro_norm.mp4")
            normalize_video(outro_path, norm_outro)
            parts.append(norm_outro)
            print(f"  ✅ 片尾准备就绪")
        
        # Step 4: 拼接
        if len(parts) > 1:
            print(f"\n  [4/4] 拼接 {len(parts)} 段视频...")
            if concat_videos(parts, output_path):
                print(f"  ✅ 拼接完成")
            else:
                print(f"  ⚠️  拼接失败，输出不带片头片尾的版本")
                shutil.copy(current_main, output_path)
        else:
            print(f"\n  [4/4] 无需拼接")
            shutil.copy(current_main, output_path)
        
        # 输出信息
        out_info = get_video_info(output_path)
        print(f"\n{'='*50}")
        print(f"  ✅ 品牌化完成!")
        print(f"  输出: {output_path}")
        print(f"  规格: {out_info['width']}x{out_info['height']}, {out_info['duration']:.1f}s")
        print(f"{'='*50}\n")
        
        return output_path
        
    finally:
        # 清理临时文件
        shutil.rmtree(tmpdir, ignore_errors=True)


def main():
    parser = argparse.ArgumentParser(description="视频品牌化处理 — Ray带你看世界")
    parser.add_argument("input", help="输入视频路径")
    parser.add_argument("-o", "--output", help="输出视频路径")
    parser.add_argument("--no-intro", action="store_true", help="不加片头")
    parser.add_argument("--no-outro", action="store_true", help="不加片尾")
    parser.add_argument("--no-watermark", action="store_true", help="不加水印")
    
    args = parser.parse_args()
    
    if not os.path.exists(args.input):
        print(f"❌ 输入文件不存在: {args.input}")
        sys.exit(1)
    
    brand_video(
        args.input,
        output_path=args.output,
        add_intro=not args.no_intro,
        add_outro=not args.no_outro,
        add_wm=not args.no_watermark
    )


if __name__ == "__main__":
    main()
