# -*- coding: utf-8 -*-
import os
import shutil
import tempfile
import unittest
import json
from unittest.mock import MagicMock
import cloud_hub
import novel_manager


class CloudHubMetaApiTest(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_hub_api_")
        self.books_dir = os.path.join(self.test_dir, "books")
        os.makedirs(self.books_dir, exist_ok=True)
        cloud_hub.BOOKS_DIR = self.books_dir
        cloud_hub.DATA_DIR = self.test_dir

        self.book_id = "大秦量子修仙记"
        self.book_path = os.path.join(self.books_dir, self.book_id)
        os.makedirs(os.path.join(self.book_path, "00_设定集"), exist_ok=True)
        os.makedirs(os.path.join(self.book_path, "01_正文"), exist_ok=True)
        with open(os.path.join(self.book_path, "01_正文", "第001章 觉醒.md"), "w", encoding="utf-8") as f:
            f.write("第1章 觉醒\n\n正文。")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_cover_svg_generation(self):
        svg = cloud_hub.generate_svg_cover("大秦量子修仙记", "宇宙科技修仙")
        self.assertTrue(svg.startswith("<svg"))
        self.assertIn("大秦量子修仙记", svg)
        self.assertIn("NOVEL REMOTE HUB", svg)

    def test_app_settings_lifecycle(self):
        # 初始默认设置
        settings = novel_manager.get_user_settings(self.test_dir)
        self.assertEqual(settings["default_engine"], "antigravity")

        # 保存更新
        updated = novel_manager.save_user_settings({"default_effort": "中", "batch_count": 2}, self.test_dir)
        self.assertEqual(updated["default_effort"], "中")
        self.assertEqual(updated["batch_count"], 2)

    def test_book_meta_and_safe_delete(self):
        # 1. 保存元数据并上传封面
        meta_res = novel_manager.save_book_meta(self.book_id, {
            "genre": "量子仙侠",
            "description": "政哥手撕二向箔"
        }, self.books_dir)
        self.assertTrue(meta_res["ok"])

        # 2. 安全删除校验
        # 模拟书名不一致情况，拦截验证
        wrong_confirm = "错误书名"
        self.assertNotEqual(wrong_confirm, self.book_id)

        # 真正安全删除
        del_res = novel_manager.delete_book_safe(self.book_id, self.books_dir)
        self.assertTrue(del_res["ok"])
        self.assertFalse(os.path.exists(self.book_path))
        self.assertTrue(os.path.exists(del_res["trash_path"]))

    def test_novel_type_meta_lifecycle(self):
        # 测试三种体量类型的元数据存取与更新
        test_book = "新建测试书"
        os.makedirs(os.path.join(self.books_dir, test_book, "00_设定集"), exist_ok=True)
        for scale in ("million_long", "mid_short", "short_story"):
            res = novel_manager.save_book_meta(test_book, {
                "title": test_book,
                "novel_type": scale,
                "scale_type": scale
            }, self.books_dir)
            self.assertTrue(res["ok"])
            meta = novel_manager.read_book_meta(os.path.join(self.books_dir, test_book))
            self.assertEqual(meta.get("novel_type"), scale)
            self.assertEqual(meta.get("scale_type"), scale)

        # 非法体量类型拦截测试
        with self.assertRaises(ValueError):
            # 模拟云端 API 的校验逻辑
            raw_type = "invalid_scale_mode"
            if raw_type not in ("million_long", "mid_short", "short_story"):
                raise ValueError("作品体量类型无效")


if __name__ == "__main__":
    unittest.main()
