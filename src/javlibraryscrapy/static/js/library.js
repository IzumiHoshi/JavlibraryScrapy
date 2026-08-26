// library.js —— /library 页面初始化（本地库浏览 + 单部刷新 + 月份/演员筛选）。
//
// 模块结构：
//   1. 状态/URL/分页/筛选
//   2. 月份选择器
//   3. 卡片渲染（renderActiveFilters / localCoverUrl / renderCard / render）
//   4. lib-status 分段渲染（renderLibStatusHtml / renderLibScanningHtml）
//   5. 数据加载（loadStatus / loadWarnings / load）
//   6. 事件绑定（搜索 / 排序 / 触发扫描 / 卡片点击 / rescan 状态轮询）
//   7. 启动
//
// 依赖 utils.js / month-picker.js / lightbox.js。

import { $, esc, fmtBytes, toast } from './utils.js';
import { setupMonthPicker } from './month-picker.js';
import { openGalleryLb } from './lightbox.js';

export async function initLibrary() {
  $('toolbar-library').style.display = 'flex';
  $('toolbar-wanted').style.display = 'none';

  const params = new URLSearchParams(location.search);
  let page = Math.max(1, parseInt(params.get('page') || '1', 10) || 1);
  let q = params.get('q') || '';
  let sort = params.get('sort') || 'released';
  let month = params.get('month') || '';
  let actor = params.get('actor') || '';

  let movies = [];
  let months = [];
  let total = 0;
  let pages = 1;

  function updateUrl() {
    const p = new URLSearchParams();
    if (month) p.set('month', month);
    if (actor) p.set('actor', actor);
    if (sort && sort !== 'released') p.set('sort', sort);
    if (q) p.set('q', q);
    if (page > 1) p.set('page', page);
    const qs = p.toString();
    history.replaceState(null, '', qs ? `?${qs}` : location.pathname);
  }

  // 月份选择器：与 /wanted 共用 setupMonthPicker，id 不带前缀（library 是唯一无前缀的）
  const monthPicker = setupMonthPicker({
    triggerId: 'btn-month',
    popupId: 'month-popup',
    yearPrevId: 'year-prev',
    yearNextId: 'year-next',
    yearDisplayId: 'year-display',
    gridId: 'month-grid',
    unknownId: 'month-unknown',
    clearId: 'month-clear',
    triggerLabelId: 'month-trigger-label',
    getMonths: () => months,
    getMonth: () => month,
    setMonth: (v) => { month = v; },
    onChange: () => { page = 1; updateUrl(); load(); },
  });

  function renderActiveFilters() {
    const el = $('active-filters');
    if (!actor) {
      el.style.display = 'none';
      el.innerHTML = '';
      return;
    }
    el.style.display = 'flex';
    el.innerHTML = `
      <span class="label">当前筛选：</span>
      <span class="chip">演员：${esc(actor)}
        <button type="button" class="chip-x" data-clear="actor" title="清除演员筛选">×</button>
      </span>`;
    el.querySelector('.chip-x[data-clear="actor"]').addEventListener('click', () => {
      actor = '';
      page = 1;
      updateUrl();
      load();
    });
  }

  function localCoverUrl(folder) {
    return `/api/local-cover?folder=${encodeURIComponent(folder)}`;
  }

  function renderCard(m) {
    const noVideoCls = m.has_video ? '' : ' local-no-video';
    const badge = m.has_video
      ? ''
      : '<div class="local-badge warn" data-code="' + esc(m.carid) + '" title="此目录下未找到视频文件">无视频</div>';
    // 图片总数角标（cover/poster/fanart/sample_NNN.jpg）：与 wanted 卡片
    // .sample-badge 一致但含义更广。服务端 library routes 的
    // _batch_count_samples（folder→count 轻量缓存）提供 image_count 字段；
    // 命中缓存，未命中并发 stat/glob 一次。
    const imageCount = m.image_count | 0;
    const sampleBadge = imageCount > 0
      ? `<div class="sample-badge" title="本地資料夾中的圖片總數（封面+海報+Fanart+樣品）">${esc(imageCount)} 張圖</div>`
      : '';
    const visibleActors = (m.actors || []).slice(0, 2);
    const actorsHtml = visibleActors.length
      ? visibleActors.map(a =>
          `<button type="button" class="actor-tag" data-actor="${esc(a)}" title="点击只看 ${esc(a)}">${esc(a)}</button>`
        ).join(' · ')
      : '—';
    const tick = (on, label) =>
      `<span class="icon ${on ? 'on' : 'off'}" title="${label}">${on ? '✓' : '✗'} ${label}</span>`;
    // 刷新按钮：有视频的卡片才显示（没有视频没东西可刷）
    const rescanBtn = m.has_video
      ? `<button class="rescan-btn" data-code="${esc(m.carid)}" title="重新搜刮（生成 NFO + 封面 + 下载样图）">↻</button>`
      : '';
    return `
      <div class="card${noVideoCls}" data-code="${esc(m.carid)}" data-folder="${esc(m.folder)}" tabindex="0">
        ${rescanBtn}
        <img class="cover" src="${esc(localCoverUrl(m.folder))}" alt="" loading="lazy"
             referrerpolicy="no-referrer" onerror="this.classList.add('broken')">
        ${badge}
        ${sampleBadge}
        <div class="body">
          <div class="code">${esc(m.carid)}</div>
          <div class="name">${esc(m.title || '(无标题)')}</div>
          <div class="meta-line">
            ${tick(m.has_nfo, 'NFO')}
            ${tick(m.has_poster, '海报')}
            ${tick(m.has_fanart, 'Fanart')}
            <span class="icon">🎬 ${m.video_count} · ${fmtBytes(m.total_size_bytes)}</span>
          </div>
          ${m.release_date ? `<div class="meta-line"><span class="icon">📅 ${esc(m.release_date)}</span></div>` : ''}
          <div class="meta-line"><span class="icon">👤</span> ${actorsHtml}</div>
          <div class="folder" title="${esc(m.folder)}">${esc(m.folder)}</div>
        </div>
      </div>`;
  }

  function render() {
    const grid = $('grid');
    if (!movies.length) {
      grid.innerHTML = '<div class="empty">没有匹配的影片' + (q ? `（搜索：${esc(q)}）` : '') + '</div>';
      return;
    }
    let html = movies.map(renderCard).join('');
    // 分页
    html += `
      <div class="pagination">
        <button id="pg-prev" ${page <= 1 ? 'disabled' : ''}>上一页</button>
        <span class="info">第 ${page} / ${pages} 页 · 共 ${total} 部</span>
        <button id="pg-next" ${page >= pages ? 'disabled' : ''}>下一页</button>
      </div>`;
    grid.innerHTML = html;
    $('pg-prev')?.addEventListener('click', () => { if (page > 1) { page--; load(); } });
    $('pg-next')?.addEventListener('click', () => { if (page < pages) { page++; load(); } });
  }

  // 渲染顶部 stats。分段结构：路径独立 span + title 属性，让窄屏 CSS 可以
  // 单独对路径段做截断/换行而不影响 "共 X 部" / "上次扫描" 段。
  // textContent 一锅端 → 1280px 屏幕上长 UNC 路径会溢出 toolbar。
  function renderLibStatusHtml(data) {
    const segs = [];
    segs.push(`共 <b>${data.movies_count}</b> 部`);
    if (data.root) {
      segs.push(
        `<span class="ls-label">根</span>` +
        `<span class="ls-path" title="${esc(data.root)}">${esc(data.root)}</span>`
      );
    }
    if (data.scanned_at) {
      segs.push(`上次扫描 ${data.scanned_at.replace('T', ' ').slice(0, 16)}`);
    }
    return segs.join('<span class="ls-sep">·</span>');
  }
  // 扫描中/扫描失败用独立分段：路径 + 当前目录都单独 span，方便窄屏换行。
  function renderLibScanningHtml(data) {
    const cur = data.current_folder ? data.current_folder.split(/[\\/]/).pop() : '';
    return [
      `正在扫描 <b>${data.scanned}</b>/${data.total_estimate}`,
      `<span class="ls-path" title="${esc(data.current_folder || '')}">${esc(cur || '…')}</span>`,
    ].join('<span class="ls-sep">·</span>');
  }

  async function loadStatus() {
    try {
      const res = await fetch('/api/library/status');
      const data = await res.json();
      if (!data.configured) {
        $('banner').hidden = false;
        $('banner').className = 'banner error';
        $('banner').textContent = '未配置本地库根目录（LIBRARY_ROOT）';
        $('lib-status').textContent = '未配置';
        return;
      }
      if (data.is_running) {
        $('lib-status').innerHTML = renderLibScanningHtml(data);
        $('btn-lib-rescan').disabled = true;
      } else {
        $('btn-lib-rescan').disabled = false;
        if (data.error) {
          $('banner').hidden = false;
          $('banner').className = 'banner error';
          $('banner').textContent = `扫描失败：${data.error}`;
        }
        // 分段渲染：路径单独 span + title，窄屏 CSS 可单独截断/换行；
        // textContent 一锅端会让长 UNC 路径在 1280px 屏幕溢出 toolbar。
        $('lib-status').innerHTML = renderLibStatusHtml(data);
      }
    } catch (e) {
      $('lib-status').textContent = '状态获取失败';
    }
  }

  async function loadWarnings() {
    try {
      const res = await fetch('/api/library/warnings');
      const data = await res.json();
      const dup = data.duplicate_carids || [];
      const noNfo = data.folders_without_nfo || [];
      const parts = [];
      if (dup.length) parts.push(`重复车牌 ${dup.length} 部`);
      if (noNfo.length) parts.push(`无 NFO ${noNfo.length} 部`);
      if (parts.length) {
        $('banner').hidden = false;
        $('banner').className = 'banner';
        $('banner').textContent = '扫描提示：' + parts.join('，') + '。详见 output/library_index.json';
      }
    } catch { /* 忽略 */ }
  }

  async function load() {
    updateUrl();
    const qs = new URLSearchParams({ q, page, size: 100, sort });
    if (month) qs.set('month', month);
    if (actor) qs.set('actor', actor);
    try {
      const res = await fetch(`/api/library?${qs}`);
      const data = await res.json();
      if (res.status === 503) {
        $('banner').hidden = false;
        $('banner').className = 'banner error';
        $('banner').textContent = data.error || '本地库未配置';
        $('grid').innerHTML = '';
        return;
      }
      if (!res.ok) throw new Error(data.error || res.statusText);
      movies = data.movies || [];
      months = data.months || [];
      total = data.total;
      pages = Math.max(1, Math.ceil(total / data.size));
      monthPicker.render();
      renderActiveFilters();
      render();
      loadStatus();
    } catch (e) {
      $('grid').innerHTML = `<div class="empty">加载失败：${esc(e.message)}</div>`;
    }
  }

  /* ---------- 事件 ---------- */
  let searchTimer = null;
  $('lib-search').addEventListener('input', (e) => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => { q = e.target.value; page = 1; load(); }, 250);
  });
  $('lib-sort').addEventListener('change', (e) => { sort = e.target.value; page = 1; load(); });

  $('btn-lib-rescan').addEventListener('click', async () => {
    $('btn-lib-rescan').disabled = true;
    try {
      const res = await fetch('/api/library/rescan', { method: 'POST' });
      if (res.status === 409) return toast('扫描已在进行中');
      if (!res.ok) throw new Error((await res.json()).error || res.statusText);
      toast('已开始扫描…');
      loadStatus();
      const tick = setInterval(async () => {
        await loadStatus();
        if (!$('btn-lib-rescan').disabled) { clearInterval(tick); load(); }
      }, 1500);
    } catch (e) {
      toast('触发扫描失败：' + e.message);
      $('btn-lib-rescan').disabled = false;
    }
  });

  /* ---------- 点击处理：刷新按钮 > 灯箱（卡片其他位置不再触发打开文件夹） ---------- */
  $('grid').addEventListener('click', async (e) => {
    // 1) 刷新按钮
    const rbtn = e.target.closest('.rescan-btn');
    if (rbtn) {
      e.stopPropagation();
      e.preventDefault();
      const carid = rbtn.dataset.code;
      try {
        rbtn.disabled = true;
        rbtn.classList.add('running');
        const res = await fetch(`/api/library/${encodeURIComponent(carid)}/rescan`, { method: 'POST' });
        const data = await res.json();
        if (!res.ok) throw new Error(data.error || res.statusText);
        if (data.already) {
          toast(data.running ? `${carid} 正在刷新` : `${carid} 已在队列 #${data.position}`);
        } else {
          toast(`已入队：${carid}（位置 #${data.position}）`);
        }
        refreshRescanStatus();
      } catch (err) {
        rbtn.disabled = false;
        rbtn.classList.remove('running', 'queued', 'done');
        toast('触发刷新失败：' + err.message);
      }
      return;
    }
    // 2) 封面 → 灯箱（与 wanted 一致：cover + 全部样品多图浏览）
    const cover = e.target.closest('.cover');
    if (cover && cover.src && !cover.classList.contains('broken')) {
      e.stopPropagation();
      e.preventDefault();
      const card = cover.closest('.card');
      const code = card?.dataset.code || '';
      const title = card?.dataset.title || card?.querySelector('.name')?.textContent || '';
      // 乐观打开：把卡片上的 cover URL 直接当首图（浏览器大概率已缓存）
      openGalleryLb(code, title, {
        coverHint: cover.src,
        imagesUrl: `/api/library/${encodeURIComponent(code)}/gallery-images`,
      });
    }
    // 3) 演员标签 → 按演员筛选
    const actorTag = e.target.closest('.actor-tag');
    if (actorTag) {
      e.stopPropagation();
      e.preventDefault();
      actor = actorTag.dataset.actor;
      page = 1;
      updateUrl();
      load();
      return;
    }
    // 卡片其他位置不触发任何动作（不再打开本地文件夹）
  });

  /* ---------- 刷新状态轮询：按钮实时反映队列状态 ---------- */
  const prevRescanState = new Map(); // carid -> 'running' | 'queued'
  const lastCurrentMap = new Map();  // carid -> 最近一次 current snapshot（完成后用来显示样图数）
  async function refreshRescanStatus() {
    try {
      const res = await fetch('/api/library/rescan-status');
      if (!res.ok) return;
      const data = await res.json();
      const inProgress = new Map();
      if (data.current) {
        inProgress.set(data.current.carid, 'running');
        // 缓存 current 的 snapshot（含 samples_downloaded），完成后用得到
        lastCurrentMap.set(data.current.carid, data.current);
      }
      for (const q of (data.queued || [])) inProgress.set(q.carid, 'queued');

      document.querySelectorAll('.rescan-btn').forEach((btn) => {
        const code = btn.dataset.code;
        const st = inProgress.get(code);
        if (st) {
          btn.disabled = true;
          btn.classList.toggle('running', st === 'running');
          btn.classList.toggle('queued', st === 'queued');
          btn.classList.remove('done');
          btn.textContent = '↻';
          btn.title = st === 'running' ? '正在刷新…' : '队列中…';
        } else {
          const wasActive = prevRescanState.has(code);
          if (wasActive) {
            // 刚完成 → 取最后一次 current snapshot 拿样图数（data.current 已不在 inProgress，
            // 但本轮 status_snapshot 里有；用 lastCurrentMap 缓存）
            const lastSnap = lastCurrentMap.get(code);
            const sampleCount = lastSnap ? (lastSnap.samples_downloaded || 0) : 0;
            const sampleNote = sampleCount > 0 ? `（新下样图 ${sampleCount} 张）` : '';
            btn.classList.add('done');
            btn.classList.remove('running', 'queued');
            btn.textContent = '✓';
            btn.title = '刷新完成';
            setTimeout(() => {
              btn.classList.remove('done');
              btn.textContent = '↻';
              btn.title = '重新搜刮（NFO + 封面 + 样图）';
            }, 1800);
            // 只对"刚才确实在跑"的（不是 queue 中就取消）才弹 toast，避免误报
            if (prevRescanState.get(code) === 'running') {
              toast(`${code} 刷新完成 ${sampleNote}`);
            }
          } else {
            btn.disabled = false;
            btn.classList.remove('running', 'queued', 'done');
            btn.textContent = '↻';
            btn.title = '重新搜刮（NFO + 封面 + 样图）';
          }
        }
      });
      prevRescanState.clear();
      inProgress.forEach((v, k) => prevRescanState.set(k, v));
    } catch { /* 静默 */ }
  }

  /* ---------- 启动 ---------- */
  $('source').textContent = '本地影片库';
  await loadWarnings();
  await load();
  // 持续轮询状态（每 3 秒）
  setInterval(loadStatus, 3000);
  // 刷新按钮状态轮询（每 1.5 秒）
  refreshRescanStatus();
  setInterval(refreshRescanStatus, 1500);
}
