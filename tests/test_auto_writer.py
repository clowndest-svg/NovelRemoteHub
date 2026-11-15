# -*- coding: utf-8 -*-
import os
import shutil
import tempfile
import unittest
import time
import json
import re
from pathlib import Path
from unittest.mock import patch, MagicMock

import auto_writer
import novel_manager
import cloud_hub


class AutoWriterTest(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_autowriter_")
        self.books_dir = os.path.join(self.test_dir, "books")
        os.makedirs(self.books_dir, exist_ok=True)
        cloud_hub.BOOKS_DIR = self.books_dir
        cloud_hub.DATA_DIR = self.test_dir

        self.book_id = "测试小说_宇宙修仙"
        self.book_path = os.path.join(self.books_dir, self.book_id)
        self.settings_dir = os.path.join(self.book_path, "00_设定集")
        self.chapters_dir = os.path.join(self.book_path, "01_正文")
        os.makedirs(self.settings_dir, exist_ok=True)
        os.makedirs(self.chapters_dir, exist_ok=True)

        # 写入前 5 章正文
        for i in range(1, 6):
            cf = os.path.join(self.chapters_dir, f"第{i:03d}章 测试正文{i}.md")
            with open(cf, "w", encoding="utf-8") as f:
                f.write(f"第{i}章 测试正文{i}\n\n正文内容{i}。")

        # 写入设定集中的分章细纲（只规划到第 7 章）
        outline_content = """# 《测试小说_宇宙修仙》分章细纲
### 第001章 初始之变
- 剧情推进：主角觉醒
### 第002章 虚空微澜
- 剧情推进：探查裂缝
### 第003章 矩阵初构
- 剧情推进：推演阵法
### 第004章 灵气跃迁
- 剧情推进：灵脉异动
### 第005章 试炼突围
- 剧情推进：初次交锋
### 第006章 阵纹暗合
- 剧情推进：暗中筹划
### 第007章 暗潮涌动
- 剧情推进：风雨欲来
"""
        with open(os.path.join(self.settings_dir, "03_全书主线与首卷分章细纲.md"), "w", encoding="utf-8") as f:
            f.write(outline_content)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_default_settings_disabled(self):
        """测试出厂设置下自动续写默认关闭，且章节数默认为 10"""
        s = novel_manager.get_user_settings(self.test_dir)
        self.assertFalse(s.get("auto_write_enabled"))
        self.assertEqual(s.get("auto_write_chapters"), 10)
        self.assertEqual(s.get("auto_write_engine_order"), ["antigravity", "codex", "deepseek"])
        self.assertTrue(s.get("auto_write_fallback"))

    def test_engine_probe_antigravity(self):
        """测试 Antigravity / Codex 本地心跳探针"""
        now = time.time()
        # 心跳正常 (20s 前)
        ok, msg, _ = auto_writer.probe_engine_health("antigravity", local_status={"online": True, "last_seen": now - 20})
        self.assertTrue(ok)
        self.assertIn("在线就绪", msg)

        # 电脑关机或心跳超时 (60s 前)
        ok_offline, msg_offline, _ = auto_writer.probe_engine_health("antigravity", local_status={"online": False, "last_seen": now - 60})
        self.assertFalse(ok_offline)
        self.assertIn("未开机", msg_offline)

    def test_engine_probe_deepseek(self):
        """测试 DeepSeek API 探针：无 key 报错，有 key 模拟正常"""
        ok, msg, _ = auto_writer.probe_engine_health("deepseek", config_dir=self.test_dir)
        self.assertFalse(ok)
        self.assertIn("未配置", msg)

        with patch("deepseek_client.test_api_key", return_value=(True, "账户可用，余额充足")):
            with patch("deepseek_client.get_deepseek_config", return_value={"api_key": "sk-mock", "base_url": ""}):
                ok_ds, msg_ds, _ = auto_writer.probe_engine_health("deepseek", config_dir=self.test_dir)
                self.assertTrue(ok_ds)
                self.assertIn("账户可用", msg_ds)

    def test_engine_ladder_evaluation_and_fallback(self):
        """测试多引擎智能梯队降级：首选离线时自动切换到次选/底选"""
        now = time.time()
        # Antigravity 在线 -> 选 Antigravity
        chosen, reports = auto_writer.evaluate_engine_ladder(
            ["antigravity", "codex", "deepseek"],
            fallback_enabled=True,
            local_status={"online": True, "last_seen": now - 10}
        )
        self.assertEqual(chosen, "antigravity")

        # Antigravity 离线, DeepSeek 可用 -> 降级选 DeepSeek
        with patch("deepseek_client.test_api_key", return_value=(True, "账户可用")):
            with patch("deepseek_client.get_deepseek_config", return_value={"api_key": "sk-mock", "base_url": ""}):
                chosen_ds, reports_ds = auto_writer.evaluate_engine_ladder(
                    ["antigravity", "codex", "deepseek"],
                    fallback_enabled=True,
                    local_status={"online": False, "last_seen": 0}
                )
                self.assertEqual(chosen_ds, "deepseek")

        # 全灭 (电脑关机且 DS 未配置/欠费) -> 返回 None 熔断
        chosen_none, reports_none = auto_writer.evaluate_engine_ladder(
            ["antigravity", "codex", "deepseek"],
            fallback_enabled=True,
            local_status={"online": False, "last_seen": 0},
            config_dir=self.test_dir
        )
        self.assertIsNone(chosen_none)
        self.assertFalse(reports_none["antigravity"]["ok"])
        self.assertFalse(reports_none["codex"]["ok"])
        self.assertFalse(reports_none["deepseek"]["ok"])

    def test_inspect_book_outlines(self):
        """测试设定集通读与分章细纲完备度检测：6-7章有细纲，8-15章缺失"""
        # 从第 6 章开始续写 10 章（即 6 到 15 章）
        # 现有大纲包含 6 和 7，缺失 8 到 15
        check = auto_writer.inspect_book_outlines(self.book_path, start_chapter=6, count=10)
        self.assertFalse(check["ready"])
        self.assertEqual(check["missing_chapters"], list(range(8, 16)))
        self.assertEqual(check["covered_count"], 2)

    def test_generate_and_append_outlines(self):
        """测试缺失大纲时推演新细纲并格式化追加写回设定集"""
        missing = [8, 9, 10]
        def fake_generate(prompt):
            self.assertIn("第 8 至 第 10 章", prompt)
            return """### 第008章 暗潮迭起
- **时间与核心场景**：虚空暗域
- **核心剧情推进与主要冲突**：遭遇魔渊先锋
- **情绪爽点与反差伏笔**：主角翻手镇压
- **章末强力断章悬念钩子**：裂缝深处传来异响

### 第009章 逆转乾坤
- **时间与核心场景**：阵法枢纽
- **核心剧情推进与主要冲突**：逆转灵气回路
- **情绪爽点与反差伏笔**：全场震惊
- **章末强力断章悬念钩子**：神秘黑袍人现身

### 第010章 终极博弈
- **时间与核心场景**：绝巅论道
- **核心剧情推进与主要冲突**：正邪双峰对决
- **情绪爽点与反差伏笔**：破境登临混元
- **章末强力断章悬念钩子**：大秦战舰从虚空降临！"""

        ok, msg = auto_writer.generate_and_append_outlines(self.book_path, self.book_id, missing, fake_generate)
        self.assertTrue(ok)

        # 验证新大纲已经写回 00_设定集/03_全书主线与首卷分章细纲.md
        outline_file = os.path.join(self.settings_dir, "03_全书主线与首卷分章细纲.md")
        content = Path(outline_file).read_text(encoding="utf-8")
        self.assertIn("第008章 暗潮迭起", content)
        self.assertIn("第009章 逆转乾坤", content)
        self.assertIn("第010章 终极博弈", content)

        # 再次检查第 6~10 章，此时应当判定 ready == True
        recheck = auto_writer.inspect_book_outlines(self.book_path, start_chapter=6, count=5)
        self.assertTrue(recheck["ready"])
        self.assertEqual(recheck["missing_chapters"], [])

    def test_auto_writer_pipeline_disabled_by_default(self):
        """测试当全局开关为 False 且非 force 时，流水线直接被保护性拦截"""
        mgr = auto_writer.AutoWriterManager(self.test_dir, self.books_dir)
        ok, msg, status = mgr.run_pipeline(trigger_type="scheduled", force=False)
        self.assertFalse(ok)
        self.assertEqual(status["status"], "disabled")
        self.assertIn("关闭状态", msg)

    def test_auto_writer_pipeline_blocked_when_all_offline(self):
        """测试开启状态下若全部引擎不可用，触发全梯队安全熔断挂起"""
        # 启用自动续写
        novel_manager.save_user_settings({"auto_write_enabled": True}, self.test_dir)
        mgr = auto_writer.AutoWriterManager(self.test_dir, self.books_dir)

        # 传入离线状态且 DS 探测失败
        ok, msg, status = mgr.run_pipeline(
            trigger_type="scheduled",
            force=True,
            local_status={"online": False, "last_seen": 0}
        )
        self.assertFalse(ok)
        self.assertEqual(status["status"], "blocked")
        self.assertIn("安全熔断暂停", msg)

    def test_auto_writer_pipeline_success(self):
        """测试完整流水线：自动补全大纲 + 派发每日续写任务"""
        novel_manager.save_user_settings({"auto_write_enabled": True, "auto_write_chapters": 5}, self.test_dir)
        mgr = auto_writer.AutoWriterManager(self.test_dir, self.books_dir)

        dispatched_tasks = []
        def mock_dispatch(**kw):
            dispatched_tasks.append(kw)
            return True, {"task_id": "test_t1"}

        def mock_outline_gen(p):
            return """### 第008章 灵潮奔涌
- **时间与核心场景**：九霄灵域矩阵
- **核心剧情推进与主要冲突**：主角推演四维灵阵，引动星河共鸣，强敌来袭被正面震慑
- **情绪爽点与反差伏笔**：全场震撼，埋下虚空裂缝之伏笔
- **章末强力断章悬念钩子**：深空传来苍凉龙吟！

### 第009章 逆转法则
- **时间与核心场景**：大秦战舰主控舱
- **核心剧情推进与主要冲突**：反转热力学第二定律，逆转灵力回流
- **情绪爽点与反差伏笔**：以凡人之躯抗衡天道威压
- **章末强力断章悬念钩子**：二向箔降维打击即将展开！

### 第010章 终极降维
- **时间与核心场景**：降维打击边缘
- **核心剧情推进与主要冲突**：始皇帝手握量子玉玺，肉身硬抗二向箔
- **情绪爽点与反差伏笔**：大秦玄鸟旗插满三维宇宙
- **章末强力断章悬念钩子**：高维文明主宰被当场惊退！"""

        now = time.time()
        ok, msg, status = mgr.run_pipeline(
            trigger_type="scheduled",
            force=True,
            local_status={"online": True, "last_seen": now - 10},
            dispatch_task_func=mock_dispatch,
            direct_generate_func=mock_outline_gen
        )
        self.assertTrue(ok)
        self.assertEqual(status["status"], "completed")
        self.assertEqual(status["active_engine"], "antigravity")
        self.assertEqual(len(dispatched_tasks), 1)
        self.assertEqual(dispatched_tasks[0]["book_id"], self.book_id)
        self.assertEqual(dispatched_tasks[0]["chapter_count"], 5)

    def test_cloud_hub_dispatch_auto_task(self):
        """测试 cloud_hub.dispatch_auto_task 任务创建、防重入互斥与参数完整性"""
        cloud_hub.TASKS.clear()
        ok, res = cloud_hub.dispatch_auto_task(
            book_id=self.book_id,
            workflow="serial",
            prompt="测试自动连更",
            engine_mode="antigravity",
            chapter_count=10,
            extra={"auto_pipeline": True}
        )
        self.assertTrue(ok)
        tid = res["task_id"]
        self.assertIn(tid, cloud_hub.TASKS)
        t = cloud_hub.TASKS[tid]
        self.assertEqual(t["book_id"], self.book_id)
        self.assertEqual(t["extra_data"]["chapter_count"], 10)
        self.assertEqual(t["extra_data"]["workflow"], "serial")
        self.assertTrue(t["extra_data"]["auto_pipeline"])

        # 重复下发同一部小说，排他互斥防并发重入，直接复用已有任务
        ok2, res2 = cloud_hub.dispatch_auto_task(
            book_id=self.book_id,
            workflow="serial",
            prompt="重复请求",
            engine_mode="antigravity",
            chapter_count=10
        )
        self.assertTrue(ok2)
        self.assertTrue(res2.get("reused"))
        self.assertEqual(res2["task_id"], tid)

    def test_auto_ensure_outline_in_write_chapters(self):
        """测试在写新章节前，若缺少细纲，story_memory.write_chapters 自动先行补全大纲写回设定集再写正文"""
        import story_memory
        prefs = {"writing_context_chars": 64000, "writing_context_mode": "layered", "auto_ensure_outline": True}

        called_prompts = []
        def fake_llm(p):
            called_prompts.append(p)
            if "分章大纲自动推演" in p or "分章大纲" in p:
                return """### 第008章 暗潮迭起
- **时间与核心场景**：虚空暗域
- **核心剧情推进与主要冲突**：遭遇魔渊先锋并正面交锋
- **情绪爽点与反差伏笔**：主角翻手镇压强敌
- **章末强力断章悬念钩子**：裂缝深处传来异响！

### 第009章 逆转乾坤
- **时间与核心场景**：阵法枢纽
- **核心剧情推进与主要冲突**：逆转灵气回路化解死局
- **情绪爽点与反差伏笔**：全场震撼
- **章末强力断章悬念钩子**：神秘黑袍人现身！

### 第010章 终极博弈
- **时间与核心场景**：绝巅论道
- **核心剧情推进与主要冲突**：正邪双峰对决
- **情绪爽点与反差伏笔**：破境登临混元
- **章末强力断章悬念钩子**：大秦战舰从虚空降临！"""
            if "批量更新记忆" in p:
                keys = re.findall(r"【片段 id=([a-f0-9]+)】", p)
                records = [{"id": k, "summary": "前情线索", "synopsis": "后续发展", "characters": {}, "threads": []} for k in keys]
                return json.dumps({"records": records}, ensure_ascii=False)
            if "【阶段 一致性审核】" in p:
                return json.dumps({"passed": True, "issues": [], "summary": "审核通过", "synopsis": "剧情推进", "characters": {}, "threads": []}, ensure_ascii=False)
            if "【阶段 更新记忆】" in p:
                return json.dumps({"summary": "剧情更新", "synopsis": "后续发展", "characters": {}, "threads": []}, ensure_ascii=False)
            m = re.search(r"第0*(\d+)章", p)
            num = int(m.group(1)) if m else 6
            return f"# 第{num:03d}章 剧情正文\n\n" + "主角紧扣最新大纲设定推进主线情节，突破境界。" * 40

        progress_msgs = []
        ok, msg, last_f = story_memory.write_chapters(
            root=self.book_path,
            count=5,
            user_prompt="紧扣设定集推进",
            preferences=prefs,
            generate=fake_llm,
            progress=lambda m: progress_msgs.append(m)
        )
        self.assertTrue(ok)
        self.assertTrue(any("【大纲前置校验】" in m for m in progress_msgs))
        self.assertTrue(any("【大纲前置完成】" in m for m in progress_msgs))

        outline_file = os.path.join(self.settings_dir, "03_全书主线与首卷分章细纲.md")
        outline_txt = Path(outline_file).read_text(encoding="utf-8")
        self.assertIn("第008章 暗潮迭起", outline_txt)
        self.assertIn("第009章 逆转乾坤", outline_txt)
        self.assertIn("第010章 终极博弈", outline_txt)

        for c in range(6, 11):
            files = list(Path(self.chapters_dir).glob(f"*第*{c}章*"))
            self.assertTrue(len(files) > 0, f"第{c}章正文应当存在")


if __name__ == "__main__":
    unittest.main()
