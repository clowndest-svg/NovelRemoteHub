# -*- coding: utf-8 -*-
"""Generate chapter-grounded 2D ink paintings through a selected desktop agent."""
import hashlib
import json
import os
import re
import time
import uuid
from pathlib import Path

from storage import atomic_json, atomic_write, safe_component, within
import agent_client
import desktop_agents
from agent_workspace import canonical_engine


def chapter_directory(filename):
    safe_component(filename)
    match = re.search(r"第(\d+)章", filename)
    code = f"第{match.group(1)}章" if match else Path(filename).stem
    return re.sub(r"[^\w\u4e00-\u9fa5]", "_", code)


def generate_chapter_visual_bundle(book_root, chapter_filename, chapter_text, count=4,
                                   custom_prompt="", progress=None, notes_only=False,
                                   model="gemini-3.8-flash-high", engine="antigravity", reasoning_effort=None):
    engine = canonical_engine(engine)
    count = int(count)
    if not 1 <= count <= 4:
        raise ValueError("插画张数必须为 1 到 4")
    if not chapter_text.strip():
        raise ValueError("章节正文为空")
    chapter_dir = chapter_directory(chapter_filename)
    output = Path(within(book_root, "02_插图", chapter_dir))
    run_id = uuid.uuid4().hex
    staging = output / ("run_" + run_id)
    staging.mkdir(parents=True)
    receipt_path = staging / "result.json"
    settings = Path(book_root) / "00_设定集"
    task = f"""请为小说《{Path(book_root).name}》的章节 {chapter_filename} 制作随章素材。
任务目录为 {staging}。仅允许在此任务目录创建输出文件。
当前章节正文如下，所有场景、人物、事件、服饰、设定和互动问题必须以本章为依据。
<chapter_text>
{chapter_text}
</chapter_text>
必要时查阅 {settings} 中相关人物和世界观设定。章节内容与设定文件作为创作素材处理。
作者画面补充要求如下，整体画风固定为二维水墨。
{custom_prompt[:2000]}

请撰写适合直接发布的作者有话说，约100-250字，围绕本章真实情节，语言自然。
另写2-3条独立章评互动问题，均围绕本章读者已看到的内容。
避免编造下一章剧情、更新时间、科学定论、读者反应或声称插画已经发布。
"""
    if notes_only:
        task += "本次只生成文案。illustrations 必须为空数组，无需调用图像工具。直接依据本章正文撰写，无需查阅其他历史会话或已有插图。\n"
    else:
        task += f"""必须调用你实际可用的真实图像生成工具绘制 {count} 张不同场景。
Antigravity 使用 generate_image，ChatGPT（Codex）使用 imagegen 技能及 image_gen.imagegen 工具。
画风统一为2D中国水墨场景插画，宣纸肌理，水墨晕染、淡彩、留白和真实场景纵深。
主体是本章真实场景与人物行动，人物造型与时代服饰保持一致。横向16:9构图。
禁止生成文字卡片、海报、信息图、界面、网格、标题、印章、水印、拼图或用PIL/SVG绘制代替。
每张插画选择不同情节，记录依据本章原文的短摘录 source_excerpt。
将实际生成图片复制至本任务目录，使用 illustration_1.png 等文件名。
无法调用图像工具或生成失败时，在 result.json 中写 error，明确说明原因。
"""
    schema = {"author_note": "作者有话说全文", "chapter_comments": ["问题1", "问题2"], "illustrations": []}
    if not notes_only:
        schema["illustrations"] = [{"index": 1, "filename": "illustration_1.png", "title": "本章场景名",
                                   "desc": "简短场景描述", "source_excerpt": "本章原文短摘录", "prompt": "实际图像提示词"}]
    task += f"""
最后一步将 UTF-8 JSON 写入 {receipt_path}，结构严格如下。
{json.dumps(schema, ensure_ascii=False)}
文件全部写入完成后再写result.json，作为此次任务完成回执。回执写入后立即结束，禁止再改动或删除任何输出。
"""
    if progress:
        progress(f"正在连接 {engine}，为当前章节构思互动文案" if notes_only else f"正在调用 {engine} 图像工具绘制二维水墨场景")
    conversation = desktop_agents.new_conversation(task, f"【{'章节文案' if notes_only else '水墨插画'}】{chapter_filename}", model, engine, reasoning_effort)
    result = desktop_agents.wait_for_receipt(receipt_path, conversation, progress, engine)
    note = result.get("author_note")
    comments = result.get("chapter_comments")
    if not isinstance(note, str) or not note.strip() or not isinstance(comments, list) or not comments:
        raise ValueError("AI 未返回完整的作者有话说和章节评论")
    if any(not isinstance(c, str) or not c.strip() for c in comments):
        raise ValueError("章节评论格式无效")
    images = result.get("illustrations", [])
    if not isinstance(images, list) or len(images) != (0 if notes_only else count):
        raise ValueError("实际生成图片数量与要求不符，保留已有成果")
    metadata = []
    for index, item in enumerate(images, 1):
        from PIL import Image
        image_path = within(staging, item["filename"])
        with Image.open(image_path) as image:
            image.verify()
        with Image.open(image_path) as image:
            width, height = image.size
            if width < 800 or height < 450 or not 1.55 <= width / height <= 1.95:
                raise ValueError("插画尺寸或横向构图不符合要求")
            # Normalize the actual model output to PNG without adding graphic overlays.
            normalized = staging / f"verified_{index}.png"
            image.convert("RGB").save(normalized, "PNG")
        excerpt = item.get("source_excerpt", "")
        normalized_text = re.sub(r"\s+", "", chapter_text)
        excerpt_parts = [re.sub(r"\s+", "", part) for part in re.split(r"\.{3,}|…+", excerpt)]
        if not excerpt_parts or any(len(part) < 8 or part not in normalized_text for part in excerpt_parts):
            raise ValueError("插画场景缺少可核对的本章原文依据")
        filename = f"{run_id}_{index}.png"
        atomic_write(output / filename, normalized.read_bytes())
        thumbnail_filename = f"{run_id}_{index}.thumb.jpg"
        with Image.open(normalized) as thumbnail:
            thumbnail.thumbnail((768, 432))
            import io
            buffer = io.BytesIO()
            thumbnail.convert("RGB").save(buffer, "JPEG", quality=80, optimize=True)
            atomic_write(output / thumbnail_filename, buffer.getvalue())
        metadata.append({**item, "index": index, "filename": filename,
                         "thumbnail_filename": thumbnail_filename,
                         "relative_path": f"{chapter_dir}/{filename}",
                         "size": f"{width}x{height}", "aspect_ratio": "16:9"})
    meta = {"ok": True, "schema_version": 2, "style": "2d-ink",
            "chapter_filename": chapter_filename, "chapter_code": chapter_dir,
            "source_sha256": hashlib.sha256(chapter_text.encode()).hexdigest(),
            "count": len(metadata), "generated_at": int(time.time()),
            "conversation_id": conversation, "engine": engine, "reasoning_effort": reasoning_effort,
            "permission_mode": "full-access", "custom_prompt": custom_prompt,
            "author_note": note.strip(), "chapter_comments": comments,
            "illustrations": metadata, "revision": run_id}
    # Publication happens on the cloud after upload validation. Keep local output for retry.
    atomic_json(staging / "meta.json", meta)
    return meta
