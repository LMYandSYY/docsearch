# -*- coding: utf-8 -*-
"""opener.py 单测：全 mock，不真正打开程序。"""
import helpers  # noqa: F401
import os
import unittest
from unittest import mock

import opener


class TestOpenFolder(unittest.TestCase):
    @mock.patch("opener.subprocess.run")
    @mock.patch("opener.platform.system", return_value="Darwin")
    def test_mac(self, _sys, run):
        r = opener.open_folder("/a/b.txt")
        self.assertTrue(r["ok"])
        run.assert_called_once_with(["open", "-R", "/a/b.txt"], timeout=10)

    @mock.patch("opener.subprocess.run")
    @mock.patch("opener.platform.system", return_value="Windows")
    def test_win(self, _sys, run):
        r = opener.open_folder(r"C:\a\b.txt")
        self.assertTrue(r["ok"])
        run.assert_called_once_with(["explorer", "/select,", r"C:\a\b.txt"], timeout=10)

    @mock.patch("opener.subprocess.run", side_effect=Exception("boom"))
    @mock.patch("opener.platform.system", return_value="Darwin")
    def test_fail(self, _sys, run):
        r = opener.open_folder("/a/b.txt")
        self.assertFalse(r["ok"])
        self.assertIn("boom", r["error"])


class TestOpenWithWps(unittest.TestCase):
    @mock.patch("opener.subprocess.run")
    @mock.patch("opener.platform.system", return_value="Darwin")
    @mock.patch("opener.find_wps", return_value="wpsoffice")
    def test_mac_wps(self, _fw, _sys, run):
        r = opener.open_with_wps("/a/b.docx")
        self.assertEqual(r, {"ok": True, "method": "wps"})
        run.assert_called_once_with(["open", "-a", "wpsoffice", "/a/b.docx"], timeout=10)

    @mock.patch("opener.subprocess.run")
    @mock.patch("opener.platform.system", return_value="Darwin")
    @mock.patch("opener.find_wps", return_value=None)
    def test_mac_fallback(self, _fw, _sys, run):
        r = opener.open_with_wps("/a/b.docx")
        self.assertEqual(r, {"ok": True, "method": "default"})
        run.assert_called_once_with(["open", "/a/b.docx"], timeout=10)

    @mock.patch("opener.subprocess.run")
    @mock.patch("opener.platform.system", return_value="Windows")
    @mock.patch("opener._find_wps_win", return_value=None)
    def test_win_fallback(self, _fw, _sys, run):
        # mock startfile 为 subprocess.run 的调用
        r = opener.open_with_wps(r"C:\a\b.xlsx")
        self.assertEqual(r, {"ok": True, "method": "default"})
        # 验证调用了 Windows 默认打开方式
        self.assertEqual(run.call_count, 1)
        # 检查命令中包含 start（模拟 startfile 的回退行为）
        self.assertIn("start", run.call_args[0][0])

    @mock.patch("opener.subprocess.run")
    @mock.patch("opener.platform.system", return_value="Windows")
    @mock.patch("opener._find_wps_win", return_value=r"C:\WPS\office6")
    @mock.patch("opener.os.path.isfile", return_value=True)
    def test_win_wps_by_ext(self, _isf, _fw, _sys, run):
        r = opener.open_with_wps(r"C:\a\b.xlsx")  # xlsx 应选择 et.exe
        self.assertEqual(r, {"ok": True, "method": "wps"})
        run.assert_called_once_with([r"C:\WPS\office6\et.exe", r"C:\a\b.xlsx"], timeout=10)


class TestFindWps(unittest.TestCase):
    @mock.patch("opener.os.path.isdir", side_effect=lambda p: p == "/Applications/wpsoffice.app")
    @mock.patch("opener.platform.system", return_value="Darwin")
    def test_mac_found(self, _sys, _isd):
        self.assertEqual(opener.find_wps(), "wpsoffice")

    @mock.patch("opener.os.path.isdir", return_value=False)
    @mock.patch("opener.platform.system", return_value="Darwin")
    def test_mac_not_found(self, _sys, _isd):
        self.assertIsNone(opener.find_wps())


if __name__ == "__main__":
    unittest.main()
