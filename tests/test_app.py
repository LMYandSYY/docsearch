# -*- coding: utf-8 -*-
import os
import unittest
from unittest import mock

from helpers import make_tmpdir, write_txt, make_docx, make_pdf
import app as appmod


def make_folder(files):
    """files: {name: content}; content 为 list 时按段落生成 docx。"""
    d = make_tmpdir()
    for name, content in files.items():
        p = os.path.join(d, name)
        if name.endswith(".docx"):
            make_docx(p, content if isinstance(content, list) else [content])
        elif name.endswith(".pdf"):
            make_pdf(p, content)
        else:
            write_txt(p, content)
    return d


class TestApp(unittest.TestCase):
    def setUp(self):
        self.client = appmod.app.test_client()

    def test_load_single_folder_compat(self):
        # 旧版单 folder 字段仍兼容
        d = make_folder({"a.txt": "电网"})
        r = self.client.post("/api/load", json={"folder": d, "ocr": False})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.get_json()["ok"])

    def test_load_multiple_folders(self):
        d1 = make_folder({"a.txt": "电网"})
        d2 = make_folder({"b.txt": "开关"})
        r = self.client.post("/api/load", json={"folders": [d1, d2], "ocr": False})
        j = r.get_json()
        self.assertTrue(j["ok"])
        self.assertEqual(j["count"], 2)
        self.assertEqual(len(j["folders"]), 2)

    def test_search_across_folders(self):
        d1 = make_folder({"a.txt": "电网一"})
        d2 = make_folder({"b.txt": "电网二"})
        self.client.post("/api/load", json={"folders": [d1, d2], "ocr": False})
        s = self.client.get("/api/search?kw=电网").get_json()
        self.assertEqual(sorted(x["name"] for x in s), ["a.txt", "b.txt"])

    def test_load_bad_folder_returns_error(self):
        r = self.client.post("/api/load", json={"folders": ["/no/such/dir"], "ocr": False})
        self.assertEqual(r.status_code, 400)
        self.assertFalse(r.get_json()["ok"])

    def test_load_empty_folders(self):
        r = self.client.post("/api/load", json={"folders": [], "ocr": False})
        self.assertEqual(r.status_code, 400)

    def test_search_empty_kw(self):
        d = make_folder({"a.txt": "电网"})
        self.client.post("/api/load", json={"folders": [d], "ocr": False})
        self.assertEqual(self.client.get("/api/search?kw=").get_json(), [])

    def test_preview_txt_highlight(self):
        d = make_folder({"a.txt": "电网开关测试"})
        self.client.post("/api/load", json={"folders": [d], "ocr": False})
        r = self.client.get("/preview?path=" + os.path.join(d, "a.txt") + "&kw=电网")
        self.assertEqual(r.status_code, 200)
        body = r.get_data(as_text=True)
        self.assertIn("<mark>", body)
        self.assertIn("电网", body)

    def test_preview_docx(self):
        d = make_folder({"c.docx": ["电网文档内容", "含开关"]})
        self.client.post("/api/load", json={"folders": [d], "ocr": False})
        r = self.client.get("/preview?path=" + os.path.join(d, "c.docx") + "&kw=电网")
        self.assertEqual(r.status_code, 200)
        self.assertIn("<mark>", r.get_data(as_text=True))

    def test_preview_pdf(self):
        d = make_folder({"x.pdf": "the SWITCH text"})
        self.client.post("/api/load", json={"folders": [d], "ocr": False})
        r = self.client.get("/preview?path=" + os.path.join(d, "x.pdf") + "&kw=SWITCH")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.mimetype, "application/pdf")

    def test_preview_missing_file(self):
        r = self.client.get("/preview?path=/no/such/file")
        self.assertEqual(r.status_code, 404)

    def test_settings_roundtrip(self):
        d = make_folder({"a.txt": "x"})
        self.client.post("/api/load", json={"folders": [d], "ocr": True})
        s = self.client.get("/api/settings").get_json()
        self.assertIn(d, s["folders"])
        self.assertTrue(s["ocr"])

    def test_error_response_is_json(self):
        # 触发 400，响应必须是 JSON（前端按 JSON 解析）
        r = self.client.post("/api/load", json={"folders": ["/no/such/dir"]})
        self.assertEqual(r.headers.get("content-type", "")[:16], "application/json")


class TestPickFolder(unittest.TestCase):
    """跨平台目录选择器逻辑（用 mock，不依赖真弹窗/真平台）。"""

    def _mock_run(self, returncode=0, stdout=""):
        m = mock.Mock()
        m.returncode = returncode
        m.stdout = stdout
        m.stderr = ""
        return m

    def test_macos_cancelled(self):
        with mock.patch.object(appmod.platform, "system", return_value="Darwin"), \
             mock.patch.object(appmod.subprocess, "run", return_value=self._mock_run(1, "")):
            self.assertIsNone(appmod._pick_folder())

    def test_macos_success_strips_trailing_slash(self):
        with mock.patch.object(appmod.platform, "system", return_value="Darwin"), \
             mock.patch.object(appmod.subprocess, "run", return_value=self._mock_run(0, "/Users/x/my dir/\n")):
            self.assertEqual(appmod._pick_folder(), "/Users/x/my dir")

    def test_windows_success_keeps_backslashes(self):
        with mock.patch.object(appmod.platform, "system", return_value="Windows"), \
             mock.patch.object(appmod.subprocess, "run", return_value=self._mock_run(0, "D:\\Docs\\stuff\r\n")):
            self.assertEqual(appmod._pick_folder(), "D:\\Docs\\stuff")

    def test_windows_cancelled(self):
        with mock.patch.object(appmod.platform, "system", return_value="Windows"), \
             mock.patch.object(appmod.subprocess, "run", return_value=self._mock_run(1, "")):
            self.assertIsNone(appmod._pick_folder())

    def test_linux_success(self):
        with mock.patch.object(appmod.platform, "system", return_value="Linux"), \
             mock.patch.object(appmod.subprocess, "run", return_value=self._mock_run(0, "/home/u/docs\n")):
            self.assertEqual(appmod._pick_folder(), "/home/u/docs")

    def test_tool_missing_returns_none(self):
        with mock.patch.object(appmod.platform, "system", return_value="Darwin"), \
             mock.patch.object(appmod.subprocess, "run", side_effect=FileNotFoundError):
            self.assertIsNone(appmod._pick_folder())

    def test_route_success(self):
        client = appmod.app.test_client()
        with mock.patch.object(appmod, "_pick_folder", return_value="/some/path"):
            self.assertEqual(client.get("/api/pick_folder").get_json(),
                             {"ok": True, "path": "/some/path"})

    def test_route_cancelled(self):
        client = appmod.app.test_client()
        with mock.patch.object(appmod, "_pick_folder", return_value=None):
            j = client.get("/api/pick_folder").get_json()
            self.assertFalse(j["ok"])
            self.assertTrue(j["cancelled"])


if __name__ == "__main__":
    unittest.main()
