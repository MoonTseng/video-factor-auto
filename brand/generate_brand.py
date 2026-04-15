#!/usr/bin/env python3
"""
「像素旅人Ray」品牌素材生成器 v3
核心形象: 外部像素小狗图 (pixel_dog_source.png)
频道定位: 旅行 · 美食 · 影视 · AI

配色: 赛博粉(#FF6B9D) + 电光蓝(#00D4FF) + 像素绿(#7BFF7B) + 深紫底(#1A0A2E)
"""

import os
import math
from PIL import Image, ImageDraw, ImageFont, ImageFilter

BRAND_DIR = os.path.dirname(os.path.abspath(__file__))
DOG_SOURCE = os.path.join(BRAND_DIR, "pixel_dog_source.png")
WIDTH, HEIGHT = 1920, 1080

# === 配色 ===
CYBER_PINK   = (255, 107, 157)
ELECTRIC_BLUE= (0, 212, 255)
PIXEL_GREEN  = (123, 255, 123)
DEEP_PURPLE  = (26, 10, 46)
NEON_PURPLE  = (180, 80, 255)
WARM_WHITE   = (250, 245, 240)
DARK_BG      = (12, 8, 24)


def get_font(size, bold=False):
    for fp in [
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
        "/System/Library/Fonts/STHeiti Medium.ttc",
        "/Library/Fonts/Arial Unicode.ttf",
    ]:
        if os.path.exists(fp):
            try:
                return ImageFont.truetype(fp, size)
            except:
                continue
    return ImageFont.load_default()


def load_dog(size=None, circle_crop=False, feather=0, center_crop=False):
    """加载像素猫/狗源图，可选中心裁切、缩放、圆形裁切、边缘羽化"""
    img = Image.open(DOG_SOURCE).convert("RGBA")
    
    if center_crop:
        # 从横版图中裁切中心正方形区域（角色主体）
        w, h = img.size
        sq = min(w, h)
        # 偏左一点裁切 (角色通常在中左)
        left = max(0, (w - sq) // 2 - int(sq * 0.05))
        top = 0
        img = img.crop((left, top, left + sq, top + sq))
    
    if size:
        img = img.resize(size, Image.LANCZOS)
    
    if circle_crop:
        w, h = img.size
        mask = Image.new('L', (w, h), 0)
        mask_draw = ImageDraw.Draw(mask)
        mask_draw.ellipse([0, 0, w, h], fill=255)
        if feather > 0:
            mask = mask.filter(ImageFilter.GaussianBlur(feather))
        img.putalpha(mask)
    
    return img


def draw_gradient_bg(img, color1, color2):
    draw = ImageDraw.Draw(img)
    w, h = img.size
    for i in range(h):
        ratio = i / h
        r = int(color1[0] + (color2[0] - color1[0]) * ratio)
        g = int(color1[1] + (color2[1] - color1[1]) * ratio)
        b = int(color1[2] + (color2[2] - color1[2]) * ratio)
        draw.line([(0, i), (w, i)], fill=(r, g, b))


def draw_neon_text(draw, text, x, y, font, color=WARM_WHITE, glow_color=CYBER_PINK):
    """带霓虹发光的文字"""
    for offset in range(5, 0, -1):
        a = 30
        draw.text((x + offset, y + offset), text,
                 fill=(*glow_color[:3], a), font=font)
    draw.text((x, y), text, fill=color, font=font)


def draw_pixel_stars(draw, width, height, count=30):
    import random
    random.seed(42)
    for _ in range(count):
        sx = random.randint(0, width)
        sy = random.randint(0, height)
        size = random.choice([2, 3, 4])
        c = random.choice([ELECTRIC_BLUE, CYBER_PINK, PIXEL_GREEN, WARM_WHITE])
        brightness = random.randint(120, 255)
        draw.rectangle([sx, sy, sx+size, sy+size], fill=(*c[:3], brightness))


def create_logo():
    """频道Logo (800x800)"""
    print("  [1/4] 生成频道Logo...")
    size = 800
    img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    
    # 渐变圆形背景
    draw = ImageDraw.Draw(img, 'RGBA')
    for r in range(size // 2, 0, -1):
        ratio = r / (size // 2)
        cr = int(DEEP_PURPLE[0] * ratio + DARK_BG[0] * (1 - ratio))
        cg = int(DEEP_PURPLE[1] * ratio + DARK_BG[1] * (1 - ratio))
        cb = int(DEEP_PURPLE[2] * ratio + DARK_BG[2] * (1 - ratio))
        draw.ellipse([size//2-r, size//2-r, size//2+r, size//2+r],
                    fill=(cr, cg, cb, 255))
    
    draw_pixel_stars(draw, size, size, count=20)
    
    # 赛博猫 (中心裁切+圆形+羽化, 与背景自然融合)
    dog = load_dog(size=(580, 580), circle_crop=True, feather=20, center_crop=True)
    dog_x = (size - 580) // 2
    dog_y = size // 2 - 580 // 2 - 30
    img.paste(dog, (dog_x, dog_y), dog)
    
    # 霓虹圆框
    for i in range(10, 0, -2):
        alpha = int(50 * (i / 10))
        draw.ellipse([i, i, size-i, size-i],
                    outline=(*CYBER_PINK[:3], alpha), width=3)
    draw.ellipse([3, 3, size-3, size-3],
                outline=(*ELECTRIC_BLUE[:3], 200), width=3)
    
    # "像素旅人Ray"
    font = get_font(56, bold=True)
    text = "像素旅人Ray"
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    text_y = size // 2 + 200
    draw_neon_text(draw, text, size//2 - tw//2, text_y, font)
    
    path = os.path.join(BRAND_DIR, "logo.png")
    img.save(path, "PNG")
    print(f"    -> {path}")
    return path


def create_watermark():
    """视频水印 (右上角半透明)"""
    print("  [2/4] 生成视频水印...")
    img = Image.new('RGBA', (380, 80), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img, 'RGBA')
    
    # 半透明底板
    draw.rounded_rectangle([0, 0, 380, 80], radius=8, fill=(0, 0, 0, 90))
    
    # 像素猫 (圆形小图标, 中心裁切)
    dog = load_dog(size=(56, 56), circle_crop=True, feather=3, center_crop=True)
    img.paste(dog, (10, 12), dog)
    
    # 文字+描边
    font = get_font(26, bold=True)
    text = "像素旅人Ray"
    tx, ty = 75, 8
    for dx in [-1, 0, 1]:
        for dy in [-1, 0, 1]:
            if dx or dy:
                draw.text((tx+dx, ty+dy), text, fill=(0, 0, 0, 140), font=font)
    draw.text((tx, ty), text, fill=(*WARM_WHITE, 210), font=font)
    
    font_sub = get_font(15)
    draw.text((75, 44), "旅行 · 美食 · 影视", fill=(*ELECTRIC_BLUE[:3], 170), font=font_sub)
    
    path = os.path.join(BRAND_DIR, "watermark.png")
    img.save(path, "PNG")
    print(f"    -> {path}")
    return path


def create_intro_frames():
    """片头动画 3秒 90帧"""
    print("  [3/4] 生成片头动画...")
    frames_dir = os.path.join(BRAND_DIR, "intro_frames")
    os.makedirs(frames_dir, exist_ok=True)
    
    dog_full = load_dog(size=(500, 500), center_crop=True)
    total = 90
    import random
    
    for f in range(total):
        t = f / total
        img = Image.new('RGB', (WIDTH, HEIGHT), DARK_BG)
        draw = ImageDraw.Draw(img, 'RGBA')
        
        # 星星
        random.seed(f // 4)
        draw_pixel_stars(draw, WIDTH, HEIGHT, count=40)
        
        # Phase 1: 0-0.35 小狗从下弹入
        if t < 0.35:
            pt = t / 0.35
            ease = pt * pt * (3 - 2 * pt)  # smoothstep
            target_y = HEIGHT//2 - 300
            dog_y = int(HEIGHT + 50 - (HEIGHT + 50 - target_y) * ease)
            dog_x = (WIDTH - 500) // 2
            
            tmp = Image.new('RGBA', (WIDTH, HEIGHT), (0,0,0,0))
            tmp.paste(dog_full, (dog_x, dog_y), dog_full)
            img.paste(tmp, mask=tmp)
        
        # Phase 2: 0.35-0.65 稳定+文字打字机效果
        elif t < 0.65:
            dog_x = (WIDTH - 500) // 2
            dog_y = HEIGHT//2 - 300
            tmp = Image.new('RGBA', (WIDTH, HEIGHT), (0,0,0,0))
            tmp.paste(dog_full, (dog_x, dog_y), dog_full)
            img.paste(tmp, mask=tmp)
            
            pt = (t - 0.35) / 0.30
            full_text = "像素旅人Ray"
            chars = int(len(full_text) * min(1.0, pt * 1.6))
            show = full_text[:chars]
            if show:
                font = get_font(80, bold=True)
                full_bbox = draw.textbbox((0,0), full_text, font=font)
                full_tw = full_bbox[2] - full_bbox[0]
                text_x = (WIDTH - full_tw) // 2
                text_y = HEIGHT//2 + 220
                draw_neon_text(draw, show, text_x, text_y, font)
                
                # 光标
                if f % 8 < 4:
                    show_bbox = draw.textbbox((0,0), show, font=font)
                    cx = text_x + show_bbox[2] - show_bbox[0] + 5
                    draw.rectangle([cx, text_y+5, cx+4, text_y+75], fill=ELECTRIC_BLUE)
        
        # Phase 3: 0.65-1.0 完整展示+标签淡入
        else:
            dog_x = (WIDTH - 500) // 2
            dog_y = HEIGHT//2 - 300
            tmp = Image.new('RGBA', (WIDTH, HEIGHT), (0,0,0,0))
            tmp.paste(dog_full, (dog_x, dog_y), dog_full)
            img.paste(tmp, mask=tmp)
            
            font = get_font(80, bold=True)
            text = "像素旅人Ray"
            bbox = draw.textbbox((0,0), text, font=font)
            tw = bbox[2] - bbox[0]
            text_x = (WIDTH - tw) // 2
            text_y = HEIGHT//2 + 220
            draw_neon_text(draw, text, text_x, text_y, font)
            
            # 标签淡入
            pt = (t - 0.65) / 0.35
            alpha = int(min(255, pt * 350))
            font_tag = get_font(30)
            tag = "旅行 · 美食 · 影视 · AI"
            bbox2 = draw.textbbox((0,0), tag, font=font_tag)
            tw2 = bbox2[2] - bbox2[0]
            draw.text((WIDTH//2 - tw2//2, text_y + 100), tag,
                     fill=(*PIXEL_GREEN[:3], alpha), font=font_tag)
            
            # 装饰线
            line_w = int(280 * min(1.0, pt * 2))
            draw.line([(WIDTH//2 - line_w, text_y + 90), (WIDTH//2 + line_w, text_y + 90)],
                     fill=(*ELECTRIC_BLUE[:3], alpha), width=2)
        
        img.save(os.path.join(frames_dir, f"frame_{f:03d}.png"), "PNG")
    
    print(f"    -> {frames_dir}/ ({total} frames)")
    return frames_dir


def create_outro_frames():
    """片尾动画 2秒 60帧 — 上下布局：头像居中上方，文字居中下方"""
    print("  [4/4] 生成片尾动画...")
    frames_dir = os.path.join(BRAND_DIR, "outro_frames")
    os.makedirs(frames_dir, exist_ok=True)
    
    dog_sm = load_dog(size=(180, 180), center_crop=True, circle_crop=True, feather=8)
    total = 60
    
    for f in range(total):
        t = f / total
        img = Image.new('RGB', (WIDTH, HEIGHT), DARK_BG)
        draw = ImageDraw.Draw(img, 'RGBA')
        
        draw_pixel_stars(draw, WIDTH, HEIGHT, count=25)
        alpha = int(min(255, t * 4 * 255))
        
        # 头像 (居中, 偏上)
        dog_x = (WIDTH - 180) // 2
        dog_y = HEIGHT // 2 - 180
        tmp = Image.new('RGBA', (WIDTH, HEIGHT), (0,0,0,0))
        tmp.paste(dog_sm, (dog_x, dog_y), dog_sm)
        img.paste(tmp, mask=tmp)
        
        # 频道名 (头像正下方)
        font = get_font(56, bold=True)
        text = "像素旅人Ray"
        bbox = draw.textbbox((0,0), text, font=font)
        tw = bbox[2] - bbox[0]
        text_y = HEIGHT // 2 + 20
        draw_neon_text(draw, text, WIDTH//2 - tw//2, text_y, font)
        
        # 装饰线
        line_w = int(180 * min(1.0, t * 3))
        draw.line([(WIDTH//2 - line_w, text_y + 70), (WIDTH//2 + line_w, text_y + 70)],
                 fill=(*ELECTRIC_BLUE[:3], alpha), width=2)
        
        # 关注引导
        if t > 0.25:
            sa = int(min(255, (t-0.25)/0.3 * 255))
            font_cta = get_font(30)
            cta = "关注 · 点赞 · 收藏 · 转发"
            bbox2 = draw.textbbox((0,0), cta, font=font_cta)
            tw2 = bbox2[2] - bbox2[0]
            draw.text((WIDTH//2 - tw2//2, text_y + 90), cta,
                     fill=(*CYBER_PINK[:3], sa), font=font_cta)
        
        if t > 0.5:
            ta = int(min(180, (t-0.5)/0.3 * 180))
            font_tag = get_font(22)
            tag = "更多精彩内容，下期见！"
            bbox3 = draw.textbbox((0,0), tag, font=font_tag)
            tw3 = bbox3[2] - bbox3[0]
            draw.text((WIDTH//2 - tw3//2, text_y + 140), tag,
                     fill=(*PIXEL_GREEN[:3], ta), font=font_tag)
        
        img.save(os.path.join(frames_dir, f"frame_{f:03d}.png"), "PNG")
    
    print(f"    -> {frames_dir}/ ({total} frames)")
    return frames_dir


def frames_to_video(frames_dir, output_name, duration):
    import subprocess
    output_path = os.path.join(BRAND_DIR, output_name)
    cmd = [
        'ffmpeg', '-y', '-framerate', '30',
        '-i', os.path.join(frames_dir, 'frame_%03d.png'),
        '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
        '-preset', 'slow', '-crf', '18',
        '-t', str(duration), output_path
    ]
    subprocess.run(cmd, capture_output=True)
    print(f"    -> {output_path}")
    return output_path


def main():
    print("=" * 55)
    print("  🐕 像素旅人Ray — 品牌素材 v3 (外部像素狗)")
    print("=" * 55)
    
    create_logo()
    create_watermark()
    
    intro_dir = create_intro_frames()
    frames_to_video(intro_dir, "intro.mp4", 3)
    
    outro_dir = create_outro_frames()
    frames_to_video(outro_dir, "outro.mp4", 2)
    
    print(f"\n{'='*55}")
    print("  ✅ 全部生成完成!")
    print("=" * 55)


if __name__ == "__main__":
    main()
