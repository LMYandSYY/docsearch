# -*- coding: utf-8 -*-
"""本地文档全文检索工具 —— Flask 入口。

运行： python app.py   （随后会自动打开浏览器）
端口固定 8765（避开 macOS 的 5000 AirPlay 端口）。
"""
import html as html_mod
import json
import os
import platform
import subprocess
import threading
import traceback
import webbrowser

from flask import Flask, jsonify, render_template, request, send_file
from werkzeug.exceptions import HTTPException

import extractor
from indexer import Indexer
import opener
from semantic import SemanticIndex

BASE = os.path.dirname(os.path.abspath(__file__))
PORT = 8765
SUPPORTED_EXTS = extractor.SUPPORTED  # 与 extractor.SUPPORTED 同源，避免两处不同步
SETTINGS_FILE = os.path.join(BASE, "settings.json")

app = Flask(__name__)
indexer = Indexer(os.path.join(BASE, "cache.db"))
semantic = SemanticIndex(os.path.join(BASE, "cache.db"))  # 与 indexer 共用 cache.db


def load_settings():
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            raw = json.load(f)
    except Exception:
        return {"folders": [], "ocr": True}
    # 兼容旧版单 folder 字段
    folders = raw.get("folders")
    if folders is None and raw.get("folder"):
        folders = [raw["folder"]]
    return {"folders": folders or [], "ocr": raw.get("ocr", True)}


def save_settings(folders, ocr):
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump({"folders": folders, "ocr": ocr}, f, ensure_ascii=False)
    except Exception:
        pass


def _safe_file(path):
    return bool(path) and os.path.isfile(path)


def _pick_folder():
    """调系统原生目录选择器，返回选中的绝对路径；取消或不可用返回 None。"""
    sys_name = platform.system()
    common = dict(capture_output=True, encoding="utf-8", errors="replace", timeout=600)
    try:
        if sys_name == "Darwin":
            out = subprocess.run(["osascript", "-e", "POSIX path of (choose folder)"], **common)
            if out.returncode != 0:
                return None  # 用户取消
            return out.stdout.strip().rstrip("/") or None
        if sys_name == "Windows":
            # -STA：FolderBrowserDialog 需单线程单元；UTF-8 输出避免中文路径乱码
            ps = (
                "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
                "Add-Type -AssemblyName System.Windows.Forms; "
                "$f = New-Object System.Windows.Forms.FolderBrowserDialog; "
                "$f.RootFolder = [System.Environment+SpecialFolder]::MyComputer; "
                "if ($f.ShowDialog() -eq 'OK') { Write-Output $f.SelectedPath }"
            )
            out = subprocess.run(["powershell", "-NoProfile", "-STA", "-Command", ps], **common)
            return (out.stdout.strip() or None) if out.returncode == 0 else None
        # Linux / 其它：尝试 zenity
        out = subprocess.run(["zenity", "--file-selection", "--directory"], **common)
        return (out.stdout.strip() or None) if out.returncode == 0 else None
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    except Exception:  # noqa: BLE001
        return None


@app.errorhandler(HTTPException)
def _http_error(e):
    return jsonify({"ok": False, "error": f"{e.code} {e.name}: {e.description}"}), e.code


@app.errorhandler(Exception)
def _unhandled(e):
    # 本地调试友好：把真实异常与堆栈以 JSON 返回，前端可直接显示，不再出现 HTML 报错页
    return jsonify({"ok": False, "error": repr(e), "trace": traceback.format_exc()}), 500


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/settings")
def api_settings():
    return jsonify(load_settings())


@app.post("/api/load")
def api_load():
    data = request.get_json(silent=True) or {}
    # 兼容：优先 folders 列表，其次旧版单个 folder
    folders = data.get("folders")
    if folders is None:
        folders = [data.get("folder")] if data.get("folder") else []
    folders = [f.strip() for f in folders if isinstance(f, str) and f.strip()]
    ocr = bool(data.get("ocr", True))
    if not folders:
        return jsonify({"ok": False, "error": "请至少添加一个文件夹路径"}), 400
    count, stats = indexer.load_folders(folders, SUPPORTED_EXTS, ocr=ocr)
    if not indexer.folders:
        return jsonify({"ok": False, "error": "没有有效的文件夹路径：" + "、".join(folders)}), 400
    save_settings(folders, ocr)
    # 精确索引已就绪即可搜索；语义向量放后台线程补建（文件没变会复用缓存）
    threading.Thread(target=semantic.ensure, args=(indexer.corpus(),), daemon=True).start()
    return jsonify({"ok": True, "count": count, "stats": stats, "folders": indexer.folders})


@app.get("/api/search")
def api_search():
    kw = (request.args.get("kw") or "").strip()
    if not kw:
        return jsonify([])
    return jsonify(indexer.search(kw))


@app.get("/api/pick_folder")
def api_pick_folder():
    """弹出系统原生目录选择器；返回 {ok, path} 或 {ok:false, cancelled:true}。"""
    path = _pick_folder()
    if path:
        return jsonify({"ok": True, "path": path})
    return jsonify({"ok": False, "cancelled": True})


@app.get("/preview")
def preview():
    path = request.args.get("path", "")
    kw = request.args.get("kw", "")
    if not _safe_file(path):
        return "文件不存在", 404
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        # 浏览器原生渲染 PDF，直接返回文件
        return send_file(path)
    if ext not in SUPPORTED_EXTS:
        # 非支持格式直接下载，避免空预览
        return send_file(path, as_attachment=True)
    # 其余支持格式统一走 to_preview_html（docx/doc 富文本，其它回退纯文本 <pre>）
    try:
        body = extractor.to_preview_html(path)
    except Exception as e:  # noqa: BLE001
        body = "<p>预览失败：" + html_mod.escape(repr(e)) + "</p>"
    return render_template("preview.html", title=os.path.basename(path), body_html=body, kw=kw)


@app.get("/api/semantic_search")
def api_semantic_search():
    kw = (request.args.get("kw") or "").strip()
    if not kw:
        return jsonify({"ok": True, "results": []})
    return jsonify(semantic.search(kw))


@app.get("/api/semantic_status")
def api_semantic_status():
    return jsonify(semantic.status())


@app.post("/api/open")
def api_open():
    data = request.get_json(silent=True) or {}
    path, mode = data.get("path"), data.get("mode")
    if not path or not os.path.isfile(path):
        return jsonify({"ok": False, "error": "文件不存在：" + str(path)}), 404
    if mode == "folder":
        return jsonify(opener.open_folder(path))
    if mode == "wps":
        return jsonify(opener.open_with_wps(path))
    return jsonify({"ok": False, "error": "mode 必须是 folder 或 wps"}), 400


def _open_browser():
    webbrowser.open(f"http://127.0.0.1:{PORT}/")


if __name__ == "__main__":
    threading.Timer(1.2, _open_browser).start()
    app.run(host="127.0.0.1", port=PORT, threaded=True, debug=False)
