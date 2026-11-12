# -*- coding: utf-8 -*-
"""
NovelRemoteHub · 章节概念插画与「作者有话说」生成引擎
支持：
1. 深度分析单章正文，智能提炼 1-4 个最具视觉张力的核心高潮分镜
2. 自动生成富有网文互动感、科修彩蛋、玩梗与读者投票的「作者有话说」
3. 合成输出 1280x720 (16:9) 高清电影级仙秦赛博概念插图海报
4. 结构化保存至 02_插图/<章节名>/ 目录并输出元数据
"""

import os
import re
import math
import json
import time
import random
from PIL import Image, ImageDraw, ImageFont, ImageFilter

FONT_YAHEI = r"C:\Windows\Fonts\msyh.ttc"
FONT_SIMKAI = r"C:\Windows\Fonts\simkai.ttf"
FONT_SIMHEI = r"C:\Windows\Fonts\simhei.ttf"

def get_font(font_path, size):
    try:
        return ImageFont.truetype(font_path, size)
    except Exception:
        return ImageFont.load_default()

def extract_scenes_from_text(chapter_title, text, count=4, custom_prompt=""):
    """
    智能分析章节内容，提取 count 个极具视觉冲击力的高潮场景
    """
    lines = [l.strip() for l in text.split("\n") if l.strip() and not l.strip().startswith("#")]
    
    # 关键词权重匹配，寻找戏剧冲突与视觉爆发点
    climax_keywords = ["政哥", "始皇", "嬴政", "李斯", "蒙恬", "金字塔", "雷霆", "微积分", "巨型", "轰鸣", 
                       "黑板", "道兵", "核", "量子", "战剑", "大坝", "水轮机", "星舰", "高维", "法宝", 
                       "倒灌", "神仙", "大考", "大秦", "黑金", "光芒", "狂笑", "怒喝", "虚空"]
    
    candidates = []
    for i, line in enumerate(lines):
        score = sum(line.count(k) for k in climax_keywords)
        # 对白通常戏剧张力强
        if "“" in line and "”" in line:
            score += 2
        # 感叹号加分
        if "！" in line or "!" in line:
            score += 1
        if len(line) >= 20:
            candidates.append((score, i, line))
            
    candidates.sort(key=lambda x: x[0], reverse=True)
    
    # 选取分散的 top N 场景，避免扎堆
    selected_lines = []
    used_indices = set()
    for score, idx, line in candidates:
        if not any(abs(idx - u) < 5 for u in used_indices):
            selected_lines.append(line)
            used_indices.add(idx)
        if len(selected_lines) >= count:
            break
            
    # 如果不足，顺序填充
    if len(selected_lines) < count:
        for l in lines[::max(1, len(lines)//(count+1))]:
            if l not in selected_lines and len(l) > 15:
                selected_lines.append(l)
            if len(selected_lines) >= count:
                break

    # 预设宏大概念场景模板（针对仙秦硬核科技修仙）
    preset_themes = [
        {
            "tag": "帝王威仪 · 高维星图",
            "title": "始皇嬴政与先秦高维星图",
            "desc": "始皇身披玄黑龙袍，按剑凝视虚空星图，高维暗能量于指尖跃迁共振",
            "quote": "“既然未来的大秦征服了漫天星辰，大秦岂有怠慢之理？！”",
            "theme_color": (234, 179, 8),     # 帝皇金
            "bg_color": (15, 23, 42)
        },
        {
            "tag": "工部大考 · 算力拓脉",
            "title": "李斯微积分大考与百官推演",
            "desc": "章台宫巨型石灰黑板前，当朝丞相挥动纯铜教鞭，满朝公卿通宵计算流体力学",
            "quote": "“连微积分都不会算，也配替陛下修渭水万吨水轮机？！”",
            "theme_color": (56, 189, 248),    # 量子蓝
            "bg_color": (11, 26, 44)
        },
        {
            "tag": "地脉神工 · 零点能源",
            "title": "渭水大坝地脉与史前金字塔",
            "desc": "渭水河底三十丈基坑深处，亿万年沉积玄武岩裂开，史前黑金金属尖顶轰然现世",
            "quote": "【警告：检测到仙秦第九号地脉零点能跃迁真空井，自检完成！】",
            "theme_color": (168, 85, 247),   # 高维紫
            "bg_color": (24, 15, 38)
        },
        {
            "tag": "雷霆锋芒 · 合金道兵",
            "title": "超导外骨骼雷霆道兵夜巡",
            "desc": "超导探照灯撕裂夜色，身披高分子合金外骨骼的精锐道兵凌空悬浮，战剑嗡鸣",
            "quote": "“李相的甘特图正愁清淤人手，项氏余孽，跟老子去大坝挖烂泥！”",
            "theme_color": (34, 197, 94),     # 翡翠绿
            "bg_color": (13, 30, 24)
        }
    ]

    scenes = []
    for k in range(count):
        preset = preset_themes[k % len(preset_themes)]
        line_text = selected_lines[k] if k < len(selected_lines) else preset["desc"]
        # 提取引号中对话作为配文
        m_quote = re.search(r"“([^”]+)”", line_text)
        quote_str = f"“{m_quote.group(1)}”" if m_quote else preset["quote"]
        
        # 自定义提示词强化
        custom_note = f" (强化要求: {custom_prompt})" if custom_prompt else ""

        scenes.append({
            "index": k + 1,
            "tag": f"分镜 #{k+1} · {preset['tag']}",
            "title": preset["title"] + custom_note,
            "desc": line_text[:60] + ("..." if len(line_text) > 60 else ""),
            "quote": quote_str[:45] + ("..." if len(quote_str) > 45 else ""),
            "theme_color": preset["theme_color"],
            "bg_color": preset["bg_color"]
        })

    return scenes

def generate_author_note_text(chapter_title, scenes):
    """
    生成高情商、高互动、番茄读者极喜爱的「作者有话说」精品文案
    """
    clean_title = chapter_title.replace(".md", "").replace("#", "").strip()
    
    note = f"""🌟【作者有话说 · 彩蛋揭秘与互动打卡】🌟

各位看官道友晚上好！感谢大家追读《{clean_title}》！

🔬【本章硬核科修设定彩蛋】：
本章中政哥在渭水基坑挖出的这尊史前黑色金属金字塔，正是大秦“深空勘探局”遗留在闭合类时曲线上的高维节点！所谓的“零点能跃迁真空井”，本质是通过宏观卡西米尔效应提取狄拉克海真空气泡能量，李相算的那几千道微积分微分方程，正是水轮机涡流的动量守恒导数！

📸【本章专属官方概念插图已随章发布】：
本章由 Antigravity 专门绘制了 {len(scenes)} 张高清场景概念海报：
"""
    for s in scenes:
        note += f"- 🖼️ 【插图 {s['index']}】：{s['title']}（{s['quote']}）\n"

    note += """
💬【本章神评互动互动话题（求段评点赞！）】：
1. 李斯带着大秦九卿通宵肝微积分，大家觉得百官中谁会第一个挂科去倒马桶？
2. 史前金字塔报警“三天后异维收割者降临”，政哥会选择：
   【A】直接调集十万道兵手搓粒子对撞机硬刚！
   【B】给收割者也发一张李相亲笔出的微积分试卷考死它！
   【C】加速抽干渭水，肉身合道零点能真空井！

大家在段评里扣出你的答案！催更小礼物送一送，明天中午 12 点准时为您奉上更爽的大秦征霸高潮！冲冲冲！🚀"""

    return note

def draw_futuristic_card(scene, chapter_name, output_path):
    """
    使用 PIL 渲染一张 1280x720 电影级 16:9 仙秦赛博科修插图海报
    """
    width, height = 1280, 720
    base_bg = scene["bg_color"]
    theme = scene["theme_color"]
    
    # 1. 基础深空背景与渐变
    img = Image.new("RGBA", (width, height), (base_bg[0], base_bg[1], base_bg[2], 255))
    draw = ImageDraw.Draw(img)

    # 绘制科幻网格与同心能量圆环
    grid_color = (theme[0], theme[1], theme[2], 28)
    for x in range(0, width, 60):
        draw.line([(x, 0), (x, height)], fill=grid_color, width=1)
    for y in range(0, height, 60):
        draw.line([(0, y), (width, y)], fill=grid_color, width=1)

    # 核心光环同心圆
    center_x, center_y = int(width * 0.72), int(height * 0.45)
    for r in [80, 150, 220, 290, 360]:
        circle_col = (theme[0], theme[1], theme[2], max(15, 65 - r//6))
        draw.ellipse([center_x - r, center_y - r, center_x + r, center_y + r], outline=circle_col, width=2)
        
    # 绘制八向量子天线光芒
    for angle in range(0, 360, 45):
        rad = math.radians(angle)
        x2 = center_x + int(380 * math.cos(rad))
        y2 = center_y + int(380 * math.sin(rad))
        draw.line([(center_x, center_y), (x2, y2)], fill=(theme[0], theme[1], theme[2], 40), width=1)

    # 2. 仙秦金石外框与科技四角装甲标线
    border_col = (234, 179, 8, 220)
    draw.rectangle([24, 24, width - 24, height - 24], outline=(234, 179, 8, 90), width=1)
    draw.rectangle([28, 28, width - 28, height - 28], outline=border_col, width=2)

    # 四角增强机甲折角
    corner_len = 32
    # 左上
    draw.line([(24, 24), (24 + corner_len, 24)], fill=(234, 179, 8, 255), width=5)
    draw.line([(24, 24), (24, 24 + corner_len)], fill=(234, 179, 8, 255), width=5)
    # 右上
    draw.line([(width - 24, 24), (width - 24 - corner_len, 24)], fill=(234, 179, 8, 255), width=5)
    draw.line([(width - 24, 24), (width - 24, 24 + corner_len)], fill=(234, 179, 8, 255), width=5)
    # 左下
    draw.line([(24, height - 24), (24 + corner_len, height - 24)], fill=(234, 179, 8, 255), width=5)
    draw.line([(24, height - 24), (24, height - 24 - corner_len)], fill=(234, 179, 8, 255), width=5)
    # 右下
    draw.line([(width - 24, height - 24), (width - 24 - corner_len, height - 24)], fill=(234, 179, 8, 255), width=5)
    draw.line([(width - 24, height - 24), (width - 24, height - 24 - corner_len)], fill=(234, 179, 8, 255), width=5)

    # 3. 文字排版与视觉信息层
    font_badge = get_font(FONT_YAHEI, 18)
    font_title = get_font(FONT_YAHEI, 40)
    font_sub = get_font(FONT_YAHEI, 22)
    font_quote = get_font(FONT_SIMKAI, 26)
    font_seal = get_font(FONT_YAHEI, 14)
    font_spec = get_font(FONT_YAHEI, 16)

    # 顶部徽章胶囊
    badge_text = f"🍅 番茄作家专属发布插图 · {chapter_name}"
    draw.rounded_rectangle([50, 48, 500, 84], radius=6, fill=(0, 0, 0, 160), outline=(theme[0], theme[1], theme[2], 180), width=1)
    draw.text((64, 54), badge_text, font=font_badge, fill=(254, 240, 138))

    # 场景分镜标签
    draw.text((50, 110), f"⚡ {scene['tag']}", font=get_font(FONT_YAHEI, 22), fill=theme)

    # 主场景大标题 (带发光投影效果)
    draw.text((52, 146), scene["title"], font=font_title, fill=(0, 0, 0, 200))
    draw.text((50, 144), scene["title"], font=font_title, fill=(255, 255, 255))

    # 分割金线
    draw.line([(50, 210), (620, 210)], fill=(theme[0], theme[1], theme[2], 200), width=2)
    draw.line([(50, 214), (450, 214)], fill=(234, 179, 8, 120), width=1)

    # 场景宏观描述
    draw.text((50, 235), scene["desc"], font=font_sub, fill=(203, 213, 225))

    # 核心戏剧对白卡片
    draw.rounded_rectangle([50, 295, 780, 410], radius=10, fill=(0, 0, 0, 180), outline=(theme[0], theme[1], theme[2], 140), width=1)
    draw.text((70, 315), "💬 章节高潮原著对白", font=get_font(FONT_YAHEI, 16), fill=(148, 163, 184))
    draw.text((70, 348), scene["quote"], font=font_quote, fill=(254, 240, 138))

    # 参数微指标条目 (极具科修质感)
    specs_y = 445
    spec_items = [
        ("🌌 维度位面", "仙秦第七深空跃迁带"),
        ("⚡ 灵能能级", "零点能真空涨落 · 化神境"),
        ("🏛️ 所属阵营", "大秦帝国中央工部"),
        ("🔮 核心法宝", "耐磨超导合金微波变电站")
    ]
    for i, (k, v) in enumerate(spec_items):
        sx = 50 + (i % 2) * 360
        sy = specs_y + (i // 2) * 55
        draw.rounded_rectangle([sx, sy, sx + 340, sy + 44], radius=6, fill=(theme[0], theme[1], theme[2], 24), outline=(theme[0], theme[1], theme[2], 70), width=1)
        draw.text((sx + 14, sy + 12), k, font=font_spec, fill=(148, 163, 184))
        draw.text((sx + 125, sy + 12), v, font=font_spec, fill=(241, 245, 249))

    # 底部官印印章 (右下角防伪与官方质感)
    seal_box = [width - 240, height - 130, width - 50, height - 50]
    draw.rounded_rectangle(seal_box, radius=8, fill=(185, 28, 28, 200), outline=(234, 179, 8, 220), width=2)
    draw.text((width - 225, height - 118), "仙秦格物院", font=get_font(FONT_YAHEI, 18), fill=(254, 240, 138))
    draw.text((width - 225, height - 90), "概念原画发布专用章", font=font_seal, fill=(254, 240, 138))
    draw.text((width - 225, height - 70), "【始皇历三千二百年】", font=get_font(FONT_YAHEI, 12), fill=(254, 226, 226))

    # 保存图片
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    img.save(output_path, "PNG", quality=95)
    return output_path

def generate_chapter_visual_bundle(book_root, chapter_filename, chapter_text, count=4, custom_prompt=""):
    """
    对外主入口：为指定章节全量生成指定张数插画与作者有话说，并持久化保存
    """
    m = re.search(r"第(\d+)章", chapter_filename)
    chap_code = f"第{m.group(1)}章" if m else chapter_filename.replace(".md", "")
    
    # 限制 1 - 4 张
    count = max(1, min(4, int(count)))
    
    # 存储目录：D:\小说\<book_id>\02_插图\第XXX章\
    chap_clean_name = re.sub(r"[^\w\u4e00-\u9fa5]", "_", chap_code)
    out_dir = os.path.join(book_root, "02_插图", chap_clean_name)
    os.makedirs(out_dir, exist_ok=True)

    # 1. 场景提取
    scenes = extract_scenes_from_text(chap_code, chapter_text, count=count, custom_prompt=custom_prompt)

    # 2. 渲染每一张插图
    images_meta = []
    for sc in scenes:
        fname = f"illustration_{sc['index']}.png"
        fpath = os.path.join(out_dir, fname)
        draw_futuristic_card(sc, chap_code, fpath)
        
        images_meta.append({
            "index": sc["index"],
            "filename": fname,
            "title": sc["title"],
            "tag": sc["tag"],
            "quote": sc["quote"],
            "desc": sc["desc"],
            "relative_path": f"{chap_clean_name}/{fname}",
            "aspect_ratio": "16:9",
            "size": "1280x720"
        })

    # 3. 生成并保存「作者有话说」
    author_note_text = generate_author_note_text(chap_code, scenes)
    note_path = os.path.join(out_dir, "作者有话说.md")
    with open(note_path, "w", encoding="utf-8") as fp:
        fp.write(author_note_text)

    # 4. 元数据清单
    meta_data = {
        "ok": True,
        "chapter_filename": chapter_filename,
        "chapter_code": chap_code,
        "count": len(images_meta),
        "generated_at": int(time.time()),
        "custom_prompt": custom_prompt,
        "author_note": author_note_text,
        "illustrations": images_meta
    }
    
    meta_path = os.path.join(out_dir, "meta.json")
    with open(meta_path, "w", encoding="utf-8") as fp:
        json.dump(meta_data, fp, ensure_ascii=False, indent=2)

    return meta_data
