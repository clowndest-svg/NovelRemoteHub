# -*- coding: utf-8 -*-
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
import unittest
import tempfile
import shutil
import json
from unittest.mock import patch, MagicMock
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
            "deepseek_default_model": "deepseek-flash"
        }, self.test_dir)

        cfg2 = deepseek_client.get_deepseek_config(self.test_dir)
        self.assertTrue(cfg2["enabled"])
        self.assertEqual(cfg2["api_key"], "sk-test-key-123456")

        disc2 = deepseek_client.discover(self.test_dir)
        self.assertTrue(disc2["available"])
        # 严格包含官方 2 款主力模型
        self.assertEqual(len(disc2["models"]), 2)
        model_ids = [m["id"] for m in disc2["models"]]
        self.assertEqual(model_ids, ["deepseek-flash", "deepseek-v4-pro"])

    def test_model_normalization(self):
        self.assertEqual(deepseek_client.normalize_model("deepseek-flash"), "deepseek-flash")
        self.assertEqual(deepseek_client.normalize_model("deepseek-v4-pro"), "deepseek-v4-pro")
        self.assertEqual(deepseek_client.normalize_model("deepseek-v4-flash"), "deepseek-flash")
        self.assertEqual(deepseek_client.normalize_model("deepseek-chat"), "deepseek-flash")
        self.assertEqual(deepseek_client.normalize_model("deepseek-reasoner"), "deepseek-v4-pro")
        self.assertEqual(deepseek_client.normalize_model(None), "deepseek-flash")

    def test_test_api_key_empty(self):
        ok, msg = deepseek_client.test_api_key("")
        self.assertFalse(ok)
        self.assertIn("不能为空", msg)

    @patch("urllib.request.urlopen")
    def test_test_api_key_balance_success(self, mock_urlopen):
        # 模拟官方 GET /user/balance 成功返回余额
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "is_available": True,
            "balance_infos": [
                {
                    "currency": "CNY",
                    "total_balance": "66.88",
                    "granted_balance": "10.00",
                    "topped_up_balance": "56.88"
                }
            ]
        }).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        ok, msg = deepseek_client.test_api_key("sk-valid-key")
        self.assertTrue(ok)
        self.assertIn("66.88", msg)
        self.assertIn("正常可用", msg)


if __name__ == "__main__":
    unittest.main()
