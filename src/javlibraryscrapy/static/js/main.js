// main.js —— 前端入口。
//
// 职责只有两件事：
//   1. 根据 URL path 决定渲染 wanted 页还是 library 页（默认 wanted）
//   2. 把对应的 init*() 跑起来
//
// 模块加载顺序由 main.js 的 import 语句决定；浏览器原生 ESM 保证
// 叶子模块（utils.js）先于调用方（wanted.js / library.js）执行。

import { initWanted } from './wanted.js';
import { initLibrary } from './library.js';

const PAGE = location.pathname === '/library' ? 'library' : 'wanted';
document.body.classList.toggle('page-library', PAGE === 'library');
document.body.classList.toggle('page-wanted', PAGE === 'wanted');
document.getElementById(PAGE === 'library' ? 'nav-library' : 'nav-wanted')
  ?.classList.add('active');

// ---- NSFW 隐藏开关 ----
// 默认开启（图片 blur），用户点工具栏按钮后切换；状态写到 localStorage，
// 跨刷新 / 跨页面 / 跨浏览器会话都保留。wanted / library 两个按钮共用
// 一个 state key，所以切换任一按钮两个都同步。
const NSFW_KEY = 'jav-gallery-nsfw-hidden';
function applyNsfwState(hidden) {
  document.body.classList.toggle('nsfw-hidden', !!hidden);
  // 同步两个按钮的 aria-pressed + 文案
  const labelOn = '🔞 NSFW 显示';     // 当前是 hidden，按钮是"切回显示"
  const labelOff = '🔞 NSFW 隐藏';
  document.querySelectorAll('.nsfw-toggle').forEach((btn) => {
    btn.setAttribute('aria-pressed', hidden ? 'true' : 'false');
    btn.textContent = hidden ? labelOn : labelOff;
    btn.title = hidden
      ? '点击显示海报原图（当前已隐藏）'
      : '点击模糊海报（当前显示原图）';
  });
}
let nsfwHidden = true;  // 默认隐藏（图片 blur）
try {
  const saved = localStorage.getItem(NSFW_KEY);
  if (saved === 'false') nsfwHidden = false;  // 用户明确点过「显示」才覆盖
} catch { /* localStorage 不可用时静默回退默认 */ }
applyNsfwState(nsfwHidden);

// 绑定两个按钮（wanted / library 各一个），共用同一个 handler
function bindNsfwToggle(btnId) {
  const btn = document.getElementById(btnId);
  if (!btn) return;
  btn.addEventListener('click', () => {
    nsfwHidden = !nsfwHidden;
    try { localStorage.setItem(NSFW_KEY, nsfwHidden ? 'true' : 'false'); } catch {}
    applyNsfwState(nsfwHidden);
  });
}
bindNsfwToggle('btn-nsfw-w');
bindNsfwToggle('btn-nsfw-l');

if (PAGE === 'library') {
  initLibrary();
} else {
  initWanted();
}
