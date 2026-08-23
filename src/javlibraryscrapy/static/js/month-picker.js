// month-picker.js —— 双页面共用的月份选择器。
//
// 设计要点：
// - 入参全部走 opts（id / 状态读写 / 变化回调），避免 wanted/library 各自
//   实现一份。id 通过参数传入，两套实例互不冲突（wanted 用 w- 前缀）。
// - document click/keydown 监听只在模块级挂一次（_ensureDocHandlers 单例），
//   内部遍历所有 popup 决定关哪个。
//
// 依赖 utils.js 的 $。

import { $ } from './utils.js';

function parseMonthsData(months) {
  const years = new Map();
  let unknown = 0;
  for (const m of months) {
    if (m.month === 'unknown') { unknown = m.count; continue; }
    const parts = m.month.split('-');
    if (parts.length !== 2) continue;
    const y = parseInt(parts[0], 10);
    const mo = parseInt(parts[1], 10);
    if (!y || !mo) continue;
    if (!years.has(y)) years.set(y, new Map());
    years.get(y).set(mo, m.count);
  }
  return { years, unknown };
}

// 全局注册表：避免每个 setupMonthPicker() 都挂一对 document click/keydown 监听
// （多次调用会堆叠 handler）。改为模块级 Set 跟踪所有 popupId；
// document 上 click/keydown 各只挂一次，内部遍历 Set 决定关哪个 popup。
const _popups = new Set();
let _docBound = false;
function _ensureDocHandlers() {
  if (_docBound) return;
  _docBound = true;
  document.addEventListener('click', (e) => {
    for (const p of _popups) {
      const popup = $(p.popupId);
      if (!popup || popup.hidden) continue;
      if (e.target.closest(`#${p.popupId}`) || e.target.closest(`#${p.triggerId}`)) continue;
      popup.hidden = true;
      break;  // 同时只有一个 popup 是开的
    }
  });
  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    for (const p of _popups) {
      const popup = $(p.popupId);
      if (popup && !popup.hidden) { popup.hidden = true; break; }
    }
  });
}

export function setupMonthPicker(opts) {
  const {
    triggerId, popupId, yearPrevId, yearNextId, yearDisplayId,
    gridId, unknownId, clearId, triggerLabelId,
    getMonths, getMonth, setMonth, onChange,
  } = opts;
  _ensureDocHandlers();
  _popups.add({ popupId, triggerId });
  let popupYear = 0;

  function render() {
    const months = getMonths();
    const { years, unknown } = parseMonthsData(months);
    const yearList = [...years.keys()].sort((a, b) => a - b);
    const month = getMonth();

    // popupYear 默认跟 selected month 的年份走（首次进入弹层定位）；
    // label 里的年份始终用 selected month 解析出的真值，不跟 popupYear
    // （否则用户切年后 label 跟实际选中的月份对不上）。
    if (month && month !== 'unknown') {
      const y = parseInt(month.slice(0, 4), 10);
      if (y) popupYear = y;
    }
    if (!popupYear || (yearList.length && !yearList.includes(popupYear))) {
      popupYear = yearList.length ? yearList[yearList.length - 1] : new Date().getFullYear();
    }

    let label;
    if (month === 'unknown') label = '未知月份';
    else if (month) {
      // label 年份用 selected month 解析（不用 popupYear），
      // 避免 year-next 切年后 label 显示错误的年份
      const labelYear = parseInt(month.slice(0, 4), 10);
      const labelMonth = parseInt(month.slice(5, 7), 10);
      label = `${labelYear} 年 ${labelMonth} 月`;
    }
    else label = '全部月份';
    $(triggerLabelId).textContent = label;
    $(triggerId).classList.toggle('active', !!month);

    $(yearDisplayId).textContent = `${popupYear} 年`;
    $(yearPrevId).disabled = yearList.length > 0 && popupYear <= yearList[0];
    $(yearNextId).disabled = yearList.length > 0 && popupYear >= yearList[yearList.length - 1];

    const grid = $(gridId);
    grid.innerHTML = '';
    for (let m = 1; m <= 12; m++) {
      const count = years.get(popupYear)?.get(m) || 0;
      const key = `${popupYear}-${String(m).padStart(2, '0')}`;
      const cell = document.createElement('button');
      cell.type = 'button';
      cell.className = 'month-cell';
      const isActive = month === key;
      if (isActive) cell.classList.add('active');
      if (count === 0) cell.classList.add('empty');
      cell.disabled = count === 0 && !isActive;
      cell.innerHTML = `<span class="month-num">${m}月</span><span class="month-count${count === 0 ? ' empty' : ''}">${count || ''}</span>`;
      cell.addEventListener('click', () => {
        setMonth(key);
        closePopup();
        onChange(key);
      });
      grid.appendChild(cell);
    }

    const unkBtn = $(unknownId);
    unkBtn.classList.toggle('active', month === 'unknown');
    unkBtn.querySelector('.count').textContent = unknown || '';
    unkBtn.disabled = unknown === 0 && month !== 'unknown';
    $(clearId).disabled = !month;
  }

  function openPopup() { $(popupId).hidden = false; render(); }
  function closePopup() { $(popupId).hidden = true; }

  $(triggerId).addEventListener('click', (e) => {
    e.stopPropagation();
    if ($(popupId).hidden) openPopup();
    else closePopup();
  });
  $(yearPrevId).addEventListener('click', (e) => {
    e.stopPropagation();
    if (popupYear > 0) { popupYear--; render(); }
  });
  $(yearNextId).addEventListener('click', (e) => {
    e.stopPropagation();
    popupYear++;
    render();
  });
  $(unknownId).addEventListener('click', (e) => {
    e.stopPropagation();
    setMonth('unknown');
    closePopup();
    onChange('unknown');
  });
  $(clearId).addEventListener('click', (e) => {
    e.stopPropagation();
    setMonth('');
    closePopup();
    onChange('');
  });
  // document click/keydown 由模块级 _ensureDocHandlers() 统一处理（singleton），
  // 这里不再重复挂监听。

  return { render, openPopup, closePopup };
}
