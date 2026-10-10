// Copyright (c) 2025 relakkes@gmail.com
// Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
// Read only named current-item stores and an identity-checked detail DOM.
({ identity, kind }) => {
  const route = new URL(location.href);
  if (!['douyin.com', 'www.douyin.com'].includes(route.hostname) || route.protocol !== 'https:' ||
      ![`/video/${identity}`, `/note/${identity}`].includes(route.pathname.replace(/\/$/, ''))) return null;
  const address = raw => {
    if (!raw || typeof raw !== 'object') return undefined;
    const urls = raw.url_list || raw.urlList || [];
    return { url_list: Array.isArray(urls) ? urls.filter(v => typeof v === 'string' && v.length <= 4096).slice(0, 8) : [] };
  };
  const normalize = row => {
    const id = row.aweme_id ?? row.awemeId;
    // Large IDs in already-parsed JS numbers have lost precision.
    if ((typeof id !== 'string' && !Number.isSafeInteger(id)) || String(id) !== identity) return null;
    const raw = row.video || {};
    const rates = raw.bit_rate || raw.bitRate || [];
    const images = row.images || row.image_post_info?.images || row.imagePostInfo?.images || [];
    return {
      aweme_id: identity, desc: typeof row.desc === 'string' ? row.desc.slice(0, 256000) : '',
      images: Array.isArray(images) ? images.slice(0, 61).map(address) : [],
      video: {
        width: raw.width, height: raw.height, is_bytevc1: raw.is_bytevc1 ?? raw.isBytevc1,
        is_h265: raw.is_h265, play_addr: address(raw.play_addr || raw.playAddr),
        cover: address(raw.cover || raw.origin_cover || raw.originCover),
        bit_rate: Array.isArray(rates) ? rates.slice(0, 8).map(v => ({ bit_rate: v.bit_rate ?? v.bitRate,
          is_bytevc1: v.is_bytevc1 ?? v.isBytevc1, format: v.format, play_addr: address(v.play_addr || v.playAddr) })) : []
      }
    };
  };
  const roots = [window.__SIYE_READING_DETAIL, window._ROUTER_DATA, window.__INITIAL_STATE__, window.__NEXT_DATA__];
  for (const script of [...document.querySelectorAll('script[type="application/json"], script#RENDER_DATA, script#__NEXT_DATA__')].slice(0, 12)) {
    const text = script.textContent || '';
    if (text.length > 2 * 1024 * 1024) continue;
    try { roots.push(JSON.parse(script.id === 'RENDER_DATA' ? decodeURIComponent(text) : text)); } catch {}
  }
  const seen = new Set(); let count = 0;
  const visit = (node, depth) => {
    if (!node || typeof node !== 'object' || seen.has(node) || depth > 18 || ++count > 12000) return null;
    seen.add(node);
    const matched = normalize(node);
    if (matched) return matched;
    for (const key of Object.keys(node).slice(0, 200)) {
      const value = visit(node[key], depth + 1);
      if (value) return value;
    }
    return null;
  };
  for (const root of roots) { const found = visit(root, 0); if (found) return found; }
  if (kind !== 'video') return null;
  const canonicals = document.querySelectorAll('link[rel="canonical"]');
  if (canonicals.length !== 1) return null;
  try {
    const canonical = new URL(canonicals[0].href);
    if (canonical.protocol !== 'https:' || !['douyin.com', 'www.douyin.com'].includes(canonical.hostname) ||
        canonical.pathname !== `/video/${identity}`) return null;
  } catch { return null; }
  const visible = el => el.getClientRects().length > 0;
  const details = [...document.querySelectorAll('[data-e2e="video-detail"]')].filter(visible);
  if (details.length !== 1) return null;
  const infos = [...details[0].querySelectorAll('[data-e2e="detail-video-info"]')].filter(visible);
  const players = [...details[0].querySelectorAll('video')].filter(el => visible(el) &&
    !el.closest('[data-e2e*="recommend"], [data-e2e*="feed"], [data-e2e*="related"]'));
  if (infos.length !== 1 || players.length !== 1 || !document.title.endsWith(' - 抖音')) return null;
  const clean = text => text.trim().replace(/^第\d+集\s*[|｜]\s*/, '').replace(/\s+/g, ' ');
  const title = clean(document.title.slice(0, -5));
  const desc = (infos[0].innerText || '').trim().slice(0, 10000);
  if (!title || clean(desc.split('\n')[0]) !== title) return null;
  const player = players[0];
  return {aweme_id: identity, desc, video: {width: player.videoWidth, height: player.videoHeight,
    play_addr: {url_list: [player.currentSrc || player.src]}, cover: {url_list: [player.poster]}}};
}
