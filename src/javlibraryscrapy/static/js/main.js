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

if (PAGE === 'library') {
  initLibrary();
} else {
  initWanted();
}
