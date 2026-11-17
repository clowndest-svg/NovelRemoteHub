import tempfile
import unittest
from pathlib import Path
from storage import atomic_write
import novel_manager

class ChapterIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir='.test-data')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.chapter = self.root / '01_正文/第一卷/第001章_开始.md'
        atomic_write(self.chapter, '# 第001章 开始\n\n正文')

    def test_identity_survives_edit_and_rename(self):
        first = novel_manager.chapter_identities(self.root)[str(self.chapter)]
        renamed = self.chapter.with_name('第001章_新的标题.md')
        self.chapter.rename(renamed)
        atomic_write(renamed, '# 第001章 新的标题\n\n修订后的正文')
        second = novel_manager.chapter_identities(self.root)[str(renamed)]
        self.assertEqual(first['chapter_id'], second['chapter_id'])
        self.assertNotEqual(first['chapter_revision'], second['chapter_revision'])

    def test_duplicate_numbers_have_distinct_identities(self):
        second = self.root / '01_正文/第二卷/第001章_开始.md'
        atomic_write(second, '# 第001章 开始\n\n另一卷正文')
        records = novel_manager.chapter_identities(self.root)
        self.assertEqual(len({item['chapter_id'] for item in records.values()}), 2)

    def test_removed_then_new_content_gets_new_identity(self):
        first = novel_manager.chapter_identities(self.root)[str(self.chapter)]
        self.chapter.unlink()
        novel_manager.chapter_identities(self.root)
        atomic_write(self.chapter, '# 第001章 重建\n\n重新创建的章节')
        second = novel_manager.chapter_identities(self.root)[str(self.chapter)]
        self.assertNotEqual(first['chapter_id'], second['chapter_id'])
