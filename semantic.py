# -*- coding: utf-8 -*-
"""语义搜索：文档分段 -> Ollama embedding -> SQLite 缓存 -> 内存余弦检索。

依赖本机 Ollama（bge-m3）。Ollama 不可用时优雅降级：
status.available=False，search 返回错误说明，精确搜索不受影响。
"""
import json
import os
import sqlite3
import threading
import urllib.error
import urllib.request

import numpy as np

OLLAMA_URL = "http://localhost:11434"
EMBED_MODEL = "bge-m3"
CHUNK_SIZE = 500      # 分段目标字数
EMBED_BATCH = 16      # 每次请求 Ollama 的段数
EMBED_TIMEOUT = 30    # 秒
TOP_N = 20            # 语义检索返回条数
MIN_SCORE = 0.3       # 余弦相似度阈值，低于则丢弃


def chunk_text(text, size=CHUNK_SIZE):
    """把正文切成检索块：按段落聚合到 ~size 字，超长段按 size 硬切。"""
    if not text:
        return []
    chunks, buf = [], ""

    def flush():
        nonlocal buf
        if buf.strip():
            chunks.append(buf.strip())
        buf = ""

    for para in text.split("\n"):
        p = para.strip()
        if not p:
            continue
        while len(p) > size:  # 超长段硬切，避免单块语义被稀释
            chunks.append(p[:size])
            p = p[size:]
        buf += ("\n" if buf else "") + p
        if len(buf) >= size:
            flush()
    flush()
    return chunks


def embed_texts(texts):
    """批量调 Ollama /api/embed；任何失败返回 None（调用方降级）。"""
    if not texts:
        return []
    out = []
    for i in range(0, len(texts), EMBED_BATCH):
        part = texts[i:i + EMBED_BATCH]
        body = json.dumps({"model": EMBED_MODEL, "input": part}).encode("utf-8")
        req = urllib.request.Request(
            OLLAMA_URL + "/api/embed", data=body,
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=EMBED_TIMEOUT) as r:
                data = json.loads(r.read().decode("utf-8"))
            vecs = data.get("embeddings") or []
            if len(vecs) != len(part):
                return None
            out.extend(vecs)
        except Exception:
            return None
    return out


def probe_ollama():
    """检测 Ollama 与模型可用性；None=可用，str=给人看的错误说明。"""
    body = json.dumps({"model": EMBED_MODEL, "input": ["ping"]}).encode("utf-8")
    req = urllib.request.Request(
        OLLAMA_URL + "/api/embed", data=body,
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            json.loads(r.read().decode("utf-8"))
        return None
    except urllib.error.HTTPError as e:
        try:
            msg = e.read().decode("utf-8", "ignore")
        except Exception:
            msg = ""
        if "not found" in msg or e.code == 404:
            return "模型 %s 不存在，请先执行: ollama pull %s" % (EMBED_MODEL, EMBED_MODEL)
        return "Ollama 返回错误 %s" % e.code
    except Exception:
        return "无法连接 Ollama（%s），请先启动 Ollama" % OLLAMA_URL


class SemanticIndex:
    """向量索引：chunks 表缓存 + 内存归一化矩阵。

    ensure() 在后台线程跑；search() 只读内存快照，两者不互相阻塞。
    """

    def __init__(self, db_path):
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        # 双连接并发写同一库，WAL+等待避免 locked
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=5000")
        self._init_db()
        self._matrix = None      # np.ndarray (N, dim)，行已归一化
        self._meta = []          # [{path, chunk_idx, text}]，与矩阵行一一对应
        self._available = False
        self._error = "尚未构建语义索引，请先加载文件夹"
        self._building = False
        self._pending_corpus = None
        self._done = 0
        self._total = 0

    def _init_db(self):
        with self.lock:
            self.conn.execute(
                "CREATE TABLE IF NOT EXISTS chunks ("
                " path TEXT, chunk_idx INTEGER, text TEXT,"
                " mtime INTEGER, size INTEGER, embedding BLOB,"
                " PRIMARY KEY (path, chunk_idx))"
            )
            self.conn.commit()

    def status(self):
        with self.lock:
            return {
                "available": self._available,
                "error": self._error,
                "building": self._building,
                "done": self._done,
                "total": self._total,
                "model": EMBED_MODEL,
            }

    def ensure(self, corpus):
        """为语料建/补向量索引。已在建时记下新语料，建完自动再跑一轮。"""
        with self.lock:
            if self._building:
                self._pending_corpus = corpus  # 全量快照，后来的直接覆盖（last-wins）
                return
            self._building = True
            self._done = 0
            self._total = len(corpus)
        try:
            while True:
                self._ensure_impl(corpus)
                with self.lock:
                    corpus, self._pending_corpus = self._pending_corpus, None
                    if corpus is None:
                        break
                    self._done = 0
                    self._total = len(corpus)
        finally:
            with self.lock:
                self._building = False

    def _ensure_impl(self, corpus):
        err = probe_ollama()
        with self.lock:
            self._available = err is None
            self._error = err
        with self.lock:  # 清掉已不在语料里的文件
            for (p,) in self.conn.execute("SELECT DISTINCT path FROM chunks"):
                if p not in corpus:
                    self.conn.execute("DELETE FROM chunks WHERE path=?", (p,))
            self.conn.commit()
        if err is None:
            for i, (path, info) in enumerate(corpus.items()):
                with self.lock:
                    self._done = i + 1
                try:
                    self._ensure_file(path, info)
                except Exception:
                    continue  # 单文件失败不中断整体
        self._reload()

    def _ensure_file(self, path, info):
        row = self.conn.execute(
            "SELECT 1 FROM chunks WHERE path=? AND mtime=? AND size=? LIMIT 1",
            (path, info["mtime"], info["size"])).fetchone()
        if row:
            return
        chunks = chunk_text(info.get("text") or "")
        if not chunks:
            with self.lock:
                self.conn.execute("DELETE FROM chunks WHERE path=?", (path,))
                self.conn.commit()
            return
        vecs = embed_texts(chunks)
        if vecs is None:
            return  # Ollama 中途挂了：本文件留待下次加载重建
        with self.lock:
            self.conn.execute("DELETE FROM chunks WHERE path=?", (path,))
            for idx, (c, v) in enumerate(zip(chunks, vecs)):
                self.conn.execute(
                    "INSERT INTO chunks (path, chunk_idx, text, mtime, size, embedding)"
                    " VALUES (?,?,?,?,?,?)",
                    (path, idx, c, info["mtime"], info["size"],
                     np.asarray(v, dtype=np.float32).tobytes()))
            self.conn.commit()

    def _reload(self):
        """把 chunks 表全量加载为归一化内存矩阵。"""
        rows = self.conn.execute(
            "SELECT path, chunk_idx, text, embedding FROM chunks"
            " ORDER BY path, chunk_idx").fetchall()
        meta, vecs = [], []
        for path, idx, text, blob in rows:
            v = np.frombuffer(blob, dtype=np.float32)
            n = float(np.linalg.norm(v))
            if not n:
                continue
            meta.append({"path": path, "chunk_idx": idx, "text": text})
            vecs.append(v / n)
        with self.lock:
            self._meta = meta
            self._matrix = np.vstack(vecs) if vecs else None

    def search(self, kw, top_n=TOP_N):
        """语义检索：查询词向量化后与全部块做余弦相似度，Top N。"""
        with self.lock:
            matrix, meta = self._matrix, self._meta
            if not self._available:
                return {"ok": False, "error": self._error or "语义索引未就绪"}
        if matrix is None or not meta:
            return {"ok": False, "error": "语义索引为空，请先加载文件夹并等索引建完"}
        q = embed_texts([kw])
        if not q:
            return {"ok": False, "error": "Ollama 不可用，语义搜索暂不可用"}
        qv = np.asarray(q[0], dtype=np.float32)
        qn = float(np.linalg.norm(qv))
        if not qn:
            return {"ok": True, "results": []}
        scores = matrix @ (qv / qn)
        order = np.argsort(scores)[::-1][:top_n]
        results = []
        for i in order:
            s = float(scores[i])
            if s < MIN_SCORE:
                break
            m = meta[int(i)]
            results.append({
                "path": m["path"],
                "name": os.path.basename(m["path"]),
                "ext": os.path.splitext(m["path"])[1].lower(),
                "score": round(s, 4),
                "text": m["text"],
            })
        return {"ok": True, "results": results}
