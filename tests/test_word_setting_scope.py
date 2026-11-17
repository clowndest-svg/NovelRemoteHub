# -*- coding: utf-8 -*-
"""全局「单章默认目标字数」设置作用范围回归：
百万长篇两条续写链路（每日自动 / 手动操控台）都必须尊重该设置，
短故事与中短篇保持各自创作 SOP 固定篇幅。
"""
import os
import shutil
import tempfile
import unittest
import uuid
import json
from pathlib import Path
from unittest.mock import patch

import auto_writer
import cloud_hub
import local_bridge
import novel_manager


class WordSettingScopeTest(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_wordscope_")
        self.books_dir = os.path.join(self.test_dir, "books")
        os.makedirs(self.books_dir, exist_ok=True)
        self._old_cloud = (cloud_hub.BOOKS_DIR, cloud_hub.DATA_DIR)
        self._old_bridge_root = local_bridge.BOOKS_ROOT
        cloud_hub.BOOKS_DIR = self.books_dir
        cloud_hub.DATA_DIR = self.test_dir
        local_bridge.BOOKS_ROOT = self.books_dir
        cloud_hub.TASKS.clear()
        catalog = {'engines': [{'id': 'antigravity', 'available': True, 'models': [
            {'id': 'test', 'default_effort': 'high', 'efforts': [{'id': 'high'}]}]}]}
        Path(self.test_dir, 'agents.json').write_text(json.dumps(catalog), encoding='utf-8')

    def tearDown(self):
        cloud_hub.BOOKS_DIR, cloud_hub.DATA_DIR = self._old_cloud
        local_bridge.BOOKS_ROOT = self._old_bridge_root
        cloud_hub.TASKS.clear()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def make_book(self, novel_type):
        book_id = f"篇幅测试_{uuid.uuid4().hex[:6]}"
        book_path = os.path.join(self.books_dir, book_id)
        os.makedirs(os.path.join(book_path, "00_设定集"), exist_ok=True)
        chapters = os.path.join(book_path, "01_正文")
        os.makedirs(chapters, exist_ok=True)
        Path(chapters, "第001章 起点.md").write_text("第001章 起点\n\n正文。", encoding="utf-8")
        novel_manager.save_book_meta(book_id, {"title": book_id, "novel_type": novel_type}, self.books_dir, rename=False)
        return book_id

    def run_bridge_task(self, novel_type, prefs=None, extra_words=None):
        """真实驱动 process_single_task，仅在引擎执行处打桩，捕获下发的字数快照"""
        book_id = self.make_book(novel_type)
        extra = {"workflow": "serial", "chapter_count": 2}
        if extra_words:
            extra["default_chapter_words"] = extra_words
        task = {"id": "t_scope", "book_id": book_id, "action": "creative_instruction",
                "engine_mode": "antigravity_pro", "prompt": "续写", "extra_data": extra,
                "preferences": dict(prefs or {})}
        captured = {}
        reports = []

        def fake_exec(book_id_, count, prompt, engine, model, effort, progress,
                      sessions=None, task_id=None, auto_ensure_outline=None, preferences_snapshot=None):
            captured.update(preferences_snapshot or {})
            return True, "ok"

        with patch.object(local_bridge, "report_task", side_effect=lambda payload: reports.append(payload)), \
             patch.object(local_bridge, "wait_for_cloud_sync"), \
             patch.object(local_bridge, "execute_serial_chapters", side_effect=fake_exec):
            local_bridge.process_single_task(task)
        self.assertTrue(captured, "serial 分支必须真实执行到引擎下发点")
        self.assertEqual(reports[-1]["status"], "completed")
        return captured.get("default_chapter_words")

    def run_auto_dispatch(self, novel_type, settings_words=None):
        """真实驱动 run_pipeline，捕获派发参数"""
        book_id = self.make_book(novel_type)
        settings = {"auto_write_enabled": True, "auto_write_chapters": 2}
        if settings_words:
            settings["default_chapter_words"] = settings_words
        novel_manager.save_user_settings(settings, self.test_dir)
        captured = []

        def dispatch(**kw):
            captured.append(kw)
            return True, {"task_id": "t_auto"}

        mgr = auto_writer.AutoWriterManager(self.test_dir, self.books_dir)
        with patch.object(auto_writer, "evaluate_engine_ladder", return_value=("antigravity", {})):
            ok, _, status = mgr.run_pipeline(force=True, dispatch_task_func=dispatch)
        self.assertTrue(ok)
        self.assertEqual(len(captured), 1)
        return captured[0]

    # ---- 执行端入口（手动操控台与自动任务共用的领取链路） ----
    def test_bridge_million_long_honors_global_setting(self):
        self.assertEqual(self.run_bridge_task("million_long", {"default_chapter_words": "6000-8000"}), "6000-8000")

    def test_bridge_million_long_default_when_unset(self):
        self.assertEqual(self.run_bridge_task("million_long"), "4000-5000")

    def test_bridge_short_story_keeps_sop(self):
        self.assertEqual(self.run_bridge_task("short_story", {"default_chapter_words": "6000-8000"}), "2000-3000")

    def test_bridge_mid_short_keeps_sop(self):
        self.assertEqual(self.run_bridge_task("mid_short", {"default_chapter_words": "6000-8000"}), "2500-3500")

    def test_bridge_task_extra_overrides_global(self):
        self.assertEqual(self.run_bridge_task("million_long", {"default_chapter_words": "6000-8000"}, "5000-6000"), "5000-6000")

    # ---- 每日自动续写派发 ----
    def test_auto_million_long_honors_global_setting(self):
        kw = self.run_auto_dispatch("million_long", "6000-8000")
        self.assertEqual(kw["extra"]["default_chapter_words"], "6000-8000")
        self.assertIn("6000-8000字", kw["prompt"])

    def test_auto_million_long_default_when_unset(self):
        kw = self.run_auto_dispatch("million_long")
        self.assertEqual(kw["extra"]["default_chapter_words"], "4000-5000")
        self.assertIn("4000-5000字", kw["prompt"])

    def test_auto_short_story_keeps_sop(self):
        kw = self.run_auto_dispatch("short_story", "6000-8000")
        self.assertEqual(kw["extra"]["default_chapter_words"], "2000-3000")
        self.assertIn("2000-3000字", kw["prompt"])

    def test_auto_mid_short_keeps_sop(self):
        kw = self.run_auto_dispatch("mid_short", "6000-8000")
        self.assertEqual(kw["extra"]["default_chapter_words"], "2500-3500")
        self.assertIn("2500-3500字", kw["prompt"])

    # ---- 全链路：自动流水线 -> 云端建任务(挂偏好快照) -> 执行端领取 ----
    def test_full_join_global_setting_reaches_executor(self):
        book_id = self.make_book("million_long")
        novel_manager.save_user_settings(
            {"auto_write_enabled": True, "auto_write_chapters": 2, "default_chapter_words": "6000-8000"},
            self.test_dir)
        mgr = auto_writer.AutoWriterManager(self.test_dir, self.books_dir)
        with patch.object(auto_writer, "evaluate_engine_ladder", return_value=("antigravity", {})):
            ok, _, status = mgr.run_pipeline(force=True, dispatch_task_func=cloud_hub.dispatch_auto_task)
        self.assertTrue(ok)
        tid = status["novel_reports"][book_id]["result"]["task_id"]
        task = cloud_hub.TASKS[tid]
        self.assertEqual(task["preferences"]["default_chapter_words"], "6000-8000")
        self.assertEqual(task["extra_data"]["default_chapter_words"], "6000-8000")

        captured = {}

        def fake_exec(book_id_, count, prompt, engine, model, effort, progress,
                      sessions=None, task_id=None, auto_ensure_outline=None, preferences_snapshot=None):
            captured.update(preferences_snapshot or {})
            return True, "ok"

        with patch.object(local_bridge, "report_task"), \
             patch.object(local_bridge, "wait_for_cloud_sync"), \
             patch.object(local_bridge, "execute_serial_chapters", side_effect=fake_exec):
            local_bridge.process_single_task(task)
        self.assertEqual(captured.get("default_chapter_words"), "6000-8000")


if __name__ == "__main__":
    unittest.main()