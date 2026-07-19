# -*- coding: utf-8 -*-
import os
import unittest

from helpers import make_tmpdir, write_txt, make_docx
import indexer


def build_files(files):
    """files: {name: content}; content 为 ('docx', [段落]) 时生成 docx，否则 txt。"""
    d = make_tmpdir()
    for name, content in files.items():
        p = os.path.join(d, name)
        if isinstance(content, tuple) and content[0] == "docx":
            make_docx(p, content[1])
        else:
            write_txt(p, content)
    return d


class TestIndexer(unittest.TestCase):
    def test_search_hit_and_miss(self):
        d = build_files({"a.txt": "电源开关在这里", "b.txt": "无关内容"})
        idx = indexer.Indexer(os.path.join(d, "cache.db"))
        idx.load_folders([d], {".txt"})
        r = idx.search("开关")
        self.assertEqual([x["name"] for x in r], ["a.txt"])
        self.assertEqual(idx.search("绝对不存在的词XYZ"), [])

    def test_case_insensitive(self):
        d = build_files({"a.txt": "Switch and SWITCH"})
        idx = indexer.Indexer(os.path.join(d, "c.db"))
        idx.load_folders([d], {".txt"})
        self.assertEqual(idx.search("switch")[0]["count"], 2)

    def test_count_and_snippet(self):
        d = build_files({"a.txt": "开头 开关 中间内容 开关 结尾 开关"})
        idx = indexer.Indexer(os.path.join(d, "c.db"))
        idx.load_folders([d], {".txt"})
        r = idx.search("开关")
        self.assertEqual(r[0]["count"], 3)
        self.assertTrue(r[0]["snippets"])
        self.assertIn("开关", r[0]["snippets"][0])

    def test_snippet_dedup(self):
        # 10 个紧邻的"电网"应只展示 1 个片段（去重）
        d = build_files({"a.txt": "电网" * 10})
        idx = indexer.Indexer(os.path.join(d, "c.db"))
        idx.load_folders([d], {".txt"})
        r = idx.search("电网")
        self.assertEqual(r[0]["count"], 10)
        self.assertLessEqual(len(r[0]["snippets"]), 1)

    def test_results_sorted_by_count(self):
        d = build_files({"a.txt": "电网 电网", "b.txt": "电网"})
        idx = indexer.Indexer(os.path.join(d, "c.db"))
        idx.load_folders([d], {".txt"})
        r = idx.search("电网")
        self.assertEqual([x["name"] for x in r], ["a.txt", "b.txt"])

    def test_multiple_folders(self):
        d1 = build_files({"a.txt": "电网一"})
        d2 = build_files({"b.txt": "电网二"})
        idx = indexer.Indexer(os.path.join(d1, "c.db"))
        idx.load_folders([d1, d2], {".txt"})
        r = idx.search("电网")
        self.assertEqual(sorted(x["name"] for x in r), ["a.txt", "b.txt"])

    def test_duplicate_folder_dedup(self):
        d = build_files({"a.txt": "电网"})
        idx = indexer.Indexer(os.path.join(d, "c.db"))
        idx.load_folders([d, d], {".txt"})  # 同目录加两次
        self.assertEqual(len(idx.search("电网")), 1)

    def test_walk_subdirs(self):
        d = make_tmpdir()
        write_txt(os.path.join(d, "a.txt"), "电网")
        sub = os.path.join(d, "sub")
        os.makedirs(sub)
        write_txt(os.path.join(sub, "b.txt"), "电网")
        idx = indexer.Indexer(os.path.join(d, "c.db"))
        idx.load_folders([d], {".txt"})
        self.assertEqual(len(idx.search("电网")), 2)

    def test_skip_office_lock_files(self):
        # ~$ 开头是 Office 打开文档时的临时锁文件（owner file），应被跳过、不触发解析异常
        d = build_files({"a.txt": "电网开关", "~$temp.docx": "无关占位"})
        idx = indexer.Indexer(os.path.join(d, "c.db"))
        _, stats = idx.load_folders([d], {".txt", ".docx"})
        self.assertEqual([x["name"] for x in idx.search("电网")], ["a.txt"])
        self.assertEqual(stats["errors"], [])  # ~$ 文件不再进入解析

    def test_invalid_folder_skipped(self):
        d = build_files({"a.txt": "电网"})
        idx = indexer.Indexer(os.path.join(d, "c.db"))
        idx.load_folders([d, "/no/such/dir"], {".txt"})
        self.assertEqual(len(idx.folders), 1)  # 无效目录被跳过
        self.assertEqual(len(idx.search("电网")), 1)

    def test_cache_reuse_across_instances(self):
        d = build_files({"a.txt": "电网开关"})
        db = os.path.join(d, "c.db")
        idx = indexer.Indexer(db)
        idx.load_folders([d], {".txt"})
        n1 = idx.search("电网")[0]["count"]
        # 新实例、同一缓存库、文件未变 → 命中缓存、结果一致
        idx2 = indexer.Indexer(db)
        idx2.load_folders([d], {".txt"})
        self.assertEqual(idx2.search("电网")[0]["count"], n1)

    def test_cache_ocr_flag_changes_invalidate(self):
        d = build_files({"a.txt": "电网"})
        db = os.path.join(d, "c.db")
        idx = indexer.Indexer(db)
        idx.load_folders([d], {".txt"}, ocr=True)
        # 切换 OCR 设置后应重新解析（缓存键含 ocr），仍能搜到
        idx.load_folders([d], {".txt"}, ocr=False)
        self.assertEqual(len(idx.search("电网")), 1)


if __name__ == "__main__":
    unittest.main()
