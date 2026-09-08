# docsearch 项目上下文（会话自动加载）

本地文档全文检索工具：对指定文件夹内 PDF/Word/Excel/文本等做关键词 + 语义（RAG）检索，Flask 单机服务 `http://127.0.0.1:8765`。
远程仓库 `git@github.com:LMYandSYY/docsearch.git`（main 与 origin 同步，最新 `6769073`）。

## 架构（纯 Python，无前端框架，jQuery 风格原生 JS）

| 文件 | 职责 |
|---|---|
| `app.py` | Flask 路由：搜索/语义搜索/加载/设置/打开文件/进度轮询 |
| `extractor.py` | 各格式文本提取（PDF 含 OCR，doc/xls/ppt 走 LibreOffice） |
| `indexer.py` | SQLite `files` 表 + corpus 快照（mtime/size 判断缓存复用） |
| `semantic.py` | 语义搜索：~500 字分段 → Ollama bge-m3 embedding → SQLite `chunks` 表 → numpy 余弦 Top20（阈值 0.3），后台线程建索引 |
| `opener.py` | 打开目录（Finder/explorer）/ WPS 打开（无 WPS 退回系统默认） |
| `static/` `templates/` | 单页搜索界面：上半区精确匹配，下半区语义相关 |

- 依赖：`requirements.txt`；语义搜索仅额外需 `numpy` + 本机 Ollama（`ollama pull bge-m3`）
- `cache.db`（SQLite WAL + busy_timeout）同时存 files 与 chunks；**settings.json 被 .gitignore，属本地配置，改动不进 git**

## 开发历程（按会话 jsonl 汇总，2026-08-20 ~ 08-23）

1. **主开发（8/20-22）**：需求「RAG 语义搜索（搜"遥控失败"能找到"遥控不成功"）+ 打开目录/WPS 打开」
   - brainstorming 定方案一：本地余弦相似度（零外部服务，仅依赖 Ollama embedding）
   - 设计/计划文档在 `docs/superpowers/specs|plans/` 下，按 9 任务 TDD + subagent 执行
   - 终审修复 2 个真问题：SQLite 双连接并发写锁死（WAL+busy_timeout）、corpus 竞态（ensure() 改循环）
   - e2e 验证：真实 Work 目录 1578 文档 0 错误、建索引中精确/语义搜索均可用、WPS/Finder 真实拉起、单测 87/87
   - 12 个 commit squash 为 `6769073` 合回 main 并推送
2. **配置修正（8/22-23）**：默认路径改 `settings.json → folders: ["/Users/lmy/Documents/Work"]`（ocr: true）
3. 同目录另一会话为无关问题（电网路由 + CC Switch 配 GLM），与本项目无关

## 约定与现状

- 启动：`bash run.sh`（自动建 .venv）；macOS 有 DocSearch.app 双击启动
- 测试：`pytest tests/`（87 个，全绿基准）；改完代码需真实数据 e2e 验证，不只跑单测
- 语义索引首次建库慢：1578 文档约 1-2 小时（逐批调 Ollama），建完后 mtime/size 未变则秒建
- Ollama 不可用时优雅降级：语义区提示错误，精确搜索不受影响
- 遗留待办（可选）：`.gitignore` 可补 `.idea/`、`cache.db-shm`、`cache.db-wal`
