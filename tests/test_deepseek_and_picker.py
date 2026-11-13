# -*- coding: utf-8 -*-
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import unittest
import tempfile
import shutil
import json
import deepseek_client
import novel_manager


class DeepSeekAndPickerTest(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="test_ds_")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_deepseek_config_and_discovery(self):
        # 初始未启用
        cfg = deepseek_client.get_deepseek_config(self.test_dir)
        self.assertFalse(cfg["enabled"])
        
        disc = deepseek_client.discover(self.test_dir)
        self.assertFalse(disc["available"])

        # 保存开启并提供测试 Key
        novel_manager.save_user_settings({
            "deepseek_enabled": True,
            "deepseek_api_key": "sk-test-key-123456",
            "deepseek_base_url": "https://api.deepseek.com",
            "deepseek_default_model": "deepseek-chat"
        }, self.test_dir)

        cfg2 = deepseek_client.get_deepseek_config(self.test_dir)
        self.assertTrue(cfg2["enabled"])
        self.assertEqual(cfg2["api_key"], "sk-test-key-123456")

        disc2 = deepseek_client.discover(self.test_dir)
        self.assertTrue(disc2["available"])
        self.assertEqual(len(disc2["models"]), 2)
        model_ids = [m["id"] for m in disc2["models"]]
        self.assertIn("deepseek-chat", model_ids)
        self.assertIn("deepseek-reasoner", model_ids)

    def test_test_api_key_empty(self):
        ok, msg = deepseek_client.test_api_key("")
        self.assertFalse(ok)
        self.assertIn("不能为空", msg)


if __name__ == "__main__":
    unittest.main()
