# -*- coding: utf-8 -*-
"""semantic.py 单测：分段、embedding 调用、索引存储与检索。"""
import helpers  # noqa: F401  触发 sys.path 注入项目根
import io
import json as json_mod
import os
import unittest
from unittest import mock

import semantic


class TestChunkText(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(semantic.chunk_text(""), [])
        self.assertEqual(semantic.chunk_text("  \n  \n"), [])

    def test_short_single_chunk(self):
        self.assertEqual(semantic.chunk_text("你好世界"), ["你好世界"])

    def test_paragraphs_accumulate(self):
        # 3 段各 200 字：累计 602 >= 500 才成块 → 1 块
        paras = ["字" * 200, "词" * 200, "句" * 200]
        chunks = semantic.chunk_text("\n".join(paras))
        self.assertEqual(len(chunks), 1)

    def test_many_paragraphs_split(self):
        # 6 段各 200 字：约每 3 段一块 → 2 块
        paras = ["段%d%s" % (i, "内" * 196) for i in range(6)]
        chunks = semantic.chunk_text("\n".join(paras))
        self.assertEqual(len(chunks), 2)

    def test_long_paragraph_hard_cut(self):
        chunks = semantic.chunk_text("长" * 1100)
        self.assertEqual([len(c) for c in chunks], [500, 500, 100])


class _FakeResp:
    """模拟 urlopen 返回的响应对象。"""

    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return json_mod.dumps(self._payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class TestEmbedTexts(unittest.TestCase):
    def test_ok(self):
        with mock.patch("semantic.urllib.request.urlopen",
                        return_value=_FakeResp({"embeddings": [[0.1, 0.2]]})):
            out = semantic.embed_texts(["abc"])
        self.assertEqual(out, [[0.1, 0.2]])

    def test_batches(self):
        calls = []

        def fake_urlopen(req, timeout=None):
            n = len(json_mod.loads(req.data.decode())["input"])
            calls.append(n)
            return _FakeResp({"embeddings": [[0.0, 1.0]] * n})

        with mock.patch("semantic.urllib.request.urlopen", side_effect=fake_urlopen):
            out = semantic.embed_texts(["t"] * 20)
        self.assertEqual(calls, [16, 4])
        self.assertEqual(len(out), 20)

    def test_failure_returns_none(self):
        with mock.patch("semantic.urllib.request.urlopen",
                        side_effect=ConnectionRefusedError):
            self.assertIsNone(semantic.embed_texts(["t"]))

    def test_empty_input(self):
        self.assertEqual(semantic.embed_texts([]), [])


class TestProbeOllama(unittest.TestCase):
    def test_ok(self):
        with mock.patch("semantic.urllib.request.urlopen",
                        return_value=_FakeResp({"embeddings": [[0.0]]})):
            self.assertIsNone(semantic.probe_ollama())

    def test_model_missing(self):
        err = urllib_error_HTTPError(404, b'{"error":"model \'bge-m3\' not found"}')
        with mock.patch("semantic.urllib.request.urlopen", side_effect=err):
            msg = semantic.probe_ollama()
        self.assertIn("ollama pull bge-m3", msg)

    def test_conn_refused(self):
        with mock.patch("semantic.urllib.request.urlopen",
                        side_effect=ConnectionRefusedError):
            msg = semantic.probe_ollama()
        self.assertIn("无法连接", msg)


def urllib_error_HTTPError(code, body):
    import urllib.error
    return urllib.error.HTTPError("http://x", code, "err", None, io.BytesIO(body))


class TestSemanticIndexStore(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp(prefix="semtest_")
        self.db = os.path.join(self.tmp, "cache.db")
        from semantic import SemanticIndex
        self.si = SemanticIndex(self.db)

    def _corpus(self, mtime=1):
        return {"a.txt": {"text": "内容" * 400, "mtime": mtime, "size": 800}}

    def _rows(self):
        return self.si.conn.execute("SELECT path, chunk_idx FROM chunks").fetchall()

    def test_ensure_writes_chunks(self):
        with mock.patch("semantic.probe_ollama", return_value=None), \
             mock.patch("semantic.embed_texts", return_value=[[0.1, 0.2]] * 4) as me:
            self.si.ensure(self._corpus())
            self.assertTrue(self.si.status()["available"])
            self.assertGreater(len(self._rows()), 0)
            self.assertEqual(me.call_count, 1)
            self.assertFalse(self.si.status()["building"])

    def test_ensure_progress_total(self):
        # ensure 期间/结束后 done 与 total 都应有意义（前端进度条显示 x/y）
        with mock.patch("semantic.probe_ollama", return_value=None), \
             mock.patch("semantic.embed_texts", return_value=[[0.1, 0.2]] * 4):
            self.si.ensure(self._corpus())
        st = self.si.status()
        self.assertEqual(st["done"], 1)
        self.assertEqual(st["total"], 1)

    def test_unchanged_file_skipped(self):
        with mock.patch("semantic.probe_ollama", return_value=None), \
             mock.patch("semantic.embed_texts", return_value=[[0.1, 0.2]] * 4) as me:
            self.si.ensure(self._corpus())
            self.si.ensure(self._corpus())  # mtime/size 未变
            self.assertEqual(me.call_count, 1)  # 不重嵌

    def test_changed_file_rebuilt(self):
        with mock.patch("semantic.probe_ollama", return_value=None), \
             mock.patch("semantic.embed_texts", return_value=[[0.1, 0.2]] * 4) as me:
            self.si.ensure(self._corpus(mtime=1))
            self.si.ensure(self._corpus(mtime=2))
            self.assertEqual(me.call_count, 2)
            # 旧块被删，不留重复
            self.assertEqual(len(self._rows()), len(set(self._rows())))

    def test_removed_file_cleaned(self):
        with mock.patch("semantic.probe_ollama", return_value=None), \
             mock.patch("semantic.embed_texts", return_value=[[0.1, 0.2]] * 4):
            self.si.ensure(self._corpus())
            self.si.ensure({})  # 语料清空
            self.assertEqual(self._rows(), [])

    def test_probe_fail_degrades(self):
        with mock.patch("semantic.probe_ollama", return_value="无法连接 Ollama"):
            self.si.ensure(self._corpus())
        st = self.si.status()
        self.assertFalse(st["available"])
        self.assertIn("Ollama", st["error"])
        self.assertEqual(self._rows(), [])


class TestSemanticIndexSearch(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.mkdtemp(prefix="semtest2_")
        from semantic import SemanticIndex
        self.si = SemanticIndex(os.path.join(self.tmp, "cache.db"))

    def _put(self, path, text, vec):
        import numpy as np
        self.si.conn.execute(
            "INSERT INTO chunks (path, chunk_idx, text, mtime, size, embedding)"
            " VALUES (?,?,?,?,?,?)", (path, 0, text, 1, 1,
            np.asarray(vec, dtype=np.float32).tobytes()))

    def test_ranking_and_threshold(self):
        self._put("f1.txt", "遥控相关", [1.0, 0.0])
        self._put("f2.txt", "无关", [0.0, 1.0])
        self._put("f3.txt", "半相关", [0.7, 0.7])
        self.si._reload()
        self.si._available = True  # 绕过探测，直接测检索
        with mock.patch("semantic.embed_texts", return_value=[[1.0, 0.0]]):
            r = self.si.search("遥控失败")
        self.assertTrue(r["ok"])
        paths = [x["path"] for x in r["results"]]
        self.assertEqual(paths[0], "f1.txt")       # 完全同向排第一
        self.assertIn("f3.txt", paths)             # 0.707 >= 0.3 保留
        self.assertNotIn("f2.txt", paths)          # 0 分被阈值丢弃

    def test_unavailable_returns_error(self):
        r = self.si.search("x")  # _available=False 且无矩阵
        self.assertFalse(r["ok"])
        self.assertTrue(r["error"])

    def test_result_fields(self):
        self._put("/tmp/子目录/手册.docx", "正文", [1.0, 0.0])
        self.si._reload()
        self.si._available = True
        with mock.patch("semantic.embed_texts", return_value=[[1.0, 0.0]]):
            r = self.si.search("q")
        it = r["results"][0]
        self.assertEqual(it["name"], "手册.docx")
        self.assertEqual(it["ext"], ".docx")
        self.assertEqual(it["text"], "正文")
        self.assertGreaterEqual(it["score"], 0.3)


if __name__ == "__main__":
    unittest.main()
