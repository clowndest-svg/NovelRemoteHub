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

    def test_fuzzy_book_path_matching(self):
        # 验证平台书名截断时（如七猫字数限制）能自动模糊匹配
        path, title = novel_manager.get_book_path("测试", str(self.test_dir))
        self.assertEqual(title, "测试作品")

    def test_novel_creation_metadata(self):
        # 验证新书建档元数据提取（书名、短书名、分类、男女频、标签、主角、100~500字简介）
        meta = novel_manager.get_novel_creation_metadata("测试作品", str(self.test_dir))
        self.assertTrue(meta["ok"])
        self.assertEqual(meta["title"], "测试作品")
        self.assertTrue(len(meta["short_title"]) <= 15)
        self.assertEqual(meta["target_reader"], "男生")
        self.assertIn("玄幻脑洞", meta["tags"])
        self.assertIn("顾见微", meta["roles"])
        self.assertTrue(100 <= len(meta["synopsis_condensed"]) <= 500)

    def test_userscript_file_exists_and_valid(self):
        js_path = Path("fanqie_publisher.user.js")
        self.assertTrue(js_path.exists())
        text = js_path.read_text(encoding="utf-8")
        self.assertIn("// ==UserScript==", text)
        self.assertIn("@match        *://fanqienovel.com/*", text)
        self.assertIn("@match        *://zuozhe.qimao.com/*", text)
        self.assertIn("@match        *://write.shuqi.com/*", text)
        self.assertIn("@grant        GM_deleteValue", text)
        self.assertTrue("2.4.2" in text or "2.4.3" in text)
        self.assertIn("verifyEditorContentReady", text)
        self.assertIn("autoDismissTourOrGuide", text)
        self.assertIn("startBatchWatchdog", text)
        self.assertIn("nrh-resume-batch-btn", text)

        # 语法严密性自动化校验
        import subprocess
        res = subprocess.run(["node", "-c", str(js_path)], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, f"JS语法错误: {res.stderr}")
        self.assertIn("nrh_platform_book_bindings", text)
        self.assertIn("detectPageBookInfo", text)
        self.assertIn("checkBookAlignment", text)
        self.assertIn("guardBookAction", text)
        self.assertIn("nrh-book-alignment-status", text)
        self.assertIn("nrh-btn-quick-align", text)
        self.assertIn("nrh_multi_platform_batch", text)
        self.assertIn("nrh-book-select", text)
        self.assertIn("loadBooksCatalog", text)
        self.assertIn("fanqie_books", text)
        self.assertIn("nrh-btn-refresh-books", text)
        self.assertIn("expandAuthorNoteIfCollapsed", text)
        self.assertIn("nrh-dock-tab", text)
        self.assertIn("智笔云枢", text)
        self.assertIn("qimao", text)
        self.assertIn("shuqi", text)
        self.assertIn("getCleanPageText", text)
        self.assertIn("fillBookCreationInfo", text)
        self.assertIn("detectPagePublishedMaxChapter", text)
        self.assertIn(".chapter-name textarea", text)
        self.assertIn("更新草稿", text)
        self.assertIn(".ql-editor", text)

    def test_dynamic_multi_book_metadata(self):
        # 验证对第二部全新题材小说的动态建档元数据提取
        custom_book = self.test_dir / "山河救亡传"
        custom_book.mkdir(exist_ok=True)
        (custom_book / "00_设定集").mkdir(exist_ok=True)

        char_content = """# 人物设定
## 一、 主角档案：陈长生
### 1. 人物身世
战地医生穿越救亡。

## 二、 我方阵营
- **李司令（铁血军长）**：负责敌后根据地。
"""
        atomic_write(custom_book / "00_设定集" / "02_核心人物与关系阵营.md", char_content)

        book_meta = {
            "title": "山河救亡传",
            "genre": "历史纪实 / 抗战救亡",
            "description": "抗战救亡纪实小说，年轻战地军医陈长生穿越至烽火硝烟年代，以医学奇迹与战略远见力挽狂澜！保家卫国，浴血长空，重铸华夏不朽丰碑！山河犹在，国泰民安！",
            "novel_type": "mid_short"
        }
        atomic_write(custom_book / "00_设定集" / "book.json", json.dumps(book_meta, ensure_ascii=False))

        meta = novel_manager.get_novel_creation_metadata("山河救亡传", str(self.test_dir))
        self.assertTrue(meta["ok"])
        self.assertEqual(meta["title"], "山河救亡传")
        self.assertEqual(meta["roles"], ["陈长生", "李司令"])
        self.assertEqual(meta["category"], "历史纪实")
        self.assertIn("救亡图存", meta["tags"])
        self.assertTrue(100 <= len(meta["synopsis_condensed"]) <= 500)
        self.assertIn("陈长生", meta["synopsis_condensed"])

    def test_fuzzy_chapter_matching_in_publish_payload(self):
        # 验证当传入 "第001章.md" 时，自动模糊匹配实际文件 "第001章_星际开篇.md"
        book_dir = self.test_dir / "模糊匹配测试书"
        vol_dir = book_dir / "01_正文" / "第一卷"
        vol_dir.mkdir(parents=True, exist_ok=True)
        chap_file = vol_dir / "第001章_星际开篇：降临新世界.md"
        chap_file.write_text("第001章 星际开篇：降临新世界\n\n穿越到了浩瀚星河之中。\n", encoding="utf-8")

        # 传入短名 "第001章.md"
        payload = novel_manager.get_fanqie_publish_payload("模糊匹配测试书", "第001章.md", str(self.test_dir))
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["chapter_number"], 1)
        self.assertEqual(payload["filename"], "第001章_星际开篇：降临新世界.md")
        self.assertIn("穿越到了浩瀚星河之中", payload["content"])

        # read_chapter 也支持模糊匹配
        text = novel_manager.read_chapter("模糊匹配测试书", "第001章.md", str(self.test_dir))
        self.assertIn("穿越到了浩瀚星河之中", text)

    def test_multi_book_isolation_in_userscript(self):
        # 验证前端脚本中包含完善的多书区分、安全拦截与绑定防护体系
        js_text = Path("fanqie_publisher.user.js").read_text(encoding="utf-8")
        self.assertIn("detectPageBookInfo", js_text)
        self.assertIn("guardBookAction", js_text)
        self.assertIn("checkBookAlignment", js_text)
        self.assertIn("nrh_platform_book_bindings_v2", js_text)
        self.assertIn("严禁跨书串写安全拦截", js_text)
        self.assertIn("批量流水线紧急熔断", js_text)

    def test_publish_payload_withholds_stale_extras_but_serves_body(self):
        # 正文更新后互动文案未复核：发布包仍 ok 且含正文，但扣下待复核文案并返回标记（不阻断批量发布）
        meta_path = self.book_dir / "02_插图" / "第001章" / "meta.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["note_source_sha256"] = "outdated-body-fingerprint"
        meta.pop("reviewed_revision", None)
        atomic_write(meta_path, json.dumps(meta, ensure_ascii=False))
        payload = novel_manager.get_fanqie_publish_payload("测试作品", "第001章_宏观破壁.md", str(self.test_dir))
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["notes_stale"])
        self.assertEqual(payload["author_note"], "")
        self.assertEqual(payload["comments"], [])
        self.assertIn("狄拉克海", payload["content"])

    def test_batch_stale_extras_non_blocking_and_real_error_surfaced(self):
        js = Path("fanqie_publisher.user.js").read_text(encoding="utf-8")
        # 批量与单章都对 notes_stale 做非阻断提醒（不再误报中枢异常）
        self.assertIn("pkg.notes_stale || pkg.illustrations_stale", js)
        self.assertIn("本次仅录入正文、扣下待复核素材", js)
        # apiGet 透出服务端真实错误文本
        self.assertIn("if (body && body.error) detail = body.error;", js)
        # 批量取包失败时报真实原因而非笼统中枢异常
        self.assertIn("获取第 ${state.currentNum} 章数据失败：${reason}", js)

if __name__ == "__main__":
    unittest.main()
