const $ = (s) => document.querySelector(s);
const folderInput = $('#folderInput'), addBtn = $('#addBtn'), reloadBtn = $('#reloadBtn'), ocrIn = $('#ocr');
const pickBtn = $('#pickBtn');
const pathListEl = $('#pathList'), statusEl = $('#status');
const kwIn = $('#kw'), countEl = $('#resultCount'), resultsEl = $('#results'), emptyEl = $('#empty');
const modal = $('#modal'), frame = $('#frame'), modalTitle = $('#modalTitle');

let paths = [];

function esc(s) {
  return String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}
function highlight(text, kw) {
  const e = esc(text);
  if (!kw) return e;
  const re = new RegExp('(' + kw.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + ')', 'gi');
  return e.replace(re, '<mark>$1</mark>');
}

function renderPaths() {
  if (!paths.length) {
    pathListEl.innerHTML = '<span class="hint">还没有添加路径。在上方输入文件夹路径并点「添加路径」。</span>';
    return;
  }
  pathListEl.innerHTML = paths
    .map((p, i) => `<span class="chip">${esc(p)} <b class="x" data-i="${i}" title="移除">✕</b></span>`)
    .join('');
}

pathListEl.addEventListener('click', (e) => {
  if (e.target.classList.contains('x')) {
    paths.splice(+e.target.dataset.i, 1);
    renderPaths();
    loadAll();
  }
});

addBtn.onclick = () => {
  const v = folderInput.value.trim();
  if (!v) { statusEl.textContent = '请输入文件夹路径'; return; }
  if (paths.indexOf(v) >= 0) { statusEl.textContent = '该路径已添加：' + v; return; }
  paths.push(v);
  folderInput.value = '';
  renderPaths();
  loadAll();
};
folderInput.onkeydown = (e) => { if (e.key === 'Enter') addBtn.click(); };
reloadBtn.onclick = () => loadAll();
ocrIn.onchange = () => loadAll();

pickBtn.onclick = async () => {
  pickBtn.disabled = true;
  statusEl.textContent = '请在弹出的窗口中选择文件夹…';
  try {
    const d = await (await fetch('/api/pick_folder')).json();
    if (d.ok && d.path) {
      folderInput.value = d.path;
      addBtn.click();   // 选中后直接添加并加载
    } else {
      statusEl.textContent = '已取消选择目录。';
    }
  } catch (e) {
    statusEl.textContent = '选择目录出错：' + e;
  } finally {
    pickBtn.disabled = false;
  }
};

async function loadAll() {
  if (!paths.length) { statusEl.textContent = '请先添加至少一个文件夹路径'; return; }
  statusEl.textContent = '加载中…（含扫描件 OCR 时会慢一些，请稍候）';
  try {
    const r = await fetch('/api/load', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ folders: paths, ocr: ocrIn.checked }),
    });
    const ct = r.headers.get('content-type') || '';
    if (!r.ok || ct.indexOf('json') < 0) {
      statusEl.textContent = '加载失败（HTTP ' + r.status + '）：' + (await r.text()).slice(0, 400);
      return;
    }
    const d = await r.json();
    if (!d.ok) { statusEl.textContent = '加载失败：' + (d.error || '未知错误'); return; }
    const by = d.stats.by_ext || {};
    const parts = ['已加载 ' + d.count + ' 个文档（来自 ' + (d.folders ? d.folders.length : paths.length) + ' 个路径）'];
    const extSummary = Object.entries(by).map(([k, v]) => k + ' ' + v).join(' · ');
    if (extSummary) parts.push(extSummary);
    if (d.stats.ocr_used) parts.push(d.stats.ocr_used + ' 个用了 OCR');
    if (d.stats.errors && d.stats.errors.length) parts.push(d.stats.errors.length + ' 个解析异常');
    statusEl.textContent = parts.join('  ｜  ');
    kwIn.focus();
  } catch (e) {
    statusEl.textContent = '加载出错：' + e;
  }
}

let t;
kwIn.oninput = () => { clearTimeout(t); t = setTimeout(doSearch, 200); };
kwIn.onkeydown = (e) => { if (e.key === 'Enter') { clearTimeout(t); doSearch(); } };

async function doSearch() {
  const kw = kwIn.value.trim();
  if (!kw) {
    resultsEl.innerHTML = ''; countEl.textContent = '';
    emptyEl.style.display = 'block';
    emptyEl.textContent = '输入关键词开始搜索。';
    return;
  }
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
      </div>
      <div class="dir" title="${esc(it.path)}">${esc(dir)}</div>
      <div class="snips">${it.snippets.map((s) => `<div class="snip">${highlight(s, kw)}</div>`).join('')}</div>
    </li>`;
  }).join('');
  resultsEl.querySelectorAll('.prev').forEach((btn) => {
    btn.onclick = () => openPreview(btn.dataset.path, kw);
  });
}

function openPreview(path, kw) {
  modalTitle.textContent = path.split(/[\\/]/).pop();
  frame.src = '/preview?path=' + encodeURIComponent(path) + '&kw=' + encodeURIComponent(kw);
  modal.classList.remove('hidden');
}
$('#closeModal').onclick = () => { modal.classList.add('hidden'); frame.src = 'about:blank'; };
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') { modal.classList.add('hidden'); frame.src = 'about:blank'; }
});

(async () => {
  try {
    const d = await (await fetch('/api/settings')).json();
    paths = Array.isArray(d.folders) ? d.folders.slice() : [];
    if (typeof d.ocr === 'boolean') ocrIn.checked = d.ocr;
    renderPaths();
    if (paths.length) loadAll();
  } catch (e) {
    renderPaths();
  }
})();
