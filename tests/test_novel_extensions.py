# -*- coding: utf-8 -*-
import os
import shutil
import tempfile
import unittest
import base64
import novel_manager
import cloud_hub


class NovelExtensionsTest(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_novel_ext_")
        self.books_dir = os.path.join(self.test_dir, "books")
        os.makedirs(self.books_dir, exist_ok=True)
        # 创建一本测试小说
        self.book_id = "测试修仙记"
        self.book_path = os.path.join(self.books_dir, self.book_id)
        os.makedirs(os.path.join(self.book_path, "00_设定集"), exist_ok=True)
        os.makedirs(os.path.join(self.book_path, "01_正文"), exist_ok=True)
        with open(os.path.join(self.book_path, "01_正文", "第001章 觉醒.md"), "w", encoding="utf-8") as f:
            f.write("第1章 觉醒\n\n天地初开，量子自旋。")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_book_meta_and_rename(self):
        meta = {
            "title": "测试修仙记·改",
            "genre": "赛博修仙",
            "description": "一本测试专用的精彩小说",
            "tags": ["硬核", "无敌"]
        }
        res = novel_manager.save_book_meta(self.book_id, meta, self.books_dir)
        self.assertTrue(res["ok"])
        self.assertEqual(res["book_id"], "测试修仙记·改")

        # 检查重命名后的目录是否存在
        new_path = os.path.join(self.books_dir, "测试修仙记·改")
        self.assertTrue(os.path.exists(new_path))

        # 读取元数据
        loaded = novel_manager.read_book_meta(new_path)
        self.assertEqual(loaded["title"], "测试修仙记·改")
        self.assertEqual(loaded["genre"], "赛博修仙")
        self.assertEqual(loaded["description"], "一本测试专用的精彩小说")

    def test_cover_save_and_svg_generation(self):
        # 1. 测试 SVG 生成
        svg = cloud_hub.generate_svg_cover("大秦科技修仙：我肉身手撕二向箔", "宇宙科幻")
        self.assertIn("<svg", svg)
        self.assertIn("NOVEL REMOTE HUB", svg)
        self.assertIn("宇宙科幻", svg)

        # 2. 测试真实封面 Base64 存储
        # 构造一个 1x1 的有效 PNG base64
        sample_png_b64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
        saved_path = novel_manager.save_book_cover(self.book_id, sample_png_b64, self.books_dir)
        self.assertTrue(os.path.exists(saved_path))
        self.assertTrue(saved_path.endswith("cover.png"))

    def test_safe_delete_and_restore(self):
        # 校验软删除移入 .trash/
        del_res = novel_manager.delete_book_safe(self.book_id, self.books_dir)
        self.assertTrue(del_res["ok"])
        self.assertFalse(os.path.exists(self.book_path))
        self.assertTrue(os.path.exists(del_res["trash_path"]))
        self.assertIn(".trash", del_res["trash_path"])

    def test_user_settings_read_write(self):
        settings = novel_manager.get_user_settings(self.test_dir)
        self.assertIn("default_engine", settings)
        self.assertTrue(settings["auto_check_ledger"])

        # 修改并保存
        new_settings = novel_manager.save_user_settings({
            "default_engine": "codex",
            "default_chapter_words": "5000-6000"
        }, self.test_dir)
        self.assertEqual(new_settings["default_engine"], "codex")
        self.assertEqual(new_settings["default_chapter_words"], "5000-6000")

        # 重新读取确认持久化成功
        reloaded = novel_manager.get_user_settings(self.test_dir)
        self.assertEqual(reloaded["default_engine"], "codex")


if __name__ == "__main__":
    unittest.main()
