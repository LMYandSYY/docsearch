# -*- coding: utf-8 -*-
"""语义/打开接口路由测试：全 mock，不真正调 Ollama、不真正打开程序。"""
import unittest
from unittest import mock

import app as app_module


class TestSemanticRoutes(unittest.TestCase):
    def setUp(self):
        app_module.app.config["TESTING"] = True
        self.client = app_module.app.test_client()

    def test_semantic_search_empty_kw(self):
        d = self.client.get("/api/semantic_search").get_json()
        self.assertTrue(d["ok"])
        self.assertEqual(d["results"], [])

    def test_semantic_search_delegates(self):
        with mock.patch.object(app_module.semantic, "search",
                               return_value={"ok": True, "results": [{"path": "a"}]}):
            d = self.client.get("/api/semantic_search?kw=x").get_json()
        self.assertEqual(d["results"][0]["path"], "a")

    def test_semantic_status(self):
        with mock.patch.object(app_module.semantic, "status",
                               return_value={"available": False, "error": "e",
                                             "building": False, "done": 0,
                                             "total": 0, "model": "bge-m3"}):
            d = self.client.get("/api/semantic_status").get_json()
        self.assertFalse(d["available"])

    def test_open_missing_file_404(self):
        r = self.client.post("/api/open", json={"path": "/no/such/file", "mode": "wps"})
        self.assertEqual(r.status_code, 404)

    def test_open_bad_mode_400(self):
        r = self.client.post("/api/open", json={"path": __file__, "mode": "bad"})
        self.assertEqual(r.status_code, 400)

    def test_open_wps_delegates(self):
        with mock.patch("opener.open_with_wps",
                        return_value={"ok": True, "method": "default"}) as m:
            r = self.client.post("/api/open", json={"path": __file__, "mode": "wps"})
        self.assertTrue(r.get_json()["ok"])
        m.assert_called_once()

    def test_open_folder_delegates(self):
        with mock.patch("opener.open_folder", return_value={"ok": True}) as m:
            r = self.client.post("/api/open", json={"path": __file__, "mode": "folder"})
        self.assertTrue(r.get_json()["ok"])
        m.assert_called_once()


if __name__ == "__main__":
    unittest.main()
