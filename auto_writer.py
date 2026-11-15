# -*- coding: utf-8 -*-
"""
AutoWriter: 多小说每日全自动续写与多引擎智能梯队降级巡检核心模块
- 支持设置开关控制（默认关闭）与自定义每日章节数 (1~100)
- 支持多 AI 引擎健康探针（Antigravity -> Codex -> DeepSeek API），首选/次选/底选自由排序与自动降级
- 支持全套设定集通读与“大纲完备性自愈检测”（缺细纲自动推演补全新细纲写回文档后再写正文）
- 支持全梯队熔断保护（电脑关机且 API 欠费时安全挂起，绝不下发脏任务）
- 支持 Web 前端看板直观诊断与一键立即执行
"""

import os
import re
import json
import time
import glob
from pathlib import Path
from storage import atomic_json, atomic_write
import novel_manager

STATUS_FILE_NAME = "auto_write_status.json"


def get_status_file_path(data_dir=None):
    base = data_dir or os.path.dirname(__file__)
    return os.path.join(base, STATUS_FILE_NAME)


def load_auto_write_status(data_dir=None):
    path = get_status_file_path(data_dir)
    defaults = {
        "enabled": False,
        "status": "disabled",
        "last_run_date": "",
        "last_run_time": 0,
        "active_engine": "",
        "probe_reports": {},
        "message": "每日自动续写未开启（可在系统设置中手动开启）",
        "novel_reports": {},
        "history": []
    }
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                saved = json.load(f)
                defaults.update(saved)
        except Exception:
            pass
    return defaults


def save_auto_write_status(status, data_dir=None):
    path = get_status_file_path(data_dir)
    atomic_json(path, status)
    return status


def probe_engine_health(engine_id, config_dir=None, local_status=None):
    """
    轻量级前置健康探针 (Pre-flight Probe)
    - Antigravity / Codex: 检测本地电脑心跳在线状态 (45s内)
    - DeepSeek API: 访问官方 /user/balance 接口检测账户可用性与实时余额
    返回: (is_healthy: bool, report_message: str, details: dict)
    """
    engine_id = (engine_id or "").strip().lower()
    local_status = local_status or {}
    now = time.time()

    if engine_id in ("antigravity", "codex"):
        is_online = bool(local_status.get("online", False))
        last_seen = float(local_status.get("last_seen", 0))
        ago = int(now - last_seen) if last_seen > 0 else -1

        if is_online and last_seen > 0 and (now - last_seen <= 45):
            name_map = {"antigravity": "Antigravity 本地执行端", "codex": "Codex 工作区执行端"}
            return True, f"{name_map.get(engine_id, engine_id)} 在线就绪（心跳延迟 {ago}s）", {"last_seen": last_seen, "ago": ago}
        else:
            time_desc = f"{ago} 秒前" if ago >= 0 else "无心跳记录"
            return False, f"本地电脑未开机或桥接程序未运行（最近心跳：{time_desc}）", {"last_seen": last_seen, "ago": ago}

    elif engine_id == "deepseek":
        try:
            import deepseek_client
            ds_cfg = deepseek_client.get_deepseek_config(config_dir)
            api_key = ds_cfg.get("api_key", "").strip()
            if not api_key:
                return False, "未配置 DeepSeek 官方 API Key，请在设置中配置", {"error": "missing_api_key"}

            ok, msg = deepseek_client.test_api_key(api_key, ds_cfg.get("base_url"))
            return ok, msg, {"api_test_msg": msg}
        except Exception as e:
            return False, f"DeepSeek 接口探测异常: {str(e)}", {"error": str(e)}

    return False, f"未知或不支持的引擎类型: {engine_id}", {}


def evaluate_engine_ladder(order=None, fallback_enabled=True, config_dir=None, local_status=None):
    """
    按设置中的优先级顺序依次进行健康探测，选择首个可用引擎
    若首选失败且开启自动降级，则无缝尝试次选与底选；
    全灭时返回 None 及全部探针诊断，触发全梯队安全熔断。
    """
    if not order:
        order = ["antigravity", "codex", "deepseek"]

    probe_reports = {}
    chosen_engine = None

    for eng in order:
        eng_clean = eng.strip().lower()
        ok, msg, details = probe_engine_health(eng_clean, config_dir, local_status)
        probe_reports[eng_clean] = {
            "ok": ok,
            "message": msg,
            "timestamp": time.time(),
            "details": details
        }
        if ok and not chosen_engine:
            chosen_engine = eng_clean
            if not fallback_enabled:
                break

    return chosen_engine, probe_reports


def inspect_book_outlines(book_root, start_chapter, count):
    """
    通读 00_设定集/ 下所有文件，检查从 start_chapter 到 start_chapter + count - 1 的分章细纲是否完备
    返回: {
        "ready": bool,
        "missing_chapters": list[int],
        "covered_count": int,
        "outline_file": str | None
    }
    """
    book_path = Path(book_root)
    settings_dir = book_path / "00_设定集"
    if not settings_dir.exists():
        return {
            "ready": False,
            "missing_chapters": list(range(start_chapter, start_chapter + count)),
            "covered_count": 0,
            "outline_file": None
        }

    setting_files = sorted(settings_dir.glob("*.md"))
    combined_text = ""
    preferred_outline_file = None

    for f in setting_files:
        try:
            content = f.read_text(encoding="utf-8-sig")
            combined_text += "\n" + content
            if "细纲" in f.name or "大纲" in f.name:
                if not preferred_outline_file or "03_" in f.name:
                    preferred_outline_file = str(f)
        except Exception:
            continue

    if not preferred_outline_file and setting_files:
        preferred_outline_file = str(setting_files[0])

    target_chapters = list(range(start_chapter, start_chapter + count))
    missing = []

    for chap_num in target_chapters:
        # 正则匹配形如: ### 第031章 或 第31章: 或 - 第031章 或 第1卷 第031章
        pattern = rf"(?m)(?:^|[^\d])第0*{chap_num}章(?:[^\d]|$)"
        if not re.search(pattern, combined_text):
            missing.append(chap_num)

    return {
        "ready": len(missing) == 0,
        "missing_chapters": missing,
        "covered_count": count - len(missing),
        "outline_file": preferred_outline_file
    }


def generate_and_append_outlines(book_root, book_id, missing_chapters, generate_func):
    """
    为缺失的章节自动推演分章细纲并无缝格式化写回设定集对应文档
    """
    if not missing_chapters:
        return True, "细纲已齐备，无需补全"

    book_path = Path(book_root)
    settings_dir = book_path / "00_设定集"
    settings_dir.mkdir(parents=True, exist_ok=True)

    # 汇总现有设定作为背景材料
    existing_materials = []
    for f in sorted(settings_dir.glob("*.md")):
        try:
            t = f.read_text(encoding="utf-8-sig").strip()
            if t:
                existing_materials.append(f"【设定文档 {f.name}】\n{t[:4000]}")
        except Exception:
            continue

    materials_str = "\n\n".join(existing_materials)
    min_chap = min(missing_chapters)
    max_chap = max(missing_chapters)

    prompt = f"""【《{book_id}》分章大纲自动推演与补全指令】
当前小说工程工作区：`{book_root}`
检测到本书接下来的第 {min_chap} 至 第 {max_chap} 章尚未规划分章细纲。
为保证后续正文写作节奏紧凑、战力不崩、前后呼应，请通读以下全书核心设定，为缺失的第 {min_chap} 至 第 {max_chap} 章推演严密的标准化分章细纲：

全套设定集核心参考资料：
{materials_str}

【分章细纲生成标准规范】：
每一章必须严格按照以下结构输出，拒绝水字数，突出情节推进与冲突：
### 第XXX章 章节标题
- **时间与核心场景**：
- **核心剧情推进与主要冲突**：
- **情绪爽点与反差伏笔**：
- **章末强力断章悬念钩子**：

请直接输出上述缺失章节的标准化细纲文档文本，无需输出额外开场白或解释。"""

    new_outlines_text = generate_func(prompt)
    if not new_outlines_text or len(new_outlines_text.strip()) < 100:
        return False, "大纲生成结果过短或无效"

    # 定位最适大纲文档进行追加写入
    target_outline_file = settings_dir / "03_全书主线与首卷分章细纲.md"
    if not target_outline_file.exists():
        candidates = list(settings_dir.glob("*细纲*.md")) + list(settings_dir.glob("*大纲*.md"))
        if candidates:
            target_outline_file = candidates[0]

    header_banner = f"\n\n---\n\n## 自动推演续接细纲（第{min_chap}章～第{max_chap}章）\n\n"
    append_content = header_banner + new_outlines_text.strip() + "\n"

    try:
        if target_outline_file.exists():
            original = target_outline_file.read_text(encoding="utf-8-sig")
            updated = original.rstrip() + append_content
            atomic_write(str(target_outline_file), updated)
        else:
            atomic_write(str(target_outline_file), f"# 《{book_id}》分章大纲\n" + append_content)
        return True, f"已成功为第 {min_chap}~{max_chap} 章推演新细纲并追加写回 {target_outline_file.name}"
    except Exception as err:
        return False, f"写入新细纲到设定集失败: {str(err)}"


class AutoWriterManager:
    """
    全自动每日续写与巡检调度中枢管理器
    """

    def __init__(self, data_dir=None, books_dir=None):
        self.data_dir = data_dir or os.path.dirname(__file__)
        self.books_dir = books_dir or os.path.join(self.data_dir, "books")

    def get_status(self):
        settings = novel_manager.get_user_settings(self.data_dir)
        status = load_auto_write_status(self.data_dir)
        status["enabled"] = bool(settings.get("auto_write_enabled", False))
        status["settings"] = {
            "auto_write_enabled": settings.get("auto_write_enabled", False),
            "auto_write_chapters": settings.get("auto_write_chapters", 10),
            "auto_write_time": settings.get("auto_write_time", "06:00"),
            "auto_write_engine_order": settings.get("auto_write_engine_order", ["antigravity", "codex", "deepseek"]),
            "auto_write_fallback": settings.get("auto_write_fallback", True)
        }
        return status

    def run_pipeline(self, trigger_type="scheduled", force=False, local_status=None, dispatch_task_func=None, direct_generate_func=None):
        """
        执行每日自动化巡检连更完整流水线
        """
        settings = novel_manager.get_user_settings(self.data_dir)
        status = self.get_status()
        today_str = time.strftime("%Y-%m-%d")

        # 1. 开关检查 (默认关闭保护)
        if not settings.get("auto_write_enabled", False) and not force:
            status.update(
                status="disabled",
                message="每日自动续写未开启（可在系统设置中手动开启）"
            )
            save_auto_write_status(status, self.data_dir)
            return False, "每日自动续写功能处于关闭状态", status

        # 2. 幂等防重检查 (当日已跑过且非强制重跑)
        if not force and status.get("last_run_date") == today_str and status.get("status") == "completed":
            return True, f"今日 ({today_str}) 自动化续写已完成，无需重复执行", status

        # 3. 引擎优先级梯队健康探测
        order = settings.get("auto_write_engine_order", ["antigravity", "codex", "deepseek"])
        fallback = settings.get("auto_write_fallback", True)
        chosen_engine, probe_reports = evaluate_engine_ladder(order, fallback, self.data_dir, local_status)

        status["probe_reports"] = probe_reports
        status["last_probe_time"] = time.time()

        # 全梯队熔断拦截 (电脑关机 且 DS欠费)
        if not chosen_engine:
            failure_reasons = [f"{eng}: {r.get('message')}" for eng, r in probe_reports.items()]
            summary_msg = "全梯队 AI 引擎不可用，今日自动续写安全熔断暂停。" + "；".join(failure_reasons)
            status.update(
                status="blocked",
                active_engine="",
                message=summary_msg
            )
            save_auto_write_status(status, self.data_dir)
            return False, summary_msg, status

        # 锁定胜出引擎
        target_chapters_per_book = settings.get("auto_write_chapters", 10)
        status.update(
            status="running",
            active_engine=chosen_engine,
            message=f"已选定主力 AI 引擎【{chosen_engine}】，正在通读设定集并规划每日 {target_chapters_per_book} 章连更..."
        )
        save_auto_write_status(status, self.data_dir)

        # 4. 遍历所有小说进行大纲自愈核验与连更任务下发
        books = novel_manager.list_books(self.books_dir)
        novel_reports = {}
        all_success = True

        for b in books:
            b_id = b["id"]
            b_path, _ = novel_manager.get_book_path(b_id, self.books_dir)
            c_files = novel_manager.chapter_files(b_path)
            current_max = 0
            for cf in c_files:
                m = re.search(r"第(\d+)章", Path(cf).name)
                if m:
                    current_max = max(current_max, int(m.group(1)))

            start_chap = current_max + 1
            # 检查大纲完备性
            outline_check = inspect_book_outlines(b_path, start_chap, target_chapters_per_book)

            # 4.A 缺细纲 -> 先调用 AI 补全新细纲
            if not outline_check["ready"]:
                if direct_generate_func:
                    gen_ok, gen_msg = generate_and_append_outlines(b_path, b_id, outline_check["missing_chapters"], direct_generate_func)
                    if not gen_ok:
                        novel_reports[b_id] = {"ok": False, "step": "outline", "error": gen_msg}
                        all_success = False
                        continue
                else:
                    # 若无法直接同步生成，且有 dispatch 派发通道，则作为推演任务派发
                    if dispatch_task_func:
                        dispatch_task_func(b_id, "outline", f"自动推演第 {start_chap}~{start_chap + target_chapters_per_book - 1} 章细纲", chosen_engine, extra={"auto_pipeline": True})
                    novel_reports[b_id] = {"ok": True, "step": "outline_dispatched", "message": f"已派发第 {start_chap} 章起的大纲推演任务"}
                    continue

            # 4.B 细纲齐备 -> 下发批量正文续写任务
            if dispatch_task_func:
                user_p = f"严格遵循《{b_id}》设定集与最新分章细纲，保持单主线高燃推进，逐章创作全新正文，每章紧扣末尾高能反转断章钩子。"
                t_ok, t_res = dispatch_task_func(
                    book_id=b_id,
                    workflow="serial",
                    prompt=user_p,
                    engine_mode=chosen_engine,
                    chapter_count=target_chapters_per_book,
                    extra={"auto_pipeline": True, "daily_auto": True}
                )
                novel_reports[b_id] = {
                    "ok": t_ok,
                    "step": "writing_dispatched",
                    "chapter_range": f"第{start_chap}至{start_chap + target_chapters_per_book - 1}章",
                    "result": t_res
                }
                if not t_ok:
                    all_success = False
            else:
                novel_reports[b_id] = {
                    "ok": True,
                    "step": "ready",
                    "chapter_range": f"第{start_chap}至{start_chap + target_chapters_per_book - 1}章"
                }

        # 5. 完成记录与状态更新
        status.update(
            status="completed" if all_success else "partial_failed",
            last_run_date=today_str,
            last_run_time=time.time(),
            novel_reports=novel_reports,
            message=f"今日全自动连更流水线已就绪，已为各小说调度每日 {target_chapters_per_book} 章创作任务（主力AI：{chosen_engine}）"
        )
        save_auto_write_status(status, self.data_dir)
        return all_success, status["message"], status
