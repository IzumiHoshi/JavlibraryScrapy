// lightbox.js —— 单图灯箱 + 多图（cover + samples）灯箱。
//
// 单图灯箱（#lightbox）：wanted/library 都在用，但目前 JS 里没有引用方
// （保留以备扩展）。多图灯箱（#gallery-lb）：wanted/library 卡片点封面
// 都会触发，由 openGalleryLb() 统一处理；不同页面的 gallery-images 端点
// 由调用方传 imagesUrl，保持 helper 单一来源。
//
// 依赖 utils.js 的 $ / esc。

import { $, esc } from './utils.js';

/* ---------------- 单图灯箱（保留，未来扩展用） ---------------- */
const lightboxEl = $('lightbox');
const lightboxImg = $('lightbox-img');
const lightboxCap = $('lightbox-cap');

export function showLightbox(src, caption) {
  if (!src) return;
  lightboxImg.src = src;
  lightboxCap.textContent = caption || '';
  lightboxEl.hidden = false;
  requestAnimationFrame(() => lightboxEl.classList.add('show'));
}

export function hideLightbox() {
  lightboxEl.classList.remove('show');
  setTimeout(() => {
    lightboxEl.hidden = true;
    lightboxImg.src = '';
    lightboxCap.textContent = '';
  }, 180);
}

lightboxEl.addEventListener('click', hideLightbox);
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && lightboxEl.classList.contains('show')) hideLightbox();
});

/* ---------------- 多图灯箱（cover + samples，wanted/library 共用） ---------------- */
const galleryLbEl = $('gallery-lb');
const galleryLbImg = $('glb-img');
const galleryLbTitle = $('glb-title');
const galleryLbThumbs = $('glb-thumbs');
const galleryLbPrev = $('glb-prev');
const galleryLbNext = $('glb-next');
let galleryImages = [];  // [{ url, label }]
let galleryIndex = 0;
// 图像解码缓存：url → Promise<HTMLImageElement>，避免重复解码同一张图
const imgDecodeCache = new Map();
// 当前车牌的 gallery-images 请求 Promise（同一车牌重复打开复用）
let galleryFetchInflight = null;

// 预加载 URL（仅写入 imgDecodeCache，不阻塞 UI）
function preloadImage(url) {
  if (!url || imgDecodeCache.has(url)) return imgDecodeCache.get(url);
  const p = new Promise((resolve, reject) => {
    const img = new Image();
    img.decoding = 'async';
    img.onload = () => { if (img.decode) img.decode().then(() => resolve(img), () => resolve(img)); else resolve(img); };
    img.onerror = () => reject(new Error('load fail: ' + url));
    img.src = url;
  });
  imgDecodeCache.set(url, p);
  p.catch(() => imgDecodeCache.delete(url));  // 失败可重试
  return p;
}

// 预加载相邻图片（±2 张），让翻页无延迟
function prefetchAdjacent(idx) {
  if (!galleryImages.length) return;
  for (const off of [-2, -1, 1, 2]) {
    const i = ((idx + off) % galleryImages.length + galleryImages.length) % galleryImages.length;
    preloadImage(galleryImages[i].url);
  }
}

function showGalleryAt(idx) {
  if (!galleryImages.length) return;
  galleryIndex = (idx + galleryImages.length) % galleryImages.length;
  const item = galleryImages[galleryIndex];
  // 异步解码：img.decode() 返回 Promise，等解码完成再显示，避免半透明闪烁
  // 注意：不要直接 removeAttribute('src')，否则旧图瞬间消失、新图还没回来时一片黑。
  // 先记下当前 src，如果是同一 URL（已经缓存或就是同一张）就跳过预热，避免闪一下。
  const prevSrc = galleryLbImg.getAttribute('src') || '';
  if (prevSrc !== item.url) {
    // 占位：保留旧图直到新图解码完成（不再 removeAttribute，避免闪烁）
    galleryLbImg.alt = `加载中：${item.label || ''}`;
  }
  preloadImage(item.url).then((img) => {
    // 期间用户可能已经切到下一张 → 校验 index 一致才赋值
    if (galleryIndex === ((idx + galleryImages.length) % galleryImages.length)) {
      galleryLbImg.src = item.url;
      galleryLbImg.alt = item.label || '';
    }
  }).catch(() => {
    // 失败：保留旧图（coverHint 或上一张），仅改 alt 提示
    galleryLbImg.alt = `加载失败：${item.label || ''}（已显示上一张）`;
  });
  // 高亮缩略图 + 滚动到可视区
  Array.from(galleryLbThumbs.children).forEach((el, i) => {
    el.classList.toggle('active', i === galleryIndex);
  });
  const active = galleryLbThumbs.children[galleryIndex];
  if (active) {
    const rect = active.getBoundingClientRect();
    const tRect = galleryLbThumbs.getBoundingClientRect();
    if (rect.left < tRect.left || rect.right > tRect.right) {
      active.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'center' });
    }
  }
  galleryLbPrev.disabled = false;
  galleryLbNext.disabled = false;
  // 提前预热相邻图（不阻塞当前显示）
  prefetchAdjacent(galleryIndex);
}

function hideGalleryLb() {
  galleryLbEl.classList.remove('show');
  setTimeout(() => {
    galleryLbEl.hidden = true;
    galleryLbImg.src = '';
    galleryImages = [];
    galleryIndex = 0;
    galleryLbThumbs.innerHTML = '';
  }, 180);
}

// 开放给 openGalleryLb 调用：传入"卡片当前已有的 cover URL"，做到乐观打开
export async function openGalleryLb(carid, title, options) {
  options = options || {};
  const coverHint = options.coverHint || '';  // 卡片上已有的 cover URL（已可能被浏览器缓存）
  // 不同页面的 gallery-images 端点路径不同（wanted → /api/wanted/...，
  // library → /api/library/...）。让调用方传完整 URL，保持 helper 单一来源。
  const imagesUrl = options.imagesUrl || `/api/wanted/${encodeURIComponent(carid)}/gallery-images`;

  galleryLbEl.hidden = false;
  galleryLbEl.classList.add('show');
  galleryLbTitle.textContent = `${carid}${title ? ' · ' + title : ''}（加载中…）`;
  galleryLbImg.removeAttribute('src');
  galleryLbThumbs.innerHTML = '<div class="glb-empty">加载中…</div>';

  // 乐观策略：立刻把卡片上的 cover URL 作为首图显示（浏览器多已缓存，直接秒出）
  if (coverHint) {
    galleryLbImg.src = coverHint;
    galleryLbImg.alt = '封面';
  }

  // 同一 (carid, imagesUrl) 组合的 inflight 请求复用（避免用户连点触发 N 个并发 fetch）。
  // 必须把 imagesUrl 也带进 key：同一车牌在 wanted 页和 library 页指向不同端点
  // （/api/wanted/.../gallery-images vs /api/library/.../gallery-images），如果
  // 只用 carid 缓存会拿到对方的响应。
  const inflightKey = `${carid}::${imagesUrl}`;
  if (!galleryFetchInflight || galleryFetchInflight.key !== inflightKey) {
    galleryFetchInflight = {
      key: inflightKey,
      carid,
      promise: fetch(imagesUrl)
        .then(async (r) => {
          if (!r.ok) throw new Error((await r.json()).error || r.statusText);
          return r.json();
        }),
    };
  }

  try {
    const data = await galleryFetchInflight.promise;
    galleryFetchInflight = null;
    // 拼装图片列表：cover → poster → fanart → sample_*.jpg。
    // wanted 端 gallery-images 只有 cover + samples 两字段（旧版兼容），
    // 新版 library 端还会带 poster/fanart。统一在 helper 内处理。
    const urls = [];
    if (data.cover) urls.push({ url: data.cover, label: '封面' });
    if (data.poster) urls.push({ url: data.poster, label: '海报' });
    if (data.fanart) urls.push({ url: data.fanart, label: 'Fanart' });
    (data.samples || []).forEach((u, i) => urls.push({ url: u, label: `樣品 ${i + 1}` }));
    if (!urls.length) {
      galleryLbTitle.textContent =
        `${carid}${title ? ' · ' + title : ''} — 本地无图`;
      galleryLbThumbs.innerHTML = '<div class="glb-empty">本地没有图片</div>';
      galleryLbImg.removeAttribute('src');
      return;
    }
    galleryImages = urls;
    // 标题里只展示实际存在的类型（samples 数算"样品"）。
    const summary = urls.map((u) => u.label).join('、');
    galleryLbTitle.textContent =
      `${carid}${title ? ' · ' + title : ''}（${urls.length} 张：${summary}）`;
    // 缩略图懒加载；可见性由 CSS 决定（display:flex 容器内全部可见）
    galleryLbThumbs.innerHTML = urls.map((it, i) =>
      `<span class="glb-thumb" data-idx="${i}"><img src="${esc(it.url)}" loading="lazy" decoding="async" alt=""></span>`
    ).join('');
    // 缩略图原图也丢进 decode 缓存（用户点缩略图时无延迟）
    urls.forEach((u) => preloadImage(u.url));
    showGalleryAt(0);
  } catch (e) {
    galleryFetchInflight = null;
    // 失败时若 coverHint 已经显示了，就保留；否则提示
    if (!coverHint || !galleryLbImg.src) {
      galleryLbImg.removeAttribute('src');
    }
    galleryLbTitle.textContent =
      `${carid}${title ? ' · ' + title : ''} — 加载失败：${e.message}`;
    galleryLbThumbs.innerHTML = '';
  }
}

$('glb-close').addEventListener('click', hideGalleryLb);
galleryLbEl.addEventListener('click', (e) => {
  // 点击背景关闭；缩略图/按钮自身处理时不冒泡
  if (e.target === galleryLbEl || e.target.classList.contains('glb-main')) hideGalleryLb();
});
galleryLbPrev.addEventListener('click', (e) => {
  e.stopPropagation();
  if (galleryImages.length) showGalleryAt(galleryIndex - 1);
});
galleryLbNext.addEventListener('click', (e) => {
  e.stopPropagation();
  if (galleryImages.length) showGalleryAt(galleryIndex + 1);
});
galleryLbThumbs.addEventListener('click', (e) => {
  const thumb = e.target.closest('.glb-thumb');
  if (!thumb) return;
  e.stopPropagation();
  const idx = parseInt(thumb.dataset.idx, 10);
  if (!Number.isNaN(idx)) showGalleryAt(idx);
});
document.addEventListener('keydown', (e) => {
  if (!galleryLbEl.classList.contains('show')) return;
  if (e.key === 'Escape') hideGalleryLb();
  else if (e.key === 'ArrowLeft') showGalleryAt(galleryIndex - 1);
  else if (e.key === 'ArrowRight') showGalleryAt(galleryIndex + 1);
});
