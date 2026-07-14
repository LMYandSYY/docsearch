# -*- coding: utf-8 -*-
"""测试夹具：在临时目录里生成各类样本文件。"""
import os
import sys
import tempfile

# 把项目根目录(docsearch)加入 sys.path，方便 import extractor/indexer/app
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

CJK_FONTS = [
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/Library/Fonts/Arial Unicode.ttf",
]


def make_tmpdir():
    return tempfile.mkdtemp(prefix="dstest_")


def write_txt(path, text, enc="utf-8"):
    with open(path, "w", encoding=enc) as f:
        f.write(text)


def make_docx(path, paragraphs, tables=None):
    from docx import Document
    d = Document()
    for p in paragraphs:
        d.add_paragraph(p)
    if tables:
        for t in tables:
            tb = d.add_table(rows=len(t), cols=len(t[0]))
            for i, row in enumerate(t):
                for j, val in enumerate(row):
                    tb.cell(i, j).text = val
    d.save(path)


def make_pdf(path, text):
    """生成文本型 PDF（默认字体仅含 Latin，text 限 ASCII/Latin）。"""
    import fitz
    doc = fitz.open()
    pg = doc.new_page()
    pg.insert_text((72, 72), text)
    doc.save(path)
    doc.close()


def make_pdf_cjk(path, text):
    """用系统 CJK 字体生成中文 PDF；无可用字体返回 False（测试应跳过）。"""
    import fitz
    font = next((f for f in CJK_FONTS if os.path.exists(f)), None)
    if not font:
        return False
    doc = fitz.open()
    pg = doc.new_page()
    pg.insert_text((72, 100), text, fontname="F0", fontfile=font, fontsize=14)
    doc.save(path)
    doc.close()
    return True
