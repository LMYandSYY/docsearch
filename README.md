# 文档全文检索工具

一个轻量的本地文档检索工具，用于对指定文件夹中的 **PDF**、**Word**、**文本文件** 做全文检索。输入关键词后，页面会展示命中文档、命中次数、上下文片段，并支持打开原文预览。

项目默认只在本机运行，服务地址为 **http://127.0.0.1:8765**。

## 功能

- 支持 **PDF**：文本型 PDF 直接提取，扫描件可启用 **OCR**。
- 支持 **Word**：`.docx` 和 `.doc`。
- 支持 **文本文件**：`.txt`、`.md`、`.markdown`、`.csv`、`.log`。
- 支持 **多路径加载**：可以同时添加多个文件夹，统一检索。
- 支持 **中文关键词搜索**：按子串匹配，结果按命中次数排序。
- 支持 **命中片段高亮**：每个结果展示最多 3 段上下文。
- 支持 **原文预览**：PDF 使用浏览器原生预览，Word 和文本转为 HTML 预览。
- 使用 **SQLite 缓存**：文件未变化时复用解析结果，减少重复提取。

## 环境要求

- **Python 3.9+**
- **Tesseract**：仅扫描件 PDF OCR 需要；普通 PDF、Word、文本文件不需要。
- **LibreOffice**：可选，仅用于 `.doc` 文件更完整的富文本预览。

### macOS 可选依赖

```bash
brew install tesseract tesseract-lang
brew install --cask libreoffice
```

### Windows 可选依赖

- **Python**：从 https://www.python.org/downloads/ 安装，安装时勾选 **Add to PATH**。
- **Tesseract**：可使用 UB Mannheim 版本，安装时勾选 **Chinese Simplified** 语言包。
- **LibreOffice**：从官网下载安装。

## 启动

### macOS / Linux

```bash
cd docsearch
bash run.sh
```

首次启动时，脚本会自动创建 **.venv** 并安装依赖。

#### macOS 一键启动

项目根目录下有 **DocSearch.app**，双击即可：

- 首次启动自动准备 **.venv** 并打开浏览器（地址 `http://127.0.0.1:8765`）。
- 服务在后台运行；再次双击若服务已存在，则只聚焦浏览器，不重复启动。
- 运行日志写入项目根的 `run.log`；停止服务可在「活动监视器」中结束 `Python` 进程，或执行 `pkill -f app.py`。
- 注意：`DocSearch.app` 需放在**项目根目录**下才能找到 `app.py` 与 `.venv`。

### Windows

双击 `run.bat`，或在命令行执行：

```bat
cd /d docsearch
run.bat
```

启动成功后会看到类似输出：

```text
* Running on http://127.0.0.1:8765
```

浏览器未自动打开时，手动访问：

```text
http://127.0.0.1:8765
```

## 使用方法

1. 在页面顶部输入 **文件夹路径**，点击 **添加路径**。
2. 可以添加多个路径，搜索会跨路径合并结果。
3. 如需检索扫描件 PDF，勾选 **扫描件 OCR**。
4. 输入关键词后，页面会自动搜索。
5. 点击 **预览原文** 可查看完整文档并高亮关键词。
6. 再次打开时会读取上次保存的路径配置。

## 测试

```bash
cd docsearch
.venv/bin/python -m unittest discover -s tests
```

Windows 可使用：

```bat
.venv\Scripts\python -m unittest discover -s tests
```

## 目录结构

```text
docsearch/
├── app.py            # Flask 服务与路由
├── extractor.py      # 文档文字提取
├── indexer.py        # 文件扫描、缓存、检索
├── templates/        # 页面模板
├── static/           # 前端脚本与样式
├── tests/            # 单元测试
├── requirements.txt  # Python 依赖
├── run.sh            # macOS / Linux 启动脚本
├── run.bat           # Windows 启动脚本
├── DocSearch.app     # macOS 双击启动（放在项目根目录）
└── README.md         # 项目说明
```

## 本地文件说明

以下文件属于本地运行产物，已通过 `.gitignore` 忽略，不建议提交到仓库：

- **.venv/**：Python 虚拟环境。
- **cache.db**：文档解析缓存。
- **settings.json**：本机路径配置。
- **__pycache__/**：Python 字节码缓存。

## 常见问题

### 端口被占用

项目默认使用 **8765** 端口。若被占用，可修改 `app.py` 中的 `PORT`。

### 扫描件 PDF 搜不到

确认已安装 **Tesseract** 和中文语言包，并在页面勾选 **扫描件 OCR**。

### .doc 预览效果不完整

`.doc` 默认会尽量提取纯文本用于搜索和预览。若需要更完整的富文本预览，可安装 **LibreOffice**，或将文件另存为 `.docx`。

### 跨系统复制后启动失败

`.venv` 不能在 macOS 和 Windows 之间通用。复制项目到另一台机器后，如启动失败，删除 `.venv` 后重新运行启动脚本。
