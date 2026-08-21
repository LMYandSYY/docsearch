# 语义搜索 + 打开文件 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 docsearch 增加基于 Ollama bge-m3 的分段语义搜索（同页分栏展示），以及搜索结果「打开目录 / WPS 打开」能力。

**Architecture:** 新增 `semantic.py`（分段 + Ollama embedding + SQLite chunks 表缓存 + 内存 numpy 余弦检索）与 `opener.py`（跨平台打开目录/WPS，降级系统默认应用）；`app.py` 挂 3 个新路由并在加载完成后后台线程建索引；前端加语义分栏、两个打开按钮、索引进度轮询。设计文档：`docs/superpowers/specs/2026-08-20-semantic-search-and-open-file-design.md`

**Tech Stack:** Python 3.9+/Flask/SQLite/numpy/urllib（调 Ollama REST）；原生 JS 前端。

## Global Constraints

- 纯本地运行，监听 `127.0.0.1:8765`，不引入 requests 等新 HTTP 库（用 urllib）
- 新增依赖仅 `numpy`
- Ollama 地址 `http://localhost:11434`、模型 `bge-m3`、分段 500 字、Top 20、相似度阈值 0.3 —— 常量放 `semantic.py` 顶部，不做配置项
- 所有 Ollama 失败必须优雅降级：精确搜索与现有功能零影响
- 代码注释简洁中文；测试用 unittest（项目现有风格，测试文件开头 `import helpers` 引入 sys.path）
- 每个任务完成后 commit

---

### Task 1: numpy 依赖 + `semantic.py` 分段函数

**Files:**
- Create: `semantic.py`
- Modify: `requirements.txt`
- Test: `tests/test_semantic.py`

**Interfaces:**
- Produces: `chunk_text(text, size=500) -> list[str]`；常量 `CHUNK_SIZE=500, OLLAMA_URL, EMBED_MODEL, EMBED_BATCH=16, EMBED_TIMEOUT=30, TOP_N=20, MIN_SCORE=0.3`

- [ ] **Step 1: 安装 numpy 并加入依赖**

```bash
.venv/bin/pip install numpy
```

`requirements.txt` 末尾追加一行：

```text
numpy>=1.26
```

- [ ] **Step 2: 写失败测试** `tests/test_semantic.py`

```python
# -*- coding: utf-8 -*-
"""semantic.py 单测：分段、embedding 调用、索引存储与检索。"""
import helpers  # noqa: F401  触发 sys.path 注入项目根

import semantic


class TestChunkText(unittest.TestCase? None):  pass
```

完整文件（上面两行占位不要，直接用下面全文）：

```python
# -*- coding: utf-8 -*-
"""semantic.py 单测：分段、embedding 调用、索引存储与检索。"""
import helpers  # noqa: F401  触发 sys.path 注入项目根
import unittest

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


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: 跑测试确认失败**

```bash
.venv/bin/python -m unittest tests.test_semantic -v
```

Expected: FAIL/ERROR（`ModuleNotFoundError: No module named 'semantic'`）

- [ ] **Step 4: 写 `semantic.py`**

```python
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
```

- [ ] **Step 5: 跑测试确认通过**

```bash
.venv/bin/python -m unittest tests.test_semantic -v
```

Expected: 5 passed

- [ ] **Step 6: Commit**

```bash
git add semantic.py tests/test_semantic.py requirements.txt
git commit --no-verify -m "feat: semantic.py 分段函数与常量"
```

---

### Task 2: `embed_texts` + `probe_ollama`（Ollama HTTP 调用）

**Files:**
- Modify: `semantic.py`（追加函数）
- Test: `tests/test_semantic.py`（追加测试类）

**Interfaces:**
- Consumes: Task 1 的常量
- Produces: `embed_texts(texts) -> list[list[float]] | None`（批量，任何失败返回 None）；`probe_ollama() -> str | None`（None=可用，str=错误说明）

- [ ] **Step 1: 追加失败测试**（加到 `tests/test_semantic.py`）

```python
import io
import json as json_mod
from unittest import mock


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
```

- [ ] **Step 2: 跑测试确认失败**

```bash
.venv/bin/python -m unittest tests.test_semantic -v
```

Expected: 新增用例 FAIL（`embed_texts`/`probe_ollama` 不存在）

- [ ] **Step 3: `semantic.py` 追加实现**

```python
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
```

- [ ] **Step 4: 跑测试确认通过**

```bash
.venv/bin/python -m unittest tests.test_semantic -v
```

Expected: 全部 passed

- [ ] **Step 5: Commit**

```bash
git add semantic.py tests/test_semantic.py
git commit --no-verify -m "feat: Ollama embedding 调用与可用性探测"
```

---

### Task 3: `SemanticIndex` 存储层（建索引/缓存复用/失效清理）

**Files:**
- Modify: `semantic.py`（追加类）
- Test: `tests/test_semantic.py`（追加测试类）

**Interfaces:**
- Consumes: `chunk_text` / `embed_texts` / `probe_ollama`
- Produces: `SemanticIndex(db_path)`；`ensure(corpus)`（corpus: `dict path -> {"text": str, "mtime": int, "size": int}`）；`status() -> dict`（keys: `available/error/building/done/total/model`）；chunks 表结构 `(path, chunk_idx, text, mtime, size, embedding BLOB)`

- [ ] **Step 1: 追加失败测试**

```python
import os


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
```

- [ ] **Step 2: 跑测试确认失败**

```bash
.venv/bin/python -m unittest tests.test_semantic -v
```

Expected: 新增用例 FAIL（`SemanticIndex` 不存在）

- [ ] **Step 3: `semantic.py` 追加实现**

```python
class SemanticIndex:
    """向量索引：chunks 表缓存 + 内存归一化矩阵。

    ensure() 在后台线程跑；search() 只读内存快照，两者不互相阻塞。
    """

    def __init__(self, db_path):
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
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
                self._pending_corpus = corpus
                return
            self._building = True
            self._done = 0
            self._total = len(corpus)
        try:
            self._ensure_impl(corpus)
        finally:
            with self.lock:
                self._building = False
                pending, self._pending_corpus = self._pending_corpus, None
        if pending:
            self.ensure(pending)

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
```

注意：`_reload()` 在 Task 4 实现；本任务先追加空方法让代码可运行：

```python
    def _reload(self):
        pass  # Task 4 实现：把 chunks 表加载为内存矩阵
```

- [ ] **Step 4: 跑测试确认通过**

```bash
.venv/bin/python -m unittest tests.test_semantic -v
```

Expected: 全部 passed

- [ ] **Step 5: Commit**

```bash
git add semantic.py tests/test_semantic.py
git commit --no-verify -m "feat: SemanticIndex 向量存储与缓存复用"
```

---

### Task 4: `SemanticIndex` 检索（内存矩阵 + 余弦 Top N）

**Files:**
- Modify: `semantic.py`（实现 `_reload`，追加 `search`）
- Test: `tests/test_semantic.py`（追加测试类）

**Interfaces:**
- Consumes: `embed_texts`
- Produces: `search(kw, top_n=20) -> {"ok": True, "results": [{path,name,ext,score,text}]} | {"ok": False, "error": str}`

- [ ] **Step 1: 追加失败测试**

```python
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
```

- [ ] **Step 2: 跑测试确认失败**

```bash
.venv/bin/python -m unittest tests.test_semantic -v
```

Expected: 新增用例 FAIL

- [ ] **Step 3: 实现 `_reload` 与 `search`**（替换 Task 3 的占位 `_reload`）

```python
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
```

- [ ] **Step 4: 跑测试确认通过**

```bash
.venv/bin/python -m unittest tests.test_semantic -v
```

Expected: 全部 passed

- [ ] **Step 5: Commit**

```bash
git add semantic.py tests/test_semantic.py
git commit --no-verify -m "feat: SemanticIndex 余弦检索"
```

---

### Task 5: `opener.py`（打开目录 / WPS 打开，跨平台降级）

**Files:**
- Create: `opener.py`
- Test: `tests/test_opener.py`

**Interfaces:**
- Produces: `open_folder(path) -> {"ok": bool, "error"?: str}`；`open_with_wps(path) -> {"ok": bool, "method": "wps"|"default", "error"?: str}`；`find_wps()`（macOS 返回 app 名 `wpsoffice` 或 None；Windows 返回 office6 目录或 None）

- [ ] **Step 1: 写失败测试** `tests/test_opener.py`

```python
# -*- coding: utf-8 -*-
"""opener.py 单测：全 mock，不真正打开程序。"""
import helpers  # noqa: F401
import os
import unittest
from unittest import mock

import opener


class TestOpenFolder(unittest.TestCase):
    @mock.patch("opener.subprocess.run")
    @mock.patch("opener.platform.system", return_value="Darwin")
    def test_mac(self, _sys, run):
        r = opener.open_folder("/a/b.txt")
        self.assertTrue(r["ok"])
        run.assert_called_once_with(["open", "-R", "/a/b.txt"], timeout=10)

    @mock.patch("opener.subprocess.run")
    @mock.patch("opener.platform.system", return_value="Windows")
    def test_win(self, _sys, run):
        r = opener.open_folder(r"C:\a\b.txt")
        self.assertTrue(r["ok"])
        run.assert_called_once_with(["explorer", "/select,", r"C:\a\b.txt"], timeout=10)

    @mock.patch("opener.subprocess.run", side_effect=Exception("boom"))
    @mock.patch("opener.platform.system", return_value="Darwin")
    def test_fail(self, _sys, run):
        r = opener.open_folder("/a/b.txt")
        self.assertFalse(r["ok"])
        self.assertIn("boom", r["error"])


class TestOpenWithWps(unittest.TestCase):
    @mock.patch("opener.subprocess.run")
    @mock.patch("opener.platform.system", return_value="Darwin")
    @mock.patch("opener.find_wps", return_value="wpsoffice")
    def test_mac_wps(self, _fw, _sys, run):
        r = opener.open_with_wps("/a/b.docx")
        self.assertEqual(r, {"ok": True, "method": "wps"})
        run.assert_called_once_with(["open", "-a", "wpsoffice", "/a/b.docx"], timeout=10)

    @mock.patch("opener.subprocess.run")
    @mock.patch("opener.platform.system", return_value="Darwin")
    @mock.patch("opener.find_wps", return_value=None)
    def test_mac_fallback(self, _fw, _sys, run):
        r = opener.open_with_wps("/a/b.docx")
        self.assertEqual(r, {"ok": True, "method": "default"})
        run.assert_called_once_with(["open", "/a/b.docx"], timeout=10)

    @mock.patch("opener.subprocess.run")
    @mock.patch("opener.os.startfile")
    @mock.patch("opener.platform.system", return_value="Windows")
    @mock.patch("opener._find_wps_win", return_value=None)
    def test_win_fallback(self, _fw, _sys, startfile, run):
        r = opener.open_with_wps(r"C:\a\b.xlsx")
        self.assertEqual(r, {"ok": True, "method": "default"})
        startfile.assert_called_once_with(r"C:\a\b.xlsx")
        run.assert_not_called()

    @mock.patch("opener.subprocess.run")
    @mock.patch("opener.platform.system", return_value="Windows")
    @mock.patch("opener._find_wps_win", return_value=r"C:\WPS\office6")
    @mock.patch("opener.os.path.isfile", return_value=True)
    def test_win_wps_by_ext(self, _isf, _fw, _sys, run):
        r = opener.open_with_wps(r"C:\a\b.xlsx")  # xlsx 应选 et.exe
        self.assertEqual(r, {"ok": True, "method": "wps"})
        run.assert_called_once_with([r"C:\WPS\office6\et.exe", r"C:\a\b.xlsx"], timeout=10)


class TestFindWps(unittest.TestCase):
    @mock.patch("opener.os.path.isdir", side_effect=lambda p: p == "/Applications/wpsoffice.app")
    @mock.patch("opener.platform.system", return_value="Darwin")
    def test_mac_found(self, _sys, _isd):
        self.assertEqual(opener.find_wps(), "wpsoffice")

    @mock.patch("opener.os.path.isdir", return_value=False)
    @mock.patch("opener.platform.system", return_value="Darwin")
    def test_mac_not_found(self, _sys, _isd):
        self.assertIsNone(opener.find_wps())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑测试确认失败**

```bash
.venv/bin/python -m unittest tests.test_opener -v
```

Expected: ERROR（`ModuleNotFoundError: No module named 'opener'`）

- [ ] **Step 3: 写 `opener.py`**

```python
# -*- coding: utf-8 -*-
"""打开文件所在目录 / 用 WPS（找不到则系统默认应用）打开文件。"""
import glob
import os
import platform
import subprocess

# Windows：不同文件类型对应 WPS 不同主程序
_WPS_WIN_EXE = {"wps": "wps.exe", "et": "et.exe", "wpp": "wpp.exe"}
_WPS_EXTS = {".xls": "et", ".xlsx": "et", ".csv": "et", ".ppt": "wpp", ".pptx": "wpp"}


def _find_wps_mac():
    for d in ("/Applications", os.path.expanduser("~/Applications")):
        if os.path.isdir(os.path.join(d, "wpsoffice.app")):
            return "wpsoffice"
    return None


def _find_wps_win():
    """返回 WPS 的 office6 目录（内含 wps/et/wpp.exe），找不到返回 None。"""
    roots = [
        os.path.expandvars(r"%LOCALAPPDATA%\Kingsoft\WPS Office"),
        os.path.expandvars(r"%ProgramFiles%\Kingsoft\WPS Office"),
        os.path.expandvars(r"%ProgramFiles(x86)%\Kingsoft\WPS Office"),
    ]
    for root in roots:
        if not os.path.isdir(root):
            continue
        for d in sorted(glob.glob(os.path.join(root, "*", "office6"))):
            if os.path.isfile(os.path.join(d, "wps.exe")):
                return d
    return None


def find_wps():
    if platform.system() == "Windows":
        return _find_wps_win()
    return _find_wps_mac()


def _wps_exe_for(path):
    d = _find_wps_win()
    if not d:
        return None
    kind = _WPS_EXTS.get(os.path.splitext(path)[1].lower(), "wps")
    exe = os.path.join(d, _WPS_WIN_EXE[kind])
    return exe if os.path.isfile(exe) else os.path.join(d, "wps.exe")


def open_folder(path):
    """在文件管理器中定位该文件。返回 {ok} 或 {ok:False, error}。"""
    try:
        sys_name = platform.system()
        if sys_name == "Darwin":
            subprocess.run(["open", "-R", path], timeout=10)
        elif sys_name == "Windows":
            # explorer 成功时也常返回 1，不判返回码
            subprocess.run(["explorer", "/select,", path], timeout=10)
        else:
            subprocess.run(["xdg-open", os.path.dirname(path) or "."], timeout=10)
        return {"ok": True}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": repr(e)}


def open_with_wps(path):
    """用 WPS 打开；没装 WPS 退回系统默认应用。返回 {ok, method}。"""
    try:
        sys_name = platform.system()
        if sys_name == "Darwin":
            if find_wps():
                subprocess.run(["open", "-a", "wpsoffice", path], timeout=10)
                return {"ok": True, "method": "wps"}
            subprocess.run(["open", path], timeout=10)
            return {"ok": True, "method": "default"}
        if sys_name == "Windows":
            exe = _wps_exe_for(path)
            if exe:
                subprocess.run([exe, path], timeout=10)
                return {"ok": True, "method": "wps"}
            os.startfile(path)  # noqa: S606
            return {"ok": True, "method": "default"}
        subprocess.run(["xdg-open", path], timeout=10)
        return {"ok": True, "method": "default"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": repr(e)}
```

- [ ] **Step 4: 跑测试确认通过**

```bash
.venv/bin/python -m unittest tests.test_opener -v
```

Expected: 全部 passed

- [ ] **Step 5: Commit**

```bash
git add opener.py tests/test_opener.py
git commit --no-verify -m "feat: opener 打开目录与 WPS 打开"
```

---

### Task 6: `indexer.py` 记录 mtime/size + `corpus()` 快照

**Files:**
- Modify: `indexer.py`（cache 条目加字段；新增 `corpus()`）
- Test: `tests/test_indexer.py`（追加测试）

**Interfaces:**
- Produces: `Indexer.corpus() -> dict path -> {"text": str, "mtime": int, "size": int}`；`indexer.cache[p]` 新增 `"mtime"`/`"size"` 键

- [ ] **Step 1: 追加失败测试**（加到 `tests/test_indexer.py`，沿用该文件已有的建目录/写文件方式；若已有基础夹具函数直接复用）

```python
class TestCorpus(unittest.TestCase):
    def test_corpus_snapshot(self):
        import os
        tmp = helpers.make_tmpdir()
        try:
            f = os.path.join(tmp, "a.txt")
            helpers.write_txt(f, "遥控失败 相关内容")
            idx = Indexer(os.path.join(tmp, "c.db"))
            count, _ = idx.load_folders([tmp], {".txt"}, ocr=False)
            self.assertEqual(count, 1)
            c = idx.corpus()
            self.assertIn(f, c)
            st = os.stat(f)
            self.assertEqual(c[f]["mtime"], int(st.st_mtime))
            self.assertEqual(c[f]["size"], st.st_size)
            self.assertIn("遥控失败", c[f]["text"])
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)
```

注意：测试文件顶部按现有风格确保 `import helpers`、`from indexer import Indexer` 已存在；缺哪个补哪个。

- [ ] **Step 2: 跑测试确认失败**

```bash
.venv/bin/python -m unittest tests.test_indexer -v
```

Expected: 新用例 FAIL（`corpus` 不存在）

- [ ] **Step 3: 修改 `indexer.py`**

`_load_folders_impl` 中 `self.cache[p] = {...}` 改为：

```python
                self.cache[p] = {
                    "name": os.path.basename(p),
                    "ext": ext,
                    "text": text or "",
                    "meta": meta,
                    "mtime": int(st.st_mtime),
                    "size": st.st_size,
                }
```

`Indexer` 类内追加方法（放在 `search` 前）：

```python
    def corpus(self):
        """语义索引用的语料快照：path -> {text, mtime, size}。"""
        with self.lock:
            return {p: {"text": i["text"], "mtime": i["mtime"], "size": i["size"]}
                    for p, i in self.cache.items()}
```

- [ ] **Step 4: 跑全部 indexer 测试确认通过（含旧用例不回归）**

```bash
.venv/bin/python -m unittest tests.test_indexer -v
```

Expected: 全部 passed

- [ ] **Step 5: Commit**

```bash
git add indexer.py tests/test_indexer.py
git commit --no-verify -m "feat: indexer 提供 corpus 快照供语义索引用"
```

---

### Task 7: `app.py` 路由与后台建索引接线

**Files:**
- Modify: `app.py`
- Test: `tests/test_api_semantic.py`（新建）

**Interfaces:**
- Consumes: `SemanticIndex.ensure/search/status`（Task 3/4）、`opener.open_folder/open_with_wps`（Task 5）、`indexer.corpus()`（Task 6）
- Produces: 路由 `GET /api/semantic_search?kw=`、`GET /api/semantic_status`、`POST /api/open {path, mode}`；全局 `semantic` 实例（与 indexer 同用 `cache.db`）

- [ ] **Step 1: 写失败测试** `tests/test_api_semantic.py`

```python
# -*- coding: utf-8 -*-
"""语义/打开接口路由测试：全 mock，不真正调 Ollama、不真正打开程序。"""
import helpers  # noqa: F401
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
```

注意：`app.py` 里须以 `import opener` 方式引入（`mock.patch("opener.open_with_wps")` 才能命中路由内的调用点）。

- [ ] **Step 2: 跑测试确认失败**

```bash
.venv/bin/python -m unittest tests.test_api_semantic -v
```

Expected: FAIL（404，路由不存在）

- [ ] **Step 3: 修改 `app.py`**

顶部 import 区追加：

```python
import opener
from semantic import SemanticIndex
```

全局实例区（`indexer = Indexer(...)` 之后）追加：

```python
semantic = SemanticIndex(os.path.join(BASE, "cache.db"))  # 与 indexer 共用 cache.db
```

`api_load` 中 `save_settings(folders, ocr)` 之后、`return` 之前插入：

```python
    # 精确索引已就绪即可搜索；语义向量放后台线程补建（文件没变会复用缓存）
    threading.Thread(target=semantic.ensure, args=(indexer.corpus(),), daemon=True).start()
```

文件末尾（`preview` 路由之后）追加三个路由：

```python
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
```

- [ ] **Step 4: 跑测试确认通过**

```bash
.venv/bin/python -m unittest tests.test_api_semantic -v
```

Expected: 全部 passed

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_api_semantic.py
git commit --no-verify -m "feat: 语义搜索/状态/打开文件三个路由"
```

---

### Task 8: 前端（语义分栏 + 打开按钮 + 进度轮询）

**Files:**
- Modify: `templates/index.html`、`static/app.js`、`static/style.css`

**Interfaces:**
- Consumes: Task 7 的三个 API
- Produces: 无代码接口；页面行为变更

- [ ] **Step 1: `templates/index.html`**

`</main>` 之前、`<div id="empty">` 之后追加语义分栏：

```html
    <section id="semanticSection" class="semantic hidden">
      <div class="sem-head">
        <h2>语义相关</h2>
        <span id="semStatus" class="meta"></span>
      </div>
      <ul id="semResults" class="results"></ul>
      <div id="semEmpty" class="empty"></div>
    </section>
```

`<div id="modal"` 之前追加 toast 容器：

```html
  <div id="toast" class="toast hidden"></div>
```

- [ ] **Step 2: `static/style.css` 末尾追加**

```css
/* 语义搜索分栏 */
.semantic { margin-top: 18px; }
.sem-head { display: flex; align-items: baseline; gap: 10px; margin-bottom: 8px; }
.semantic h2 { font-size: 16px; margin: 0; }
#semanticSection.hidden, .toast.hidden { display: none; }

/* 打开结果 toast */
.toast { position: fixed; left: 50%; bottom: 28px; transform: translateX(-50%);
  background: #2b2f36; color: #fff; padding: 8px 16px; border-radius: 8px;
  font-size: 13px; z-index: 200; box-shadow: 0 4px 14px rgba(0,0,0,.25); }
```

- [ ] **Step 3: `static/app.js`**

3a. 元素引用行（`const modal = ...` 一行之后）追加：

```js
const semSection = $('#semanticSection'), semStatusEl = $('#semStatus'),
      semResultsEl = $('#semResults'), semEmptyEl = $('#semEmpty');
let semPollTimer = null;
```

3b. 原 `doSearch` 整体替换为下面三个函数（原精确逻辑改名 `doSearchExact` 并接收 kw 参数）：

```js
async function doSearch() {
  const kw = kwIn.value.trim();
  if (!kw) {
    resultsEl.innerHTML = ''; countEl.textContent = '';
    emptyEl.style.display = 'block';
    emptyEl.textContent = '输入关键词开始搜索。';
    semSection.classList.add('hidden');
    return;
  }
  doSearchExact(kw);
  doSearchSemantic(kw);
}

async function doSearchExact(kw) {
  const r = await fetch('/api/search?kw=' + encodeURIComponent(kw));
  const d = await r.json();
  countEl.textContent = '找到 ' + d.length + ' 个文档';
  if (!d.length) {
    resultsEl.innerHTML = '';
    emptyEl.style.display = 'block';
    emptyEl.textContent = '没有文档包含「' + kw + '」。';
    return;
  }
  emptyEl.style.display = 'none';
  resultsEl.innerHTML = d.map((it) => {
    const dir = it.path.replace(/[\\/][^\\/]+$/, '');
    return `
    <li>
      <div class="row1">
        <span class="name">${esc(it.name)}</span>
        <span class="badge">${esc(it.ext)}</span>
        ${it.pages ? `<span class="meta">${it.pages}页</span>` : ''}
        ${it.ocr_used ? `<span class="badge ocr">OCR</span>` : ''}
        <span class="meta">命中 ${it.count} 次</span>
        <button class="prev" data-path="${esc(it.path)}">预览原文</button>
        <button class="prev opendir" data-path="${esc(it.path)}">打开目录</button>
        <button class="prev openwps" data-path="${esc(it.path)}">WPS打开</button>
      </div>
      <div class="dir" title="${esc(it.path)}">${esc(dir)}</div>
      <div class="snips">${it.snippets.map((s) => `<div class="snip">${highlight(s, kw)}</div>`).join('')}</div>
    </li>`;
  }).join('');
  resultsEl.querySelectorAll('.prev').forEach((btn) => {
    btn.onclick = () => openPreview(btn.dataset.path, kw);
  });
}

async function doSearchSemantic(kw) {
  semSection.classList.remove('hidden');
  semStatusEl.textContent = '搜索中…';
  try {
    const d = await (await fetch('/api/semantic_search?kw=' + encodeURIComponent(kw))).json();
    if (!d.ok) {
      semResultsEl.innerHTML = '';
      semStatusEl.textContent = '';
      semEmptyEl.style.display = 'block';
      semEmptyEl.textContent = d.error || '语义搜索不可用（精确搜索不受影响）';
      return;
    }
    semStatusEl.textContent = d.results.length ? '相关段落 ' + d.results.length + ' 条（按相似度排序）' : '';
    if (!d.results.length) {
      semResultsEl.innerHTML = '';
      semEmptyEl.style.display = 'block';
      semEmptyEl.textContent = '没有语义相关的段落。';
      return;
    }
    semEmptyEl.style.display = 'none';
    semResultsEl.innerHTML = d.results.map((it) => {
      const dir = it.path.replace(/[\\/][^\\/]+$/, '');
      return `
      <li>
        <div class="row1">
          <span class="name">${esc(it.name)}</span>
          <span class="badge">${esc(it.ext)}</span>
          <span class="meta">相似度 ${Math.round(it.score * 100)}%</span>
          <button class="prev" data-path="${esc(it.path)}">预览原文</button>
          <button class="prev opendir" data-path="${esc(it.path)}">打开目录</button>
          <button class="prev openwps" data-path="${esc(it.path)}">WPS打开</button>
        </div>
        <div class="dir" title="${esc(it.path)}">${esc(dir)}</div>
        <div class="snips"><div class="snip">${esc(it.text)}</div></div>
      </li>`;
    }).join('');
    semResultsEl.querySelectorAll('.prev:not(.opendir):not(.openwps)').forEach((btn) => {
      btn.onclick = () => openPreview(btn.dataset.path, kw);
    });
  } catch (e) {
    semStatusEl.textContent = '语义搜索出错：' + e;
  }
}
```

说明：语义段落不做关键词高亮（同义词命中无字面对应，高亮会误导），整段展示原文即可。

3c. 文件末尾（初始化 IIFE 之前）追加打开文件与轮询逻辑：

```js
async function openPath(path, mode) {
  try {
    const r = await fetch('/api/open', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path, mode }),
    });
    const d = await r.json();
    if (d.ok) {
      toast(d.method === 'default' ? '未找到 WPS，已用系统默认应用打开' : '已打开');
    } else {
      toast(d.error || '打开失败');
    }
  } catch (e) {
    toast('打开出错：' + e);
  }
}

// 事件委托：精确与语义两栏的打开按钮统一处理
document.addEventListener('click', (e) => {
  const b = e.target.closest('button');
  if (!b) return;
  if (b.classList.contains('opendir')) openPath(b.dataset.path, 'folder');
  else if (b.classList.contains('openwps')) openPath(b.dataset.path, 'wps');
});

function toast(msg) {
  const t = $('#toast');
  t.textContent = msg;
  t.classList.remove('hidden');
  clearTimeout(toast._t);
  toast._t = setTimeout(() => t.classList.add('hidden'), 2500);
}

function pollSemantic() {
  clearInterval(semPollTimer);
  const tick = async () => {
    try {
      const d = await (await fetch('/api/semantic_status')).json();
      if (d.building) {
        semSection.classList.remove('hidden');
        semStatusEl.textContent = '语义索引构建中：' + d.done + '/' + d.total;
      } else {
        clearInterval(semPollTimer);
        semStatusEl.textContent = d.available ? '语义索引就绪' : (d.error || '语义搜索不可用');
      }
    } catch (e) { /* 轮询失败忽略，下个周期重试 */ }
  };
  tick();
  semPollTimer = setInterval(tick, 2000);
}
```

注意事件委托与 3b 中 `.prev` 的 onclick 绑定并存：`opendir`/`openwps` 按钮也有 `prev` class，但 3b 里 onclick 只绑「预览原文」。为避免误绑，精确栏的绑定选择器改为同样排除：

```js
  resultsEl.querySelectorAll('.prev:not(.opendir):not(.openwps)').forEach((btn) => {
    btn.onclick = () => openPreview(btn.dataset.path, kw);
  });
```

（上面 doSearchExact 代码块里已是排除写法，保持一致即可。）

3d. `loadAll()` 成功分支（`kwIn.focus();` 之前）追加：

```js
    pollSemantic();
```

- [ ] **Step 4: 手工验证页面**

```bash
.venv/bin/python app.py
```

浏览器开 `http://127.0.0.1:8765`：
- 加载既有路径后，语义区状态条出现「语义索引构建中：x/y」并最终变「语义索引就绪」（首次真实建库需几分钟）
- 输入关键词：上半区精确结果、下半区语义段落都出现；两栏按钮齐全
- 点「打开目录」→ Finder 定位；点「WPS打开」→ WPS 打开；toast 提示正常
- 浏览器 Console 无 JS 报错

- [ ] **Step 5: Commit**

```bash
git add templates/index.html static/app.js static/style.css
git commit --no-verify -m "feat: 前端语义分栏与打开目录/WPS按钮"
```

---

### Task 9: README 更新 + 全量回归 + 端到端验证

**Files:**
- Modify: `README.md`

**Interfaces:** 无

- [ ] **Step 1: README 更新**

「功能」列表追加：

```markdown
- 支持 **语义搜索**：输入「遥控失败」也能找到写着「遥控不成功」的段落（本机 Ollama + bge-m3 向量匹配）。
- 支持 **打开文件**：结果可一键在文件管理器中定位，或用本机 WPS 打开（未装 WPS 时用系统默认应用）。
```

「环境要求」追加：

```markdown
- **Ollama + bge-m3**：仅语义搜索需要；普通关键词搜索不需要。
  安装 Ollama 后执行 `ollama pull bge-m3`（约 1.2GB）。语义搜索全程本机完成，文档不出机器。
```

「常见问题」追加：

```markdown
### 语义搜索提示 Ollama 不可用

先确认 Ollama 已启动（菜单栏图标或 `ollama serve`），且模型已拉取：`ollama pull bge-m3`。
Ollama 不可用时只影响语义搜索，关键词精确搜索不受影响。

### 语义索引很慢

首次加载需要对每个文档分段并计算向量，文档多时需要几分钟；之后文件没变会复用缓存，只有新增或修改的文件要补建。
```

- [ ] **Step 2: 全量单测回归**

```bash
.venv/bin/python -m unittest discover -s tests
```

Expected: 全部 passed（旧用例无回归）

- [ ] **Step 3: 端到端真实验证（真实数据）**

```bash
.venv/bin/python app.py
```

1. 打开 `http://127.0.0.1:8765`，确认自动加载 `/Users/lmy/Documents/Work`
2. `curl -s http://127.0.0.1:8765/api/semantic_status` 观察建索引进度直至 `building: false, available: true`
3. 页面搜「遥控失败」：下半区应出现语义相关段落（如包含「遥控不成功/遥控异常」的文档段落），相似度百分比合理
4. 点语义结果「预览原文」→ 预览弹窗正常；「打开目录」→ Finder 定位到该文件；「WPS打开」→ WPS 打开
5. 精确搜索结果同样测试两个打开按钮
6. 降级验证（需用户同意后执行）：`pkill ollama` → 再搜 → 语义区显示「无法连接 Ollama…」，精确搜索正常；验证完 `ollama serve &` 恢复（后台模式：`nohup ollama serve >/dev/null 2>&1 &`）
7. 重启服务再次加载 → 语义索引秒建（缓存复用），`done/total` 快速走完

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit --no-verify -m "docs: 语义搜索与打开文件使用说明"
```

---

## Self-Review 记录

- 规格覆盖：分段/建索引后台/缓存复用/失效清理（Task 3）、余弦 Top20 阈值 0.3（Task 4）、三平台打开与降级（Task 5）、corpus 快照（Task 6）、三路由（Task 7）、分栏+按钮+轮询+toast（Task 8）、README/回归/e2e（Task 9）✓
- 无占位符；类型/签名跨任务一致（`ensure(corpus)`、`search` 返回结构、`opener` 返回结构在 Task 7 前端/路由中一致）✓
- 设计文档中「是否已被精确匹配命中」字段改为前端可自行对照两栏 path 实现（后端不传，避免接口耦合）——有意简化
