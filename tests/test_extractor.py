# -*- coding: utf-8 -*-
import os
import unittest

from helpers import (make_tmpdir, write_txt, make_docx, make_pdf, make_pdf_cjk,
                     make_xlsx, make_pptx, make_epub, make_rtf, make_odt)
import extractor


class TestExtractor(unittest.TestCase):
    def test_txt_utf8(self):
        d = make_tmpdir()
        p = os.path.join(d, "a.txt")
        write_txt(p, "电源开关在哪里\n第二行")
        text, meta = extractor.extract_text(p)
        self.assertIn("开关", text)
        self.assertEqual(meta["ext"], ".txt")
        self.assertEqual(meta["errors"], [])

    def test_txt_gbk(self):
        d = make_tmpdir()
        p = os.path.join(d, "b.txt")
        write_txt(p, "电网测试", enc="gbk")
        text, _ = extractor.extract_text(p)
        self.assertIn("电网", text)

    def test_docx(self):
        d = make_tmpdir()
        p = os.path.join(d, "c.docx")
        make_docx(p, ["第一段内容", "请找到这个开关按钮"])
        text, meta = extractor.extract_text(p)
        self.assertIn("开关", text)
        self.assertEqual(meta["ext"], ".docx")

    def test_docx_table(self):
        d = make_tmpdir()
        p = os.path.join(d, "t.docx")
        make_docx(p, ["正文"], tables=[[["项目", "值"], ["变电站", "甲"]]])
        text, _ = extractor.extract_text(p)
        self.assertIn("变电站", text)
        self.assertIn("甲", text)

    def test_xlsx(self):
        d = make_tmpdir()
        p = os.path.join(d, "a.xlsx")
        make_xlsx(p, [["项目", "值"], ["变电站", "甲"]])
        text, meta = extractor.extract_text(p)
        self.assertIn("变电站", text)
        self.assertIn("甲", text)
        self.assertEqual(meta["ext"], ".xlsx")

    def test_pptx(self):
        d = make_tmpdir()
        p = os.path.join(d, "a.pptx")
        make_pptx(p, ["封面页", "电网开关说明"])
        text, meta = extractor.extract_text(p)
        self.assertIn("开关", text)
        self.assertEqual(meta["ext"], ".pptx")

    def test_epub(self):
        d = make_tmpdir()
        p = os.path.join(d, "a.epub")
        make_epub(p, ["第一章 电网", "第二章 开关"])
        text, meta = extractor.extract_text(p)
        self.assertIn("电网", text)
        self.assertIn("开关", text)
        self.assertEqual(meta["ext"], ".epub")

    def test_rtf(self):
        d = make_tmpdir()
        p = os.path.join(d, "a.rtf")
        make_rtf(p, "the main SWITCH is here")
        text, meta = extractor.extract_text(p)
        self.assertIn("SWITCH", text)
        self.assertEqual(meta["ext"], ".rtf")

    def test_odt(self):
        d = make_tmpdir()
        p = os.path.join(d, "a.odt")
        make_odt(p, "电网开关ODF测试")
        text, meta = extractor.extract_text(p)
        self.assertIn("开关", text)
        self.assertEqual(meta["ext"], ".odt")

    def test_json(self):
        d = make_tmpdir()
        p = os.path.join(d, "a.json")
        write_txt(p, '{"key": "开关状态", "v": 1}')
        text, meta = extractor.extract_text(p)
        self.assertIn("开关", text)
        self.assertEqual(meta["ext"], ".json")

    def test_html_strip_tags(self):
        d = make_tmpdir()
        p = os.path.join(d, "a.html")
        write_txt(p, "<html><body><p>电网<b>开关</b></p></body></html>")
        text, meta = extractor.extract_text(p)
        self.assertIn("电网", text)
        self.assertIn("开关", text)
        self.assertNotIn("<b>", text)  # 标签应被剥离
        self.assertEqual(meta["ext"], ".html")

    def test_pdf_latin(self):
        d = make_tmpdir()
        p = os.path.join(d, "x.pdf")
        make_pdf(p, "the main SWITCH is here")
        text, meta = extractor.extract_text(p)
        self.assertIn("SWITCH", text)
        self.assertEqual(meta["ext"], ".pdf")

    def test_pdf_cjk(self):
        d = make_tmpdir()
        p = os.path.join(d, "c.pdf")
        ok = make_pdf_cjk(p, "电网开关测试")
        if not ok:
            self.skipTest("无可用的系统 CJK 字体")
        text, _ = extractor.extract_text(p)
        self.assertIn("电网", text)
        self.assertIn("开关", text)

    def test_unsupported_ext(self):
        d = make_tmpdir()
        p = os.path.join(d, "a.jpg")
        write_txt(p, "not really an image")
        _, meta = extractor.extract_text(p)
        self.assertTrue(meta["errors"])  # 不支持的格式应记错误

    def test_clean_doc_text_strips_control(self):
        s = extractor._clean_doc_text("电\x00网 test")
        self.assertIn("test", s)
        self.assertNotIn("\x00", s)
        # 多余空行折叠
        s3 = extractor._clean_doc_text("a\n\n\n\n\nb")
        self.assertNotIn("\n\n\n", s3)

    def test_clean_doc_text_keeps_adjacent_cjk(self):
        s = extractor._clean_doc_text("电网开关")
        self.assertIn("电网开关", s)

    def test_is_text_cp(self):
        self.assertTrue(extractor._is_text_cp(ord("电")))
        self.assertTrue(extractor._is_text_cp(ord("A")))
        self.assertFalse(extractor._is_text_cp(0x01))
        self.assertFalse(extractor._is_text_cp(0xE000))  # 私用区


if __name__ == "__main__":
    unittest.main()
