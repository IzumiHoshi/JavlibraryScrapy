// library.js —— /library 页面初始化（本地库浏览 + 单部补齐 + 月份/演员筛选）。
//
// 模块结构：
//   1. 状态/URL/分页/筛选
//   2. 月份选择器
//   3. 卡片渲染（renderActiveFilters / localCoverUrl / renderCard / render）
//   4. lib-status 分段渲染（renderLibStatusHtml / renderLibScanningHtml）
//   5. 数据加载（loadStatus / loadWarnings / load）
//   6. 事件绑定（搜索 / 排序 / 触发扫描 / 卡片点击 / 补齐进度轮询）
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
    // 补齐按钮：有视频 + 至少缺一种目标文件的卡片才显示
    // （替代旧的 .rescan-btn 刷新按钮：刷新会清旧封面，补齐保留一切已有）
    const incomplete = !(m.has_nfo && m.has_poster && m.has_fanart && m.sample_count > 0);
    const backfillBtn = m.has_video && incomplete
      ? `<button class="backfill-btn" data-code="${esc(m.carid)}" data-folder="${esc(m.folder)}" title="补齐缺失的文件（NFO / 海报 / Fanart / 样图；不触碰已有文件）">⟲</button>`
      : '';
    return `
      <div class="card${noVideoCls}" data-code="${esc(m.carid)}" data-folder="${esc(m.folder)}" tabindex="0">
        ${backfillBtn}
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

  /* ---------- 全库补齐：触发 + 进度轮询 ---------- */
  // 局部状态机：保存当前 job + 当前处理的车牌，供 UI 实时反映。
  let libBackfillState = { status: 'idle', job: null };

  function renderLibBackfillProgress() {
    const btn = $('btn-lib-backfill');
    const cancelBtn = $('btn-lib-backfill-cancel');
    const s = libBackfillState;
    const j = s.job;
    if (s.status === 'running' && j) {
      btn.disabled = true;
      btn.classList.add('running');
      cancelBtn.style.display = '';
      const cur = j.current_code ? ` → ${j.current_code}` : '';
      btn.textContent = `补齐中 ${j.backfilled}/${j.needs_backfill}${cur}`;
      btn.title = `进度：${j.backfilled}/${j.needs_backfill}（失败 ${j.failed}）`;
    } else if (s.status === 'done' && j) {
      btn.disabled = false;
      btn.classList.remove('running');
      cancelBtn.style.display = 'none';
      btn.textContent = '补齐缺失';
      btn.title = `已完成：补齐 ${j.backfilled} 部，失败 ${j.failed} 部`;
      toast(`全库补齐完成：补齐 ${j.backfilled}，失败 ${j.failed}`);
    } else if (s.status === 'error' && j) {
      btn.disabled = false;
      btn.classList.remove('running');
      cancelBtn.style.display = 'none';
      btn.textContent = '补齐缺失';
      btn.title = `错误：${j.error || '未知'}`;
      toast(`全库补齐失败：${j.error || '未知'}`);
    } else {
      btn.disabled = false;
      btn.classList.remove('running');
      cancelBtn.style.display = 'none';
      btn.textContent = '补齐缺失';
    }
  }

  async function pollBackfillStatus() {
    try {
      const res = await fetch('/api/library/backfill-status');
      if (!res.ok) return;
      const data = await res.json();
      libBackfillState = {
        status: data.status === 'idle' ? 'idle' : (data.job && data.job.status) || 'idle',
        job: data.job || null,
      };
      renderLibBackfillProgress();
      // 任务结束后刷一次列表 + 索引（让 has_nfo/poster/fanart 状态反映到卡片）
      if (data.status !== 'running' && prevBackfillWasRunning) {
        prevBackfillWasRunning = false;
        load();
      }
      if (data.status === 'running') {
        prevBackfillWasRunning = true;
      }
    } catch { /* 静默 */ }
  }
  let prevBackfillWasRunning = false;

  $('btn-lib-backfill').addEventListener('click', async () => {
    try {
      const res = await fetch('/api/library/backfill', { method: 'POST' });
      if (res.status === 409) return toast('已有补齐任务在运行');
      if (res.status === 503) return toast('未配置 LIBRARY_ROOT');
      if (!res.ok) throw new Error((await res.json()).detail || res.statusText);
      const data = await res.json();
      libBackfillState = { status: 'running', job: data.job };
      renderLibBackfillProgress();
      toast('已开始全库补齐…');
    } catch (e) {
      toast('触发补齐失败：' + e.message);
    }
  });

  $('btn-lib-backfill-cancel').addEventListener('click', async () => {
    try {
      const res = await fetch('/api/library/backfill-cancel', { method: 'POST' });
      if (!res.ok) throw new Error((await res.json()).detail || res.statusText);
      toast('已发取消信号');
    } catch (e) {
      toast('取消失败：' + e.message);
    }
  });

  /* ---------- 点击处理：补齐按钮 > 灯箱（卡片其他位置不再触发打开文件夹） ---------- */
  // 注：旧的 .rescan-btn 单部刷新按钮已被 .backfill-btn 取代（保留一切已有文件）。
  $('grid').addEventListener('click', async (e) => {
    // 1) 单部补齐按钮
    const bbtn = e.target.closest('.backfill-btn');
    if (bbtn) {
      e.stopPropagation();
      e.preventDefault();
      const carid = bbtn.dataset.code;
      try {
        bbtn.disabled = true;
        bbtn.classList.add('running');
        bbtn.textContent = '⟲';
        const res = await fetch(`/api/library/${encodeURIComponent(carid)}/backfill`, { method: 'POST' });
        const data = await res.json();
        if (!res.ok) throw new Error((data && data.detail) || res.statusText);
        const r = data.result || {};
        const reason = r.skipped_reason;
        if (data.ok) {
          const before = (r.plan_before && r.plan_before.missing_kinds) || [];
          const after = (r.plan_after && r.plan_after.missing_kinds) || [];
          const newlyFixed = before.filter(k => !after.includes(k));
          if (reason === 'complete') {
            toast(`${carid} 已完整，无需补齐`);
          } else if (newlyFixed.length === 0 && reason !== 'complete') {
            toast(`${carid} 已是最新（缺失：${before.join(' / ') || '无'}）`);
          } else {
            toast(`${carid} 补齐完成：${newlyFixed.join(' / ')}`);
          }
          bbtn.classList.add('done');
          bbtn.textContent = '✓';
          // 重新拉一次列表（has_nfo/poster/fanart 可能变了；sample_count 是 image_count 派生，不会立即变化）
          load();
        } else {
          toast(`补齐失败：${r.error || (data.detail || '未知错误')}`);
          bbtn.classList.add('failed');
          bbtn.textContent = '✗';
        }
        setTimeout(() => {
          bbtn.disabled = false;
          bbtn.classList.remove('running', 'done', 'failed');
          bbtn.textContent = '⟲';
        }, 2200);
      } catch (err) {
        toast('触发补齐失败：' + err.message);
        bbtn.disabled = false;
        bbtn.classList.remove('running', 'done', 'failed');
        bbtn.textContent = '⟲';
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

  /* ---------- 启动 ---------- */
  $('source').textContent = '本地影片库';
  await loadWarnings();
  await load();
  // 持续轮询状态（每 3 秒）
  setInterval(loadStatus, 3000);
  // 全库补齐进度轮询（每 1.5 秒）
  pollBackfillStatus();
  setInterval(pollBackfillStatus, 1500);
}
