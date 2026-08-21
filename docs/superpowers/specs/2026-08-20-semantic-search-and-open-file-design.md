# 设计文档：语义搜索（RAG 向量匹配）+ 打开文件/目录

日期：2026-08-20
状态：已与用户确认（采访 6 问 + 方案一批准）

## 1. 背景与目标

docsearch 目前只支持子串精确匹配。用户（电力行业工程师）经常遇到：
搜「遥控失败」，文档里实际写的是「遥控不成功」「遥调异常」——精确匹配搜不到。

本次新增两个能力：

1. **语义搜索**：输入关键词，通过向量相似度找到语义相关的文档段落（如「遥控失败」→「遥控不成功」的段落）。
2. **打开文件**：搜索结果可一键打开所在目录（Finder/资源管理器定位）或用本机 WPS 打开（无 WPS 退回系统默认应用）。

## 2. 需求决定（采访结论）

| 项 | 决定 |
|---|---|
| 向量模型 | 本机 Ollama + bge-m3（用户机器已装、模型已拉、1024 维） |
| 匹配粒度 | 分段匹配（约 500 字/段），结果展示相关段落 |
| 搜索交互 | 同页分栏：上方精确匹配，下方语义相关 |
| Ollama 不可用 | 优雅降级：语义区显示提示，精确搜索不受影响 |
| 打开文件 | 「打开目录」+「WPS打开」两按钮；无 WPS 用系统默认应用 |
| 建索引时机 | 加载完成后后台线程建，期间精确搜索可用，有进度提示 |

## 3. 环境现状（已验证）

- Ollama 服务运行中，`bge-m3:latest` 可用，`http://localhost:11434`，capabilities 含 embedding
- WPS 已装：`/Applications/wpsoffice.app`
- 本机：macOS（Windows 分支同步实现，用户另有 Windows 机器使用本项目）

## 4. 架构设计

### 4.1 新模块 `semantic.py`（核心新增，约 200 行）

职责：分段、调 Ollama embedding、向量缓存与相似度检索。与 `indexer.py` 解耦，通过 path 引用文件。

```
chunk_text(text, size=500) -> list[str]        # 按段落边界切块，长段硬切
embed_texts(texts) -> list[list[float]] | None # 调 Ollama /api/embed，失败返回 None
SemanticIndex:                                  # 向量库管理
    ensure(folders_files)      # 后台建索引（只处理新增/变更文件）
    search(kw, top_n)          # 查询向量 -> 余弦相似度 -> Top N 段落
    status()                   # {available, model, pending, done, total}
```

**分段规则**：按 `\n` 切段，累积到 ≥500 字成块；单段超 800 字按 500 硬切（带 50 字重叠可选，首版不做重叠，简单优先）。

**Ollama 调用**：`POST /api/embed`，body `{"model": "bge-m3", "input": [texts]}`（批量，一次最多 16 段），超时 30s，urllib 实现（不加 requests 依赖）。

**向量存储**：新表 `chunks`（与现有 `files` 表同库 cache.db）：

```sql
CREATE TABLE IF NOT EXISTS chunks (
    path TEXT, chunk_idx INTEGER, text TEXT,
    mtime INTEGER, size INTEGER,
    embedding BLOB,            -- 1024*4 bytes float32 little-endian
    PRIMARY KEY (path, chunk_idx)
);
```

- 建索引前按 `(path, mtime, size)` 判断：库里已有相同 mtime+size 的块则跳过该文件
- 文件变更后重建：`DELETE FROM chunks WHERE path=?` 再插入新块
- **不设 CACHE_VERSION**：分段与维度由代码常量控制，若未来改分段规则，删 cache.db 即可（缓存可随时丢弃，与现有理念一致）

**检索**：启动时/建索引后把全部块向量加载到内存 `numpy` 矩阵（几百文档 × 几千块 × 1024 维 ≈ 几十 MB，可接受）；查询词向量化后矩阵点积 + 归一化余弦，Top 20，阈值 0.3 以下丢弃。

**降级**：任何 Ollama 调用失败（连接拒绝/模型不存在/超时）→ `status.available=False`，search 返回空并带原因；前端显示提示条。

### 4.2 `app.py` 新增路由

| 路由 | 方法 | 作用 |
|---|---|---|
| `/api/semantic_search?kw=` | GET | 调 `SemanticIndex.search`，返回段落结果（path/name/ext/相似度/段落文本/是否已被精确匹配命中） |
| `/api/semantic_status` | GET | 建索引进度与可用性（前端轮询） |
| `/api/open` | POST | body `{path, mode}`，mode=`folder`\|`wps`；返回 `{ok, method}`（method 说明实际用了哪种方式打开） |

**打开文件实现**（新文件 `opener.py`，约 60 行）：

- `open_folder(path)`：
  - macOS: `open -R <path>`（Finder 定位选中）
  - Windows: `explorer /select,<path>`
  - Linux: `xdg-open <dir>`
- `open_with_wps(path)`：
  - 找 WPS：macOS 检查 `/Applications/wpsoffice.app`（及 `~/Applications`）；Windows 查注册表/常见安装路径
  - 找到：macOS `open -a wpsoffice <path>`；Windows 用 WPS 的 exe 路径启动
  - 找不到：退回系统默认（macOS `open`，Windows `os.startfile`），返回 `method: "default"` 告知前端
- 所有分支 `subprocess.run(..., timeout=10)`，异常捕获返回 `{ok: false, error}`

### 4.3 前端改动

**index.html**：
- 结果区下方加语义结果区：`<div id="semanticSection">`（标题「语义相关」+ 状态条 + `<ul id="semanticResults">`）
- 每条结果（精确+语义两栏都加）加两个按钮：「打开目录」「WPS打开」

**app.js**：
- `doSearch()` 改为并行触发 `doSearchExact()`（原逻辑）+ `doSearchSemantic()`（新）
- `doSearchSemantic()`：`fetch /api/semantic_search` → 渲染段落卡片（相似度百分比 + 高亮原文段落，不高亮同义词——语义匹配无字面对应，高亮会误导，展示原文即可）
- 建索引期间轮询 `/api/semantic_status` 显示进度（「语义索引：12/48」），建完自动停止轮询
- 新增 `openPath(path, mode)`：POST `/api/open`，返回结果 toast 提示（无 WPS 时提示「已用系统默认应用打开」）

**style.css**：语义区卡片样式 + 按钮样式（轻量，与现有风格一致）。

### 4.4 数据流

```
添加路径/重新加载
  └─ indexer.load_folders()        （照旧，精确搜索立即可用）
       └─ 完成后 app.py 触发 semantic.ensure(file_list)  后台线程
            ├─ 逐文件：mtime+size 未变 → 跳过
            ├─ chunk_text() → embed_texts() → 写 chunks 表
            └─ 完成后重载内存向量矩阵，status.available=True

输入关键词
  ├─ /api/search          → 精确结果（上半区）
  └─ /api/semantic_search → 查询向量 → 余弦 Top20 → 下半区
       └─ 每条含 path，可「预览原文 / 打开目录 / WPS打开」
```

## 5. 错误处理汇总

| 场景 | 行为 |
|---|---|
| Ollama 未启动 | 语义区显示「Ollama 未运行，语义搜索不可用（精确搜索不受影响）」 |
| bge-m3 不在 | 同上，提示 `ollama pull bge-m3` |
| 建索引中 Ollama 挂了 | 已建部分保留，status 标记失败原因，下次加载重试 |
| 单文件 embedding 失败 | 跳过该文件，记入 errors 列表，不中断整体 |
| WPS 未装 | 退回系统默认应用打开，前端提示实际方式 |
| 打开命令失败 | 返回 `{ok:false, error}`，前端 toast 显示 |
| 文件在结果出来后被删/移动 | 打开接口返回 404 风格错误提示 |

## 6. 测试方案

**单元测试**（`tests/test_semantic.py`、`tests/test_opener.py`）：

- `chunk_text`：短文一段成一块；多段按 500 聚合；超长段硬切
- embed mock：mock urllib 返回固定向量，验证批量调用与 None 降级
- `SemanticIndex`：SQLite 读写、mtime 变更后重建、检索排序（构造已知向量）
- opener：mock subprocess，验证三平台命令参数与降级逻辑（实际命令在真机验证）

**端到端验证（真实数据）**：

1. 启动服务，加载 `/Users/lmy/Documents/Work`（真实文档）
2. 观察语义索引进度条走完
3. 搜「遥控失败」：确认下半区出现语义相关段落（如「遥控不成功」文档）
4. 点「打开目录」→ Finder 弹出定位
5. 点「WPS打开」→ WPS 打开该文件
6. 停 Ollama（`pkill ollama`）再搜 → 语义区显示提示，精确搜索正常
7. 跑全部单测：`.venv/bin/python -m unittest discover -s tests`

## 7. 依赖与配置变更

- `requirements.txt`：加 `numpy`
- `settings.json`：无新增字段（Ollama 地址用默认 `http://localhost:11434`，模型名 `bge-m3` 常量放 `semantic.py` 顶部，不做成配置——轻量优先）

## 8. 不做的事（YAGNI）

- 不做向量数据库（sqlite-vec/faiss/chromadb）——几百文档内存余弦足够
- 不做分段重叠、查询改写、rerank——首版够用
- 不做语义搜索结果分页——Top 20 内
- 不做 Windows 真机自动化测试——代码按平台分支写好，用户 Windows 机器上自行验证
- 不高亮语义结果里的「同义词」——无字面对应，高亮会误导
