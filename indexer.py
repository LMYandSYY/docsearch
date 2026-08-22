# -*- coding: utf-8 -*-
"""扫描文件夹、缓存提取结果、关键词检索。

支持加载**多个文件夹**合并检索。文档量在几百以内、偶尔更新，所以采用
「按需提取 + SQLite 缓存」策略：首次加载逐个解析；文件没改（mtime+size 不变）
且 OCR 设置一致就直接复用缓存。搜索在内存里做子串匹配，中文友好、即时返回。
"""
import os
import re
import sqlite3
import threading

import extractor

# 提取逻辑版本：升级提取代码时递增，旧缓存自动失效、重新解析
CACHE_VERSION = 4
# 片段上下文长度（命中词前后取多少字符）；搜索去重也用它对齐
SNIPPET_CTX = 80


class Indexer:
    def __init__(self, cache_path):
        self.cache_path = cache_path
        # Flask 多线程处理请求，连接需跨线程共享：关闭同线程检查 + 用锁串行化访问
        self.conn = sqlite3.connect(cache_path, check_same_thread=False)
        # 双连接并发写同一库，WAL+等待避免 locked
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=5000")
        self.lock = threading.RLock()
        self._init_db()
        self.cache = {}        # path -> {name, ext, text, meta}
        self.folders = []      # 最近一次成功加载的、有效的文件夹列表

    def _init_db(self):
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS files (
                path TEXT PRIMARY KEY,
                name TEXT, ext TEXT, mtime INTEGER, size INTEGER,
                pages INTEGER, ocr_used INTEGER, text TEXT,
                ver INTEGER, ocr INTEGER
            )
            """
        )
        cols = {row[1] for row in self.conn.execute("PRAGMA table_info(files)").fetchall()}
        # 缺关键列就重建（缓存可随时丢弃）
        if cols and not {"ver", "ocr"}.issubset(cols):
            self.conn.execute("DROP TABLE files")
            self.conn.execute(
                """
                CREATE TABLE files (
                    path TEXT PRIMARY KEY,
                    name TEXT, ext TEXT, mtime INTEGER, size INTEGER,
                    pages INTEGER, ocr_used INTEGER, text TEXT,
                    ver INTEGER, ocr INTEGER
                )
                """
            )
        self.conn.commit()

    def _cache_get(self, path, mtime, size, ocr_flag):
        row = self.conn.execute(
            "SELECT ext, pages, ocr_used, text FROM files "
            "WHERE path=? AND mtime=? AND size=? AND ver=? AND ocr=?",
            (path, mtime, size, CACHE_VERSION, int(ocr_flag)),
        ).fetchone()
        if not row:
            return None
        ext, pages, ocr_used, text = row
        return {
            "text": text or "",
            "meta": {"ext": ext, "pages": pages, "ocr_used": bool(ocr_used), "errors": []},
        }

    def _cache_put(self, path, st, text, meta, ocr_flag):
        self.conn.execute(
            "INSERT OR REPLACE INTO files (path,name,ext,mtime,size,pages,ocr_used,text,ver,ocr) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                path,
                os.path.basename(path),
                meta.get("ext"),
                int(st.st_mtime),
                st.st_size,
                meta.get("pages"),
                int(bool(meta.get("ocr_used"))),
                text,
                CACHE_VERSION,
                int(ocr_flag),
            ),
        )

    def load_folders(self, folders, exts, ocr=True):
        with self.lock:
            return self._load_folders_impl(folders, exts, ocr)

    def _load_folders_impl(self, folders, exts, ocr=True):
        self.folders = []
        found = []
        for f in folders:
            if not f:
                continue
            folder = os.path.abspath(f)
            if not os.path.isdir(folder):
                continue
            self.folders.append(folder)
            for root, dirs, files in os.walk(folder):
                dirs[:] = [d for d in dirs if not d.startswith(".") and d != "__MACOSX"]
                for fn in files:
                    # 跳过隐藏文件；跳过 ~$ 开头的 Office 临时锁文件（owner file，
                    # Word/Excel/PPT 打开文档时生成，非文档内容，读取必报错）
                    if fn.startswith(".") or fn.startswith("~$"):
                        continue
                    if os.path.splitext(fn)[1].lower() in exts:
                        found.append(os.path.join(root, fn))
        # 多个路径可能重叠包含同一文件，按路径去重
        seen = set()
        uniq = []
        for p in found:
            if p not in seen:
                seen.add(p)
                uniq.append(p)

        self.cache = {}
        stats = {"total": len(uniq), "ocr_used": 0, "errors": [], "by_ext": {}}
        for p in uniq:
            ext = os.path.splitext(p)[1].lower()
            try:
                st = os.stat(p)
                cached = self._cache_get(p, int(st.st_mtime), st.st_size, ocr)
                if cached is not None:
                    text, meta = cached["text"], cached["meta"]
                else:
                    text, meta = extractor.extract_text(p, ocr_pages=ocr)
                    self._cache_put(p, st, text, meta, ocr)
                self.cache[p] = {
                    "name": os.path.basename(p),
                    "ext": ext,
                    "text": text or "",
                    "meta": meta,
                    "mtime": int(st.st_mtime),
                    "size": st.st_size,
                }
                stats["by_ext"][ext] = stats["by_ext"].get(ext, 0) + 1
                if meta.get("ocr_used"):
                    stats["ocr_used"] += 1
                if meta.get("errors"):
                    stats["errors"].append({"file": os.path.basename(p), "msg": "; ".join(meta["errors"])})
            except Exception as e:  # noqa: BLE001
                stats["errors"].append({"file": os.path.basename(p), "msg": repr(e)})

        self.conn.commit()
        return len(self.cache), stats

    def corpus(self):
        """语义索引用的语料快照：path -> {text, mtime, size}。"""
        with self.lock:
            return {p: {"text": i["text"], "mtime": i["mtime"], "size": i["size"]}
                    for p, i in self.cache.items()}

    def search(self, keyword):
        with self.lock:
            return self._search_impl(keyword)

    def _search_impl(self, keyword):
        kw = keyword.strip()
        if not kw:
            return []
        kw_l = kw.lower()
        n = len(kw_l)
        results = []
        for path, info in self.cache.items():
            text = info["text"]
            if not text:
                continue
            text_l = text.lower()
            idx = text_l.find(kw_l)
            if idx < 0:
                continue
            count, snippets, start = 0, [], 0
            next_allowed = -10**9  # 去重：相邻命中若落在已展示片段范围内则跳过，避免重复输出
            while True:
                i = text_l.find(kw_l, start)
                if i < 0:
                    break
                count += 1
                if len(snippets) < 3 and i >= next_allowed:
                    snippets.append(_snippet(text, i, n))
                    next_allowed = i + n + SNIPPET_CTX
                start = i + n
            results.append({
                "path": path,
                "name": info["name"],
                "ext": info["ext"],
                "pages": info["meta"].get("pages"),
                "ocr_used": bool(info["meta"].get("ocr_used")),
                "count": count,
                "snippets": snippets,
            })
        results.sort(key=lambda r: (-r["count"], r["name"].lower()))
        return results


def _snippet(text, idx, kwlen, ctx=SNIPPET_CTX):
    start = max(0, idx - ctx)
    end = min(len(text), idx + kwlen + ctx)
    raw = text[start:end].replace("\r", " ").replace("\n", " ")
    raw = re.sub(r"\s+", " ", raw).strip()
    return ("…" if start > 0 else "") + raw + ("…" if end < len(text) else "")
