// tooltip.js —— 鼠标 hover .local-badge 时显示本地库元数据卡片。
//
// 包含：
// - fetchLibraryEntry：带 in-memory 缓存的 GET /api/library/{carid}
// - showTooltip / hideTooltip：定位 + 显隐
// - tick / renderLocalTooltip：渲染 helper（tick 也被 library 页面的
//   renderCard 直接复用 —— 但那边是局部函数，这里是 wanted 页用的版本，
//   两者结构一致所以同名。
//
// 依赖 utils.js 的 $ / esc / fmtBytes。

import { $, esc, fmtBytes } from './utils.js';

const LIB_CACHE = new Map();

export async function fetchLibraryEntry(carid) {
  if (LIB_CACHE.has(carid)) return LIB_CACHE.get(carid);
  try {
    const res = await fetch(`/api/library/${encodeURIComponent(carid)}`);
    if (!res.ok) return null;
    const data = await res.json();
    LIB_CACHE.set(carid, data);
    return data;
  } catch { return null; }
}

const tooltipEl = $('tooltip');
let tooltipTimer = null;

export function showTooltip(target, html) {
  clearTimeout(tooltipTimer);
  tooltipEl.innerHTML = html;
  tooltipEl.hidden = false;
  const r = target.getBoundingClientRect();
  const tipR = tooltipEl.getBoundingClientRect();
  let left = r.right + 8;
  let top = r.top;
  if (left + tipR.width > window.innerWidth - 8) left = window.innerWidth - tipR.width - 8;
  if (left < 8) left = 8;
  if (top + tipR.height > window.innerHeight - 8) top = window.innerHeight - tipR.height - 8;
  if (top < 8) top = 8;
  tooltipEl.style.left = left + 'px';
  tooltipEl.style.top = top + 'px';
  requestAnimationFrame(() => tooltipEl.classList.add('show'));
}

export function hideTooltip() {
  tooltipEl.classList.remove('show');
  tooltipTimer = setTimeout(() => { tooltipEl.hidden = true; }, 150);
}

export function tick(on) {
  return `<span class="${on ? 'tip-tick' : 'tip-cross'}">${on ? '✓' : '✗'}</span>`;
}

export function renderLocalTooltip(entry) {
  const actors = (entry.actors || []).slice(0, 3).join(', ') +
    (entry.actors && entry.actors.length > 3 ? ` …+${entry.actors.length - 3}` : '');
  return `
    <div class="tip-row"><span class="tip-label">车牌</span><span class="tip-value">${esc(entry.carid)}</span></div>
    <div class="tip-path">${esc(entry.folder)}</div>
    <div class="tip-row"><span class="tip-label">视频</span><span class="tip-value">${entry.video_count} 个 · ${fmtBytes(entry.total_size_bytes)}</span></div>
    <div class="tip-row"><span class="tip-label">NFO</span><span class="tip-value">${tick(entry.has_nfo)}  poster ${tick(entry.has_poster)}  fanart ${tick(entry.has_fanart)}</span></div>
    <div class="tip-row"><span class="tip-label">演员</span><span class="tip-value">${esc(actors) || '—'}</span></div>
    <div class="tip-row"><span class="tip-label">修改</span><span class="tip-value">${esc((entry.modified || '').replace('T', ' ').slice(0, 16))}</span></div>
  `;
}
