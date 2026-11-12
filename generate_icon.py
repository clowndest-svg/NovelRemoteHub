# -*- coding: utf-8 -*-
r"""
NovelRemoteHub · 智笔云枢
高精矢量级 App 图标生成器 (支持 Android 多分辨率适配)
设计语言：星空深墨蓝底色 + 赛博流光金轨道 + 智能写作文枢羽笔 (Cyber Quill & Codex)
"""
import os
import math
from PIL import Image, ImageDraw, ImageFont

BASE_DIR = r"D:\RuanJian\NovelRemoteHub"
RES_DIR = os.path.join(BASE_DIR, "app_src", "res")

def draw_quill_hub_logo(size=512):
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # 1. 绘制圆角玄黑深邃背景
    corner_radius = size // 5
    for r in range(size // 2, 0, -2):
        factor = r / (size / 2)
        # 中心星空墨蓝 (#0f172a) 到边缘纯玄黑 (#05070a)
        c_r = int(15 * factor + 5 * (1 - factor))
        c_g = int(23 * factor + 7 * (1 - factor))
        c_b = int(42 * factor + 10 * (1 - factor))
        draw.ellipse([size/2 - r, size/2 - r, size/2 + r, size/2 + r], fill=(c_r, c_g, c_b, 255))

    # 裁切为平滑圆角矩形
    mask = Image.new("L", (size, size), 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.rounded_rectangle([0, 0, size, size], radius=corner_radius, fill=255)
    img.putalpha(mask)

    draw = ImageDraw.Draw(img)
    center = size / 2

    # 颜色定义
    gold_outer = (226, 183, 64, 255)       # 仙秦玄金
    gold_bright = (255, 215, 0, 255)      # 皓金高光
    cyan_energy = (56, 189, 248, 220)     # 灵感量子青
    indigo_deep = (30, 41, 59, 240)       # 墨渊深色

    # 2. 外层星轨光环 (模拟宇宙宏观与多书架星系)
    draw.ellipse([size*0.09, size*0.09, size*0.91, size*0.91], outline=gold_outer, width=max(2, size//64))
    draw.ellipse([size*0.13, size*0.13, size*0.87, size*0.87], outline=(226, 183, 64, 100), width=max(1, size//120))

    # 3. 绘制量子交叉轨道 (灵感共振)
    draw.arc([size*0.14, size*0.28, size*0.86, size*0.72], start=15, end=195, fill=cyan_energy, width=max(2, size//90))
    draw.arc([size*0.14, size*0.28, size*0.86, size*0.72], start=210, end=345, fill=cyan_energy, width=max(2, size//90))
    draw.arc([size*0.28, size*0.14, size*0.72, size*0.86], start=105, end=285, fill=gold_bright, width=max(2, size//90))
    draw.arc([size*0.28, size*0.14, size*0.72, size*0.86], start=300, end=75, fill=gold_bright, width=max(2, size//90))

    # 4. 绘制展开的书卷/灵枢典籍底座 (Codex Base)
    # 左书页
    left_book = [
        (center - size*0.02, center + size*0.30),
        (center - size*0.28, center + size*0.24),
        (center - size*0.32, center + size*0.05),
        (center - size*0.04, center + size*0.10)
    ]
    draw.polygon(left_book, fill=(24, 32, 47, 230), outline=gold_outer, width=max(1, size//120))

    # 右书页
    right_book = [
        (center + size*0.02, center + size*0.30),
        (center + size*0.28, center + size*0.24),
        (center + size*0.32, center + size*0.05),
        (center + size*0.04, center + size*0.10)
    ]
    draw.polygon(right_book, fill=(24, 32, 47, 230), outline=gold_outer, width=max(1, size//120))

    # 5. 绘制中央凌空悬浮的赛博智笔笔尖 (Cyber Fountain Pen / Stylus Nib)
    # 笔尖主体 (从顶部向下收敛到中心笔尖)
    nib_top = center - size*0.32
    nib_tip = center + size*0.16
    nib_half_w = size * 0.16

    nib_poly = [
        (center, nib_tip),                     # 笔尖最底尖端
        (center - nib_half_w, center - size*0.05), # 笔身左折角
        (center - nib_half_w * 0.8, nib_top),  # 笔身左顶
        (center + nib_half_w * 0.8, nib_top),  # 笔身右顶
        (center + nib_half_w, center - size*0.05), # 笔身右折角
    ]
    draw.polygon(nib_poly, fill=(18, 24, 38, 255), outline=gold_outer, width=max(3, size//60))

    # 笔身金色导流纹路与中缝 (Pen Nib Split & Breather Hole)
    draw.line([(center, nib_tip), (center, center - size*0.10)], fill=gold_bright, width=max(2, size//90))
    # 气孔 Breather Hole (发光的智能之眼)
    eye_r = size * 0.035
    draw.ellipse([center - eye_r, center - size*0.10 - eye_r, center + eye_r, center - size*0.10 + eye_r], fill=cyan_energy, outline=gold_bright, width=max(1, size//120))

    # 笔翼几何装饰线
    draw.line([(center - nib_half_w*0.8, nib_top + size*0.06), (center - nib_half_w*0.4, center - size*0.05)], fill=gold_outer, width=max(1, size//120))
    draw.line([(center + nib_half_w*0.8, nib_top + size*0.06), (center + nib_half_w*0.4, center - size*0.05)], fill=gold_outer, width=max(1, size//120))

    # 6. 笔尖光芒微粒 (灵感星火)
    sparkle_pts = [
        (center, nib_tip + size*0.04),
        (center - size*0.08, nib_tip + size*0.08),
        (center + size*0.08, nib_tip + size*0.08)
    ]
    for spx, spy in sparkle_pts:
        draw.ellipse([spx-2, spy-2, spx+2, spy+2], fill=gold_bright)

    return img

def main():
    img_512 = draw_quill_hub_logo(512)
    target_512 = os.path.join(BASE_DIR, "app_logo_512.png")
    img_512.save(target_512, "PNG")
    print(f"[Icon] 512x512 高清母图已生成: {target_512}")

    # 导出到各个 Android 规格
    densities = {
        "drawable-mdpi": 48,
        "drawable-hdpi": 72,
        "drawable-xhdpi": 96,
        "drawable-xxhdpi": 144,
        "drawable-xxxhdpi": 192,
        "drawable": 96
    }

    for folder, dim in densities.items():
        fpath = os.path.join(RES_DIR, folder)
        os.makedirs(fpath, exist_ok=True)
        out_file = os.path.join(fpath, "ic_launcher.png")
        resized = img_512.resize((dim, dim), Image.Resampling.LANCZOS)
        resized.save(out_file, "PNG")
        print(f"  -> 已输出 {folder}/ic_launcher.png ({dim}x{dim})")

if __name__ == "__main__":
    main()
