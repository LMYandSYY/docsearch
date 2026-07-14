# -*- coding: utf-8 -*-
"""文档文字提取：PDF（含扫描件 OCR）、Word(.docx/.doc)、纯文本。

所有第三方库都在函数内部延迟导入，缺少某个库时只会跳过对应格式，
不会让整个程序无法启动。
"""
import html
import os
import re
import shutil
import subprocess
import tempfile

SUPPORTED = {".pdf", ".docx", ".doc", ".txt", ".md", ".markdown", ".csv", ".log"}


def extract_text(path, ocr_pages=True):
    """提取一个文件的纯文本。

    返回 (text, meta)。meta 含 ext / pages / ocr_used / errors。
    """
    ext = os.path.splitext(path)[1].lower()
    text, pages, ocr_used, errors = "", None, False, []
    try:
        if ext == ".pdf":
            text, pages, ocr_used = _pdf(path, ocr_pages)
        elif ext == ".docx":
            text = _docx_text(path)
        elif ext == ".doc":
            text = _doc_text(path)
        elif ext in (".txt", ".md", ".markdown", ".csv", ".log"):
            text = _textfile(path)
        else:
            errors.append("不支持的格式: " + ext)
    except Exception as e:  # noqa: BLE001
        errors.append("提取失败: " + repr(e))
    return text, {"ext": ext, "pages": pages, "ocr_used": ocr_used, "errors": errors}


# ----------------------------- PDF ----------------------------- #

def _best_lang(langs):
    if "chi_sim" in langs and "eng" in langs:
        return "chi_sim+eng"
    if "chi_sim" in langs:
        return "chi_sim"
    return "eng"


def _pdf(path, ocr_pages):
    try:
        import pymupdf as fitz
    except ImportError:
        import fitz  # 旧版本别名

    doc = fitz.open(path)
    pages_text, ocr_used = [], False
    ocr_ok, lang = False, "eng"
    if ocr_pages:
        try:
            import pytesseract  # noqa: F401
            from PIL import Image  # noqa: F401
            ocr_ok = True
            try:
                lang = _best_lang(set(pytesseract.get_languages() or []))
            except Exception:
                lang = "eng"
        except Exception:
            ocr_ok = False

    for page in doc:
        t = (page.get_text() or "").strip()
        # 文字很少，基本可判定为扫描页 → OCR
        if len(t) < 10 and ocr_ok:
            try:
                import io
                import pytesseract
                from PIL import Image
                pix = page.get_pixmap(dpi=200)
                img = Image.open(io.BytesIO(pix.tobytes("png")))
                try:
                    t = (pytesseract.image_to_string(img, lang=lang) or "").strip()
                except Exception:
                    t = (pytesseract.image_to_string(img, lang="eng") or "").strip()
                ocr_used = True
            except Exception:
                pass
        pages_text.append(t)

    n = len(pages_text)
    doc.close()
    return "\n".join(pages_text), n, ocr_used


# ----------------------------- Word ----------------------------- #

def _docx_text(path):
    import docx
    d = docx.Document(path)
    parts = [p.text for p in d.paragraphs if p.text and p.text.strip()]
    for table in d.tables:
        for row in table.rows:
            for cell in row.cells:
                if cell.text and cell.text.strip():
                    parts.append(cell.text)
    return "\n".join(parts)


def _doc_text(path):
    """老版 .doc：先用纯 Python(olefile) 提取正文；提取不到再退回 LibreOffice 转 .docx。"""
    text = _doc_text_olefile(path)
    if text.strip():
        return text
    docx_path = _doc_to_docx(path)
    if docx_path:
        try:
            return _docx_text(docx_path)
        except Exception:
            return ""
    return ""


def _doc_text_olefile(path):
    """纯 Python 提取 .doc 正文：读 WordDocument 流，按 UTF-16LE 解码并清理。

    不依赖任何外部程序，开箱即用；老版 Word（含中文）正文通常以 UTF-16LE 存储，
    用于关键词检索足够可靠。富文本/排版会丢失，需要精美预览可另装 LibreOffice。
    """
    try:
        import olefile
    except ImportError:
        return ""
    try:
        ole = olefile.OleFileIO(path)
        if not ole.exists("WordDocument"):
            ole.close()
            return ""
        data = ole.openstream("WordDocument").read()
        ole.close()
    except Exception:
        return ""
    return _clean_doc_text(data.decode("utf-16-le", errors="ignore"))


def _is_text_cp(o):
    """是否为「正常文字」码点：ASCII / 拉丁扩展 / CJK / 全角 / 通用标点。"""
    return (
        0x20 <= o <= 0x7E
        or 0xA0 <= o <= 0x24F
        or 0x3000 <= o <= 0x9FFF      # CJK 符号/统一表意/扩展A
        or 0xF900 <= o <= 0xFAFF      # CJK 兼容表意
        or 0xFF00 <= o <= 0xFFEF      # 全角形式
        or 0x2000 <= o <= 0x206F      # 通用标点（破折号、引号等）
    )


def _clean_doc_text(text):
    """把 .doc 二进制头解码产生的噪声清掉，只留可读正文。

    1) 非文字码点统一转成空格（而非丢弃），避免不相邻的字被错误拼到一起；
    2) 正文前的 FIB 二进制头里，可读字符都是零散短串，真正正文是连续长串——
       所以定位到第一段连续长文字（≥6）作为正文起点，跳过前面的噪声。
    """
    out = []
    for ch in text:
        o = ord(ch)
        if ch in "\r\n":
            out.append("\n")
        elif ch == "\t":
            out.append(" ")
        elif _is_text_cp(o):
            out.append(ch)
        else:
            out.append(" ")
    s = "".join(out)
    s = re.sub(r"[ \t]+", " ", s)
    m = re.search(r"\S{6,}", s)
    if m:
        s = s[m.start():]
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def _doc_to_docx(path):
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    if not soffice:
        return None
    outdir = tempfile.mkdtemp(prefix="docconv_")
    try:
        subprocess.run(
            [soffice, "--headless", "--convert-to", "docx", "--outdir", outdir, path],
            check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=180,
        )
    except Exception:
        return None
    out = os.path.join(outdir, os.path.splitext(os.path.basename(path))[0] + ".docx")
    return out if os.path.isfile(out) else None


# ----------------------------- 纯文本 ----------------------------- #

def _textfile(path):
    for enc in ("utf-8", "gb18030", "big5", "latin-1"):
        try:
            with open(path, "r", encoding=enc) as f:
                return f.read()
        except UnicodeDecodeError:
            continue
    return ""


# ----------------------------- 预览 ----------------------------- #

def docx_to_html(path):
    import mammoth
    with open(path, "rb") as f:
        result = mammoth.convert_to_html(f)
    return result.value or "<p>(文档为空)</p>"


def to_preview_html(path):
    """供预览页使用的 HTML 正文。"""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".docx":
        return docx_to_html(path)
    if ext == ".doc":
        # 装了 LibreOffice 就给富文本预览；否则回退纯文本（同样可高亮命中词）
        docx_path = _doc_to_docx(path)
        if docx_path:
            try:
                return docx_to_html(docx_path)
            except Exception:
                pass
        text = _doc_text_olefile(path)
        if text.strip():
            return "<pre style='white-space:pre-wrap;word-break:break-word'>" + html.escape(text) + "</pre>"
        return "<p>无法读取此 .doc 文件的内容，可尝试另存为 .docx。</p>"
    return "<p>暂不支持预览此格式。</p>"
