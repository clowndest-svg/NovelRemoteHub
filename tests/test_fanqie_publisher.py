# -*- coding: utf-8 -*-
import unittest
import os
import json
from pathlib import Path
import novel_manager
from storage import atomic_write

class TestFanqiePublisher(unittest.TestCase):
    def setUp(self):
        self.test_dir = Path(".test-data/fanqie_test")
        self.test_dir.mkdir(parents=True, exist_ok=True)
        self.book_dir = self.test_dir / "测试作品"
        self.book_dir.mkdir(exist_ok=True)
        (self.book_dir / "01_正文" / "第一卷").mkdir(parents=True, exist_ok=True)
        (self.book_dir / "02_插图" / "第001章").mkdir(parents=True, exist_ok=True)
        (self.book_dir / "00_设定集").mkdir(parents=True, exist_ok=True)

        # 写入测试章节
        content = """### 第001章 宏观破壁

按步就班既然如此，始皇帝在次凝视狄拉克海真空涨落。
迫不急待地测试端粒酶与二向箔。
"""
        atomic_write(self.book_dir / "01_正文" / "第一卷" / "第001章_宏观破壁.md", content)

        # 写入测试元数据
        extras_meta = {
            "chapter_filename": "第001章_宏观破壁.md",
            "author_note": "本章作者有话说：关于狄拉克海的奥秘。",
            "comments": ["始皇帝硬抗二向箔到底有多强？", "下一章政哥会登月吗？"]
        }
        atomic_write(self.book_dir / "02_插图" / "第001章" / "meta.json", json.dumps(extras_meta, ensure_ascii=False))

    def tearDown(self):
        import shutil
        if self.test_dir.exists():
            shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_auto_fix_in_clean_for_tomato(self):
        raw = "### 第001章 宏观破壁\n\n按步就班即然如此，在次展开做战。"
        cleaned = novel_manager.clean_for_tomato(raw, "第001章_宏观破壁.md", auto_fix_typos=True)
        self.assertEqual(cleaned["title"], "第001章 宏观破壁")
        self.assertIn("按部就班既然如此", cleaned["content"])
        self.assertIn("再次展开作战", cleaned["content"])
        self.assertTrue(cleaned["replaced_count"] >= 3)

    def test_get_fanqie_publish_payload(self):
        payload = novel_manager.get_fanqie_publish_payload("测试作品", "第001章_宏观破壁.md", str(self.test_dir))
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["chapter_number"], 1)
        self.assertEqual(payload["chapter_title"], "宏观破壁")
        self.assertEqual(payload["full_title"], "第001章 宏观破壁")
        self.assertIn("按部就班", payload["content"])
        self.assertIn("狄拉克海", payload["content"])
        self.assertIn("关于狄拉克海的奥秘", payload["author_note"])
        self.assertIn("【本章互动话题】", payload["author_note"])
        self.assertEqual(len(payload["comments"]), 2)

    def test_whitelist_not_penalized(self):
        # 验证白名单中的词不会导致机审扣分
        raw = "### 第001章 科学修仙\n\n始皇帝肉身硬抗二向箔，狄拉克海泛起涟漪，端粒酶逆转寿命。"
        cleaned = novel_manager.clean_for_tomato(raw, "第001章_科学修仙.md")
        audit = novel_manager.audit_for_tomato(cleaned)
        # 狄拉克海、二向箔、端粒酶均不应被报为高危敏感风控
        danger_words = [iss["word"] for iss in audit["issues"] if iss["level"] == "danger"]
        self.assertNotIn("二向箔", danger_words)
        self.assertNotIn("狄拉克海", danger_words)
        self.assertNotIn("端粒酶", danger_words)

    def test_userscript_file_exists_and_valid(self):
        js_path = Path("fanqie_publisher.user.js")
        self.assertTrue(js_path.exists())
        text = js_path.read_text(encoding="utf-8")
        self.assertIn("// ==UserScript==", text)
        self.assertIn("@match        https://fanqienovel.com/main/writer/*", text)
        self.assertIn("智笔云枢", text)

if __name__ == "__main__":
    unittest.main()
