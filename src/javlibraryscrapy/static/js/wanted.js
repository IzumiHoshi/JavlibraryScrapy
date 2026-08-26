// wanted.js —— /wanted 页面初始化（Most Wanted 列表 + 抓取 + zspace 集成）。
//
// 模块结构（保持 initWanted 单函数内闭包，避免污染全局）：
//   1. 状态/URL/分页
//   2. 选择（多选 + localStorage 持久化）
//   3. 月份选择器（setupMonthPicker）
//   4. 卡片 HTML 模板（displayTitle / localBadgeHtml / statusBadge /
//      refetchBtnHtml / cardHtml）
//   5. 渲染（render / applyLocalFilter / renderCardInPlace / cssEscape）
//   6. 刷新状态（updateLastRefresh / phaseLabel）
//   7. 数据加载（load / loadMonthsOnly / startRefresh / pollRefreshStatus）
//   8. 抓取面板（openPanel / closePanel / renderJob / poll / watchJob /
//      startScrape / copyText / download）
//   9. 单部 JavBus 重抓（refetch 按钮 + visibilitychange + pagehide 防御）
//  10. badge hover → tooltip
//  11. 极空间 NAS 集成（loadZspaceStatus / sendToZspace / zspace 配置 modal）
//  12. 事件绑定 + 启动
//
// 依赖 utils.js / month-picker.js / tooltip.js / lightbox.js。

import { $, esc, toast, copyText, download, cssEscape } from './utils.js';
import { setupMonthPicker } from './month-picker.js';
import { fetchLibraryEntry, showTooltip, hideTooltip, renderLocalTooltip } from './tooltip.js';
import { openGalleryLb } from './lightbox.js';

export async function initWanted() {
  $('source').textContent = 'JAVLibrary + JavBus';
  $('toolbar-wanted').style.display = 'flex';
  $('toolbar-library').style.display = 'none';
  $('active-filters').style.display = 'none';
  const STORE_KEY = 'jav-gallery-selection';
  const STATUS_TEXT = {
    pending: '等待中', ok: '成功', no_magnet: '无磁力', failed: '失败',
    local_skip: '本地已有',
  };

  let movies = [];
  let months = [];
  let missingCount = 0;
  let total = 0;       // 服务端返回的全集总数（受 month/q 过滤）
  let pages = 1;       // ceil(total / size)，用于分页按钮 disabled 控制
  let selected = new Set();
  let pollTimer = null;
  let jobId = null;
  let lastItems = [];

  // ---- URL 状态：month / page / q ----
  const params = new URLSearchParams(location.search);
  let month = params.get('month') || '';
  let page = Math.max(1, parseInt(params.get('page') || '1', 10) || 1);
  const size = 60;
  let q = '';  // 本地搜索（不写 URL，避免 URL 混乱）

  function updateUrl() {
    const p = new URLSearchParams();
    if (month) p.set('month', month);
    if (page > 1) p.set('page', page);
    const qs = p.toString();
    history.replaceState(null, '', qs ? `?${qs}` : location.pathname);
  }

  function loadSelection() {
    try {
      const saved = JSON.parse(localStorage.getItem(STORE_KEY) || '[]');
      if (Array.isArray(saved)) selected = new Set(saved);
    } catch { /* 忽略 */ }
  }
  function saveSelection() {
    try { localStorage.setItem(STORE_KEY, JSON.stringify([...selected])); } catch { /* 忽略 */ }
  }
  function setSelected(code, on) {
    on ? selected.add(code) : selected.delete(code);
    const card = document.querySelector(`.card[data-code="${cssEscape(code)}"]`);
    if (card) {
      card.classList.toggle('selected', on);
      card.querySelector('input').checked = on;
    }
  }
  function refreshCount() {
    $('sel-count').textContent = selected.size;
    $('btn-scrape').disabled = selected.size === 0;
    saveSelection();
  }

  // 月份选择器：与 /library 共用 setupMonthPicker，id 用 w- 前缀避免冲突
  const monthPicker = setupMonthPicker({
    triggerId: 'btn-w-month',
    popupId: 'w-month-popup',
    yearPrevId: 'w-year-prev',
    yearNextId: 'w-year-next',
    yearDisplayId: 'w-year-display',
    gridId: 'w-month-grid',
    unknownId: 'w-month-unknown',
    clearId: 'w-month-clear',
    triggerLabelId: 'w-month-trigger-label',
    getMonths: () => months,
    getMonth: () => month,
    setMonth: (v) => { month = v; },
    onChange: () => { page = 1; updateUrl(); load(); },
  });

  function displayTitle(m) {
    const t = m.title || '';
    const stripped = t.replace(new RegExp('^' + m.code.replace(/[-[\]{}()*+?.,\\^$|#\s]/g, '\\$&') + '\\s*', 'i'), '');
    return stripped || t || '(无标题)';
  }

  function localBadgeHtml(m) {
    if (!m.local_exists) return '';
    const cls = 'local-badge';
    return `<div class="${cls}" data-code="${esc(m.code)}" title="本地库已收录">本地已有</div>`;
  }

  function statusBadge(m) {
    if (m.missing_in_remote) {
      return '<div class="local-badge warn" title="远端 Most Wanted 已找不到">远端缺</div>';
    }
    if (!m.release_date) {
      return m._status === 'failed'
        ? '<div class="local-badge warn" title="JavBus 抓取失败">抓取失败</div>'
        : '<div class="local-badge warn" title="等待 JavBus 详情">无日期</div>';
    }
    return '';
  }

  // 单部 JavBus 重抓按钮：
  //   - 无 release_date（failed / 无日期）→ 必显示（获取日期）
  //   - 有 release_date 但本地样品图为 0 → 也显示（重下 cover + samples）
  function refetchBtnHtml(m) {
    const noDate = !m.release_date;
    const noSamples = !((m.local_samples|0) > 0);
    if (!noDate && !noSamples) return '';
    const title = noDate
      ? (m._status === 'failed' ? '上次 JavBus 抓取失败，点此重试' : '手动重抓 JavBus 详情（获取 release_date）')
      : '本地无样品图，点此重抓 JavBus（重下 cover + samples）';
    return `<button class="refetch-btn" data-code="${esc(m.code)}" title="${esc(title)}">↻</button>`;
  }

  // 单卡片 HTML —— 提取出来供 render() 和 in-place 更新共用。
  function cardHtml(m) {
    return `
      <div class="card${selected.has(m.code) ? ' selected' : ''}${m.local_exists ? ' local-exists' : ''}"
           data-code="${esc(m.code)}"
           data-search="${esc((m.code + ' ' + (m.title || '') + ' ' + (m.actors || '')).toLowerCase())}">
        <input type="checkbox" ${selected.has(m.code) ? 'checked' : ''} tabindex="-1">
        ${(m.cover || m.cover_url) ? `<img class="cover" src="${esc(m.cover || m.cover_url)}" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="this.classList.add('broken')">`
                  : '<div class="cover"></div>'}
        ${localBadgeHtml(m)}
        ${statusBadge(m)}
        ${refetchBtnHtml(m)}
        ${(m.local_samples|0) > 0 ? `<div class="sample-badge" title="本地已下载的 sample 圖片數量">${esc(m.local_samples)} 張樣品</div>` : ''}
        <div class="body">
          <div class="code">${esc(m.code)}</div>
          <div class="name">${esc(displayTitle(m))}</div>
          ${m.release_date ? `<div class="meta-line"><span class="icon">📅 ${esc(m.release_date)}</span></div>` : ''}
          ${m.actors ? `<div class="meta-line"><span class="icon">👤 ${esc(m.actors.split(' / ').slice(0, 3).join(' · '))}</span></div>` : ''}
          ${m.javbus_url ? `<div class="card-links"><a href="${esc(m.javbus_url)}" target="_blank" rel="noreferrer">JavBus ↗</a></div>` : ''}
          ${m.magnet ? `<div class="card-magnet"><button class="r-copy" data-magnet="${esc(m.magnet)}" title="复制磁力链接（wanted refresh 时一并保存，无需再点抓磁力）"><svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m6 15-4-4 6.75-6.77a7.59 7.59 0 0 1 11 11L13 22l-4-4 6.39-6.36a2.14 2.14 0 0 0-3-3L6 15"/><path d="m5 8 4 4"/><path d="m12 15 4 4"/></svg>复制</button><button class="r-nas" data-code="${esc(m.code)}" data-magnet="${esc(m.magnet)}" title="直接推送到极空间 NAS（使用 .env 中的 ZSPACE_DOWNLOAD_PATH）"><svg class="ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>NAS</button></div>` : ''}
        </div>
      </div>`;
  }

  function render() {
    const grid = $('grid');
    if (!movies.length) {
      const emptyMsg = month
        ? `${month} 月份无数据，<a href="?" onclick="event.preventDefault(); window.location.search=''">查看全部</a>`
        : '没有影片数据。点击右上角"🔄 手动刷新"开始抓取';
      grid.innerHTML = `<div class="empty">${emptyMsg}</div>`;
      return;
    }
    grid.innerHTML = movies.map(cardHtml).join('');
    // 分页：显示真实 total（受 month/q 过滤的全集总数），下一页在最后一页禁用
    grid.innerHTML += `
      <div class="pagination">
        <button id="pg-prev" ${page <= 1 ? 'disabled' : ''}>上一页</button>
        <span class="info">第 ${page} / ${pages} 页 · 共 ${total} 部</span>
        <button id="pg-next" ${page >= pages ? 'disabled' : ''}>下一页</button>
      </div>`;
    $('total').textContent = total;
    $('pg-prev')?.addEventListener('click', () => { if (page > 1) { page--; load(); } });
    $('pg-next')?.addEventListener('click', () => { if (page < pages) { page++; load(); } });
    refreshCount();
    applyLocalFilter();
  }

  function applyLocalFilter() {
    const term = q.trim().toLowerCase();
    document.querySelectorAll('.card').forEach((card) => {
      card.classList.toggle('hidden', term !== '' && !card.dataset.search.includes(term));
    });
  }

  // 单卡片 in-place 更新：把 movies 数组里对应 code 的条目替换，
  // 并用 cardHtml(m) 重渲该 .card 节点。不动其它卡片（灯箱打开时不会闪）。
  function renderCardInPlace(code) {
    const m = movies.find((x) => x.code === code);
    if (!m) return false;
    const el = document.querySelector(`.card[data-code="${cssEscape(code)}"]`);
    if (!el) return false;
    const tmp = document.createElement('div');
    tmp.innerHTML = cardHtml(m).trim();
    const fresh = tmp.firstElementChild;
    el.replaceWith(fresh);
    applyLocalFilter();  // 重新过滤（data-search 可能变化了）
    return true;
  }

  function updateLastRefresh(snapshot) {
    const el = $('last-refresh');
    if (!snapshot) {
      el.textContent = '';
      el.classList.remove('has-fresh', 'is-stale', 'is-running');
      return;
    }
    const updated = snapshot.javbus_done + '/' + snapshot.javbus_total;
    const queue = snapshot.queue_length != null ? snapshot.queue_length
                   : Math.max(0, (snapshot.javbus_total|0) - (snapshot.javbus_done|0));
    const phase = snapshot.phase;
    const isRun = snapshot.status === 'running';
    el.classList.toggle('has-fresh', !isRun && phase === 'done');
    el.classList.toggle('is-stale', !isRun && phase === 'error');
    el.classList.toggle('is-running', isRun);
    let label;
    if (isRun) {
      // 抓取中：显示当前车牌 + 进度 + 队列剩余（含当前）
      // 例：🔄 抓 JavBus 详情 · IPZZ-907 · 5/30 · 队列25
      const cur = snapshot.current_code ? ` · ${snapshot.current_code}` : '';
      const qLabel = queue > 0 ? ` · 队列${queue}` : '';
      label = `🔄 ${phaseLabel(phase)}${cur} · ${updated}${qLabel}`;
    } else {
      label = `上次刷新：${phaseLabel(phase)} · +${snapshot.wanted_added} / 更新 ${snapshot.wanted_updated}`;
    }
    el.textContent = label;
  }

  function phaseLabel(phase) {
    return ({
      fetch_wanted: '抓取 Most Wanted',
      merge: '合并本地',
      fetch_javbus: '抓 JavBus 详情',
      save: '保存',
      done: '完成',
      error: '出错',
      idle: '空闲',
    })[phase] || phase;
  }

  /* ---------- 数据加载 ---------- */
  async function load() {
    updateUrl();
    const qs = new URLSearchParams({ page, size });
    if (month) qs.set('month', month);
    try {
      const res = await fetch(`/api/wanted?${qs}`);
      const text = await res.text();
      // 非 2xx 时服务端可能返回 HTML 错误页，先尝试解析 JSON 拿 .detail/.error
      if (!res.ok) {
        let msg = res.statusText;
        try { msg = (JSON.parse(text).detail || JSON.parse(text).error || msg); } catch {}
        throw new Error(msg);
      }
      const data = JSON.parse(text);
      movies = data.items || [];
      months = data.months || [];
      missingCount = data.missing_in_remote_count || 0;
      total = data.total || 0;
      pages = Math.max(1, Math.ceil(total / size));
      monthPicker.render();
      render();
    } catch (e) {
      $('grid').innerHTML = `<div class="empty">加载失败：${esc(e.message)}</div>`;
    }
  }

  // 只刷新月份桶摘要（不动 grid）。
  // 用于：单车抓取后该卡片的 _bucket 跨桶迁移（比如 unknown → 2026-08），
  // 这时 month chip 上的 count 数字不准，需要重算但不重渲 grid。
  async function loadMonthsOnly() {
    try {
      const res = await fetch('/api/wanted/months');
      const text = await res.text();
      if (!res.ok) throw new Error(res.statusText);
      const data = JSON.parse(text);
      months = data.months || [];
      missingCount = data.missing_in_remote_count || missingCount;
      monthPicker.render();
    } catch (e) {
      // 静默失败：不阻塞用户操作
      console.warn('loadMonthsOnly failed:', e);
    }
  }

  /* ---------- 手动刷新 ---------- */
  async function startRefresh() {
    $('btn-refresh').disabled = true;
    $('last-refresh').textContent = '正在触发…';
    try {
      const res = await fetch('/api/wanted/refresh', { method: 'POST' });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || res.statusText);
      if (data.is_already_running) {
        toast('已有刷新任务在跑，继续轮询进度');
      } else {
        toast('刷新任务已启动');
      }
      pollRefreshStatus();
    } catch (e) {
      toast('触发刷新失败：' + e.message);
      $('btn-refresh').disabled = false;
      $('last-refresh').textContent = '';
    }
  }
  function pollRefreshStatus() {
    clearInterval(pollTimer);
    pollTimer = setInterval(async () => {
      try {
        const res = await fetch('/api/wanted/refresh-status');
        if (!res.ok) return;
        const snap = await res.json();
        if (!snap || snap.status === 'idle') {
          clearInterval(pollTimer);
          pollTimer = null;
          $('btn-refresh').disabled = false;
          await load();
          return;
        }
        updateLastRefresh(snap);
        if (snap.status !== 'running') {
          clearInterval(pollTimer);
          pollTimer = null;
          $('btn-refresh').disabled = false;
          toast(
            snap.phase === 'error'
              ? '刷新失败：' + (snap.error || '未知错误')
              : `刷新完成：+${snap.wanted_added} 新增 / 更新 ${snap.wanted_updated} / JavBus ${snap.javbus_done} (${snap.javbus_failed} 失败)`
          );
          await load();
        }
      } catch { /* 静默 */ }
    }, 1500);
  }

  /* ---------- 抓取面板（保持兼容）---------- */
  const openPanel = () => $('panel').classList.add('open');
  const closePanel = () => $('panel').classList.remove('open');

  function renderJob(job) {
    lastItems = job.items || [];
    const total = job.total || lastItems.length;
    const pct = total ? Math.round((job.finished / total) * 100) : 0;
    $('prog-bar').style.width = pct + '%';
    $('prog-count').textContent = `${job.finished} / ${total}`;

    const skippedCount = (job.outputs && job.outputs.skipped_count) || 0;
    const skippedNote = skippedCount > 0 ? `（已跳过本地 ${skippedCount}）` : '';

    if (job.status === 'running') {
      $('prog-label').textContent = job.current ? `正在抓取 ${job.current}…` : '抓取中…';
    } else if (job.status === 'error') {
      $('prog-label').textContent = `任务出错：${job.error || '未知错误'}`;
    } else {
      $('prog-label').textContent = `完成：成功 ${job.succeeded} / ${total}${skippedNote}`;
    }

    $('results').innerHTML = lastItems.map((it) => `
      <div class="result">
        <span class="r-code">${esc(it.code)}</span>
        <span class="chip ${esc(it.status)}">${esc(STATUS_TEXT[it.status] || it.status)}</span>
        <span class="r-magnet" title="${esc(it.magnet || '')}">${esc(it.magnet || it.title || '')}</span>
        ${it.magnet ? `<button class="r-copy" data-magnet="${esc(it.magnet)}">复制</button>` : ''}
      </div>`).join('');

    const log = $('log');
    const atBottom = log.scrollTop + log.clientHeight >= log.scrollHeight - 24;
    log.textContent = (job.logs || []).join('\n');
    if (atBottom) log.scrollTop = log.scrollHeight;

    const failed = lastItems.filter((it) =>
      it.status === 'failed' || it.status === 'no_magnet' || it.status === 'local_skip');
    $('btn-retry').disabled = job.status === 'running' || failed.length === 0;
    $('btn-retry').textContent = failed.length ? `重试失败项 (${failed.length})` : '重试失败项';

    if (job.outputs && job.outputs.json) {
      $('outputs').innerHTML = `已写入：<code>${esc(job.outputs.json)}</code><br>磁力列表：<code>${esc(job.outputs.links)}</code>`;
    }

    const running = job.status === 'running';
    $('btn-scrape').disabled = running || selected.size === 0;
    $('btn-scrape').textContent = running ? '抓取中…' : '抓取选中的磁力';
    // Scrape 进行中禁用「📥 发送到NAS」：避免把上一次 job 的 lastItems 误当作新结果提交
    $('btn-send-zspace').disabled = running || !zspaceStatus || !zspaceStatus.configured;
    return running;
  }

  async function poll() {
    if (!jobId) return;
    try {
      const res = await fetch(`/api/job/${jobId}`);
      if (!res.ok) throw new Error(await res.text());
      const job = await res.json();
      if (!renderJob(job)) {
        clearInterval(pollTimer); pollTimer = null;
        toast(job.status === 'error' ? '抓取任务出错' : `抓取完成，成功 ${job.succeeded} / ${job.total}`);
      }
    } catch (e) {
      clearInterval(pollTimer); pollTimer = null;
      toast('获取任务状态失败：' + e.message);
    }
  }
  function watchJob(id) {
    jobId = id; openPanel();
    clearInterval(pollTimer); poll(); pollTimer = setInterval(poll, 1000);
  }

  async function startScrape(codes) {
    if (!codes.length) return toast('请先勾选影片');
    $('btn-scrape').disabled = true;
    try {
      const res = await fetch('/api/scrape', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ codes }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || res.statusText);
      $('results').innerHTML = ''; $('log').textContent = ''; $('outputs').innerHTML = '';
      const skipMsg = (data.skipped && data.skipped.length) ? `，跳过本地 ${data.skipped.length}` : '';
      toast(`已提交 ${data.total} 个车牌${skipMsg}`);
      watchJob(data.job_id);
    } catch (e) {
      $('btn-scrape').disabled = selected.size === 0;
      toast('提交失败：' + e.message);
    }
  }

  /* ---------- 事件 ---------- */
  $('grid').addEventListener('click', (e) => {
    if (e.target.closest('a')) return;
    if (e.target.closest('.local-badge')) return;  // badge自己处理
    // 点击卡片里的「复制」按钮 → 复制磁力链接，不触发卡片选中
    const copyBtn = e.target.closest('.r-copy');
    if (copyBtn) {
      e.stopPropagation();
      e.preventDefault();
      copyText(copyBtn.dataset.magnet, '已复制磁力链接');
      return;
    }
    // 点击卡片里的「📥 NAS」按钮 → 推送单条磁力到 NAS（不弹路径框，走 .env 配置）
    const nasBtn = e.target.closest('.r-nas');
    if (nasBtn) {
      e.stopPropagation();
      e.preventDefault();
      sendOneToZspace(nasBtn);
      return;
    }
    // 点击单部 JavBus 重抓按钮 → 调 /api/wanted/{code}/javbus，成功后刷新当前页
    const refetchBtn = e.target.closest('.refetch-btn');
    if (refetchBtn) {
      e.stopPropagation();
      e.preventDefault();
      refetchOneJavbus(refetchBtn);
      return;
    }
    // 点击封面 → 多图灯箱（wanted 页面：cover + 樣品）
    const cover = e.target.closest('.cover');
    if (cover && cover.src && !cover.classList.contains('broken')) {
      e.stopPropagation();
      e.preventDefault();
      const card = cover.closest('.card');
      const code = card?.dataset.code || '';
      const title = card?.dataset.title || card?.querySelector('.name')?.textContent || '';
      // 乐观打开：把卡片上的 cover URL 直接当首图（浏览器大概率已缓存这张图）
      openGalleryLb(code, title, { coverHint: cover.src });
      return;
    }
    const card = e.target.closest('.card');
    if (!card) return;
    const code = card.dataset.code;
    const willSelect = e.target.matches('input[type="checkbox"]')
      ? e.target.checked : !selected.has(code);
    setSelected(code, willSelect); refreshCount();
  });

  // 单部 JavBus 重抓：阻塞同步调用（5-45 秒），完成后 in-place 更新该卡片，
  // 不重渲整个网格 —— 灯箱打开时不会闪到背后卡片。
  // 服务器响应里 data.movie 是更新后的完整条目（成功或失败但已存在）。
  //   - 成功：release_date/bucket/_status 全部刷新；按钮变 "✓ 已抓取" 后消失（如果不再需要）
  //   - 失败：仅 _status 变 failed；按钮短暂显示 ✗ 再回到 ↻
  //   - 异常（response 没 movie 字段）：fallback 到 load() 全表刷新
  //
  // 移动端坑：
  //   - 单次 fetch 可能 30-60s 没响应，浏览器/网络可能 timeout 后静默杀掉；
  //   - 后台标签页会被暂停，恢复后状态可能错位；
  //   - 用户切走再回来时按钮可能仍卡在 "⏳ 抓取…"。
  // 因此：
  //   1) 按钮文字每秒刷新一次显示已等待时间（"⏳ 抓取 30s"）；
  //   2) AbortController 5 分钟硬超时，到时间客户端主动放弃并标记失败；
  //   3) visibilitychange 切回前台时检查所有 running 按钮，超过 90s 的强制标记失败并刷新；
  //   4) pagehide 时 abort 所有 in-flight，避免后台挂着空 fetch。
  const REFETCH_TIMEOUT_MS = 5 * 60 * 1000;   // 5 分钟硬超时
  const REFETCH_STUCK_MS    = 90 * 1000;      // > 90s 仍 running 视为卡住
  const refetchControllers = new Map();        // code -> AbortController（支持取消）

  function resetRefetchBtn(btn, status /* 'failed'|'done'|... */) {
    const code = btn.dataset.code || '';
    refetchControllers.delete(code);
    // 注意：renderCardInPlace 已经把 btn 节点从 DOM 替换掉了，
    // 这里直接操作的 btn 可能已经 detached。需要找到新卡片上的按钮（如果还在）。
    const liveBtn = document.querySelector(`.refetch-btn[data-code="${cssEscape(code)}"]`) || btn;
    liveBtn.disabled = false;
    liveBtn.classList.remove('running', 'done', 'failed');
    delete liveBtn.dataset.startedAt;
    if (status === 'failed') {
      liveBtn.classList.add('failed');
      liveBtn.textContent = '✗';
      setTimeout(() => {
        if (liveBtn.classList.contains('failed')) {
          liveBtn.classList.remove('failed');
          liveBtn.textContent = '↻';
        }
      }, 3000);
    } else {
      liveBtn.textContent = '↻';
    }
  }

  // 启动一个超时监控定时器：到 REFETCH_TIMEOUT_MS 主动 abort + 标记失败。
  // 按钮只显示图标（↻），无文字 —— running 状态由 .refetch-btn.running 的
  // pulse CSS 动画表达。title tooltip 由 refetchBtnHtml 根据状态更新。
  function startRefetchTick(btn, controller, startedAt) {
    const tick = setInterval(() => {
      if (controller.signal.aborted) { clearInterval(tick); return; }
      if (Date.now() - startedAt > REFETCH_TIMEOUT_MS) {
        controller.abort(new Error('timeout'));
      }
    }, 1000);
    controller.signal.addEventListener('abort', () => clearInterval(tick), { once: true });
  }

  async function refetchOneJavbus(btn) {
    const code = btn.dataset.code || '';
    if (!code) return;
    if (btn.disabled) return;  // 已经在跑
    btn.disabled = true;
    btn.classList.remove('done', 'failed');
    btn.classList.add('running');
    const startedAt = Date.now();
    btn.dataset.startedAt = String(startedAt);
    // 保持 ↻ 图标（pulse 动画表达"抓取中"），不显示文字
    btn.textContent = '↻';

    const controller = new AbortController();
    refetchControllers.set(code, controller);
    startRefetchTick(btn, controller, startedAt);

    try {
      const res = await fetch(`/api/wanted/${encodeURIComponent(code)}/javbus`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
        signal: controller.signal,
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      if (data.ok) {
        toast(`${code} → ${data.bucket}（${data.release_date || ''}）`);
        if (data.movie) {
          // 抓取成功 → 本地刚落了 cover + samples，
          // 必须重新读磁盘才能拿到最新的 local_samples 计数（data.movie 没有这个字段）。
          // 不更新的话：卡片 sample-badge 不显示，"↻"按钮也会残留
          // （refetchBtnHtml 判断 !noSamples 才隐藏按钮）。
          // gallery-images 走 NFS thread pool 读盘，单次 < 50ms，不阻塞 UI。
          try {
            const gr = await fetch(`/api/wanted/${encodeURIComponent(code)}/gallery-images`);
            if (gr.ok) {
              const gd = await gr.json();
              data.movie.local_samples = (gd.samples || []).length;
            }
          } catch (e) {
            // 拿不到就保持 data.movie.local_samples=0/旧值，至少不影响主流程
            console.warn(`refresh local_samples failed for ${code}:`, e);
          }
          const oldBucket = (movies.find((x) => x.code === code) || {})._bucket;
          const i = movies.findIndex((x) => x.code === code);
          if (i >= 0) movies[i] = data.movie;
          if (oldBucket !== data.movie._bucket) {
            // bucket 跨月迁移 → 更新月份 chip 数 + 重渲该卡片（release_date /
            // sample-badge / refetch-btn 全部依赖新 movies[i] 数据，不重渲就过期）
            await loadMonthsOnly();
            renderCardInPlace(code);
          } else {
            renderCardInPlace(code);
          }
        } else {
          await load();
        }
        // 成功后：按钮瞬时变绿再消失（如果 refetchBtnHtml 返回 ''）。
        // refetchBtnHtml 的判断已通过 renderCardInPlace 反映到新 DOM。
        // 当前 btn 节点已被 replaceWith 替换掉，无需再 reset。
        refetchControllers.delete(code);
      } else {
        toast(`${code} 抓取失败：${data.error || '未知错误'}`, true);
        if (data.movie) {
          const i = movies.findIndex((x) => x.code === code);
          if (i >= 0) movies[i] = data.movie;
          renderCardInPlace(code);
        } else {
          await load();
        }
        resetRefetchBtn(btn, 'failed');
      }
    } catch (e) {
      // abort / 网络失败 / 超时都走这里
      const isAbort = e && (e.name === 'AbortError' || controller.signal.aborted);
      if (isAbort && e.message && e.message !== 'timeout') {
        // 用户主动切换页面（pagehide）触发的 abort，不算失败
        // 但要把按钮状态收尾，否则下次回来还是 "running"
        btn.classList.remove('running', 'done', 'failed');
        btn.textContent = '↻';
        btn.disabled = false;
        refetchControllers.delete(code);
      } else {
        toast(`${code} ${isAbort ? '抓取超时（>5min）' : '请求失败：' + e.message}`, true);
        resetRefetchBtn(btn, 'failed');
        // 失败后顺手 load 一次（让 UI 跟服务端实际状态对齐 —— 万一后端已完成但前端没收到响应）
        await load();
      }
    }
  }

  // ---------- 移动端防御：切走/切回/卸载 ----------
  // 切走（后台标签/锁屏）：abort 所有 in-flight，避免浏览器静默杀掉后 fetch 永远不返回
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) return;
    // 回到前台：检查是否有按钮卡在 running 超过 90s，是的话强制 abort + 触发 reload 对齐服务端
    let hasStuck = false;
    for (const [code, ctrl] of refetchControllers.entries()) {
      const btn = document.querySelector(`.refetch-btn[data-code="${cssEscape(code)}"]`);
      if (!btn || !btn.classList.contains('running')) {
        refetchControllers.delete(code);
        continue;
      }
      const sinceMs = Date.now() - Number(btn.dataset.startedAt || Date.now());
      if (sinceMs > REFETCH_STUCK_MS) {
        ctrl.abort(new Error('stuck-on-resume'));
        resetRefetchBtn(btn, 'failed');
        hasStuck = true;
      }
    }
    if (hasStuck) load();  // 对齐服务端真实状态
  });

  window.addEventListener('pagehide', () => {
    for (const ctrl of refetchControllers.values()) {
      try { ctrl.abort(new Error('pagehide')); } catch {}
    }
    refetchControllers.clear();
  });

  /* ---------- badge hover → tooltip ---------- */
  $('grid').addEventListener('mouseover', async (e) => {
    const badge = e.target.closest('.local-badge');
    if (!badge) return;
    const code = badge.dataset.code;
    const entry = await fetchLibraryEntry(code);
    if (entry) showTooltip(badge, renderLocalTooltip(entry));
  });
  $('grid').addEventListener('mouseout', (e) => {
    if (e.target.closest('.local-badge')) hideTooltip();
  });

  $('search').addEventListener('input', (e) => { q = e.target.value; applyLocalFilter(); });
  $('btn-all').addEventListener('click', () => { visibleCards().forEach((c) => setSelected(c.dataset.code, true)); refreshCount(); });
  $('btn-none').addEventListener('click', () => {
    selected.clear();
    document.querySelectorAll('.card').forEach((c) => { c.classList.remove('selected'); c.querySelector('input').checked = false; });
    refreshCount();
  });
  $('btn-invert').addEventListener('click', () => { visibleCards().forEach((c) => setSelected(c.dataset.code, !selected.has(c.dataset.code))); refreshCount(); });
  $('btn-export').addEventListener('click', () => {
    if (!selected.size) return toast('请先勾选影片');
    const codes = movies.filter((m) => selected.has(m.code)).map((m) => m.code);
    download('selected_codes.txt', codes.join('\n') + '\n');
    toast(`已导出 ${codes.length} 个车牌`);
  });
  $('btn-scrape').addEventListener('click', () => {
    startScrape(movies.filter((m) => selected.has(m.code)).map((m) => m.code));
  });
  $('btn-retry').addEventListener('click', () => {
    const codes = lastItems
      .filter((it) => it.status !== 'ok' && it.status !== 'local_skip')
      .map((it) => it.code);
    startScrape(codes);
  });
  $('btn-copy-all').addEventListener('click', () => {
    const links = lastItems.filter((it) => it.magnet).map((it) => it.magnet);
    if (!links.length) return toast('暂无磁力链接');
    copyText(links.join('\n'), `已复制 ${links.length} 条磁力链接`);
  });

  /* ---------- 极空间 NAS 集成 ----------
   * 拉取 /api/zspace/status 决定按钮是否启用；点击后弹路径输入（默认 = status.default_download_path），
   * POST lastItems 里的 magnet 到 /api/zspace/submit，结果以 toast + 控制台 warn 给出。 */
  let zspaceStatus = null;
  async function loadZspaceStatus() {
    try {
      const res = await fetch('/api/zspace/status');
      if (!res.ok) throw new Error(res.statusText);
      zspaceStatus = await res.json();
    } catch {
      zspaceStatus = { configured: false };
    }
    const btn = $('btn-send-zspace');
    btn.disabled = !zspaceStatus.configured;
    btn.title = zspaceStatus.configured
      ? `发送到极空间 ${zspaceStatus.host || ''}（默认 ${zspaceStatus.default_download_path}）`
      : '极空间未配置（.env 需 ZSPACE_ENABLED + ZSPACE_HOST/USER/PASSWORD）';
    // 同步禁用卡片里的「📥 NAS」按钮
    document.querySelectorAll('.r-nas').forEach((b) => {
      b.disabled = !zspaceStatus.configured;
      if (!zspaceStatus.configured) {
        b.title = '极空间未配置（.env 需 ZSPACE_ENABLED + ZSPACE_HOST/USER/PASSWORD）';
      }
    });
  }
  // 单卡片 NAS 推送：直接调 /api/zspace/submit（items 长度=1），不弹路径框，
  // 路径走 zspaceStatus.default_download_path。按钮状态：sending 时禁用 + 改文案，
  // 完成后短暂变 ✓/✗ 再恢复。
  async function sendOneToZspace(btn) {
    if (!zspaceStatus || !zspaceStatus.configured) return toast('极空间未配置');
    const code = btn.dataset.code || '';
    const magnet = btn.dataset.magnet || '';
    if (!magnet) return toast('该卡片没有磁力链接');
    if (btn.disabled) return;

    const path = (zspaceStatus.default_download_path || '').trim();
    if (!path) return toast('ZSPACE_DOWNLOAD_PATH 未配置（请在 .env 设置）');

    btn.disabled = true;
    const orig = btn.textContent;
    btn.textContent = '📥 推送中…';
    try {
      const res = await fetch('/api/zspace/submit', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          items: [{ code, magnet }],
          download_path: path,
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || data.error || res.statusText);
      const results = data.results || [];
      const r = results[0] || {};
      if (r.ok) {
        btn.textContent = '✓ 已推送';
        btn.classList.add('r-nas-ok');
        toast(`${code} 已推送到 NAS`);
        setTimeout(() => {
          btn.textContent = orig;
          btn.classList.remove('r-nas-ok');
          btn.disabled = false;
        }, 2500);
      } else {
        const code2 = r.status_code ? ` [${r.status_code}]` : '';
        const msg = r.msg || r.error || '未知错误';
        btn.textContent = '✗ 失败';
        btn.classList.add('r-nas-fail');
        toast(`${code} NAS 推送失败${code2}：${msg}`, true);
        setTimeout(() => {
          btn.textContent = orig;
          btn.classList.remove('r-nas-fail');
          btn.disabled = false;
        }, 3000);
      }
    } catch (e) {
      btn.textContent = '✗ 失败';
      btn.classList.add('r-nas-fail');
      toast(`NAS 推送失败：${e.message}`, true);
      setTimeout(() => {
        btn.textContent = orig;
        btn.classList.remove('r-nas-fail');
        btn.disabled = false;
      }, 3000);
    }
  }

  async function sendToZspace() {
    const items = lastItems
      .filter((it) => it.magnet)
      .map((it) => ({ code: it.code, magnet: it.magnet }));
    if (!items.length) return toast('暂无磁力链接可发送');
    if (!zspaceStatus || !zspaceStatus.configured) return toast('极空间未配置');
    // prompt 在桌面 + 移动浏览器都可用，路径对话框比自定义 overlay 更稳。
    const path = window.prompt(
      `极空间下载目录（/pool/my/data/.../）`,
      zspaceStatus.default_download_path || ''
    );
    if (path === null) return;  // 用户取消
    const download_path = path.trim();
    if (!download_path) return toast('下载目录不能为空');

    const btn = $('btn-send-zspace');
    btn.disabled = true;
    btn.textContent = '📥 发送中…';
    try {
      const res = await fetch('/api/zspace/submit', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ items, download_path }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || data.error || res.statusText);
      const failed = (data.results || []).filter((r) => !r.ok);
      if (failed.length) {
        console.warn('zspace 部分提交失败：', failed);
        // 把 NAS 业务码 + msg 直接显示在 toast（最多 3 条避免截屏塞爆），用户不用开 console 也能定位字段名问题
        const sample = failed.slice(0, 3).map((r) => {
          const code = r.status_code ? ` [${r.status_code}]` : '';
          const msg = r.msg ? `: ${r.msg}` : (r.error ? `: ${r.error}` : '');
          return `${r.code}${code}${msg}`;
        }).join('；');
        const more = failed.length > 3 ? `；…还有 ${failed.length - 3} 条` : '';
        toast(`提交 ${data.total} 条，成功 ${data.ok_count}，失败 ${failed.length}：${sample}${more}`, true);
      } else {
        toast(`已提交 ${data.total} 条磁力到 ${data.download_path}`);
      }
    } catch (e) {
      toast('发送失败：' + e.message, true);
    } finally {
      btn.textContent = '📥 发送到NAS';
      btn.disabled = !zspaceStatus || !zspaceStatus.configured;
    }
  }
  $('btn-send-zspace').addEventListener('click', sendToZspace);

  $('btn-refresh').addEventListener('click', startRefresh);
  $('results').addEventListener('click', (e) => {
    const btn = e.target.closest('.r-copy');
    if (btn) copyText(btn.dataset.magnet, '已复制磁力链接');
  });
  $('btn-close').addEventListener('click', closePanel);
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closePanel(); });

  // visibleCards：当前可见（未被本地搜索隐藏）的卡片，供全选/反选用
  function visibleCards() {
    return [...document.querySelectorAll('.card:not(.hidden)')];
  }

  /* ---------- 启动 ---------- */
  loadSelection();
  await load();
  // 拉极空间配置状态（决定「📥 发送到NAS」按钮是否启用）
  await loadZspaceStatus();
  // 启动时轮询一次刷新状态（如果后台正在跑）
  try {
    const r = await fetch('/api/wanted/refresh-status');
    if (r.ok) {
      const s = await r.json();
      if (s && s.status === 'running') {
        updateLastRefresh(s);
        pollRefreshStatus();
        $('btn-refresh').disabled = true;
      }
    }
  } catch { /* 静默 */ }
}
