// Copyright (c) 2025 relakkes@gmail.com
// Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
// Observe the official page's own response without issuing another request.
identity => {
  const current = () => location.protocol === 'https:' && ['www.douyin.com', 'douyin.com'].includes(location.hostname) &&
    [`/video/${identity}`, `/note/${identity}`].includes(location.pathname.replace(/\/$/, ''));
  const allowed = raw => {
    try { const u = new URL(raw, location.href); return u.protocol === 'https:' && !u.username && !u.password &&
      (!u.port || u.port === '443') && ['www.douyin.com', 'www-hj.douyin.com'].includes(u.hostname) &&
      u.pathname === '/aweme/v1/web/aweme/detail/'; } catch { return false; }
  };
  const address = raw => ({url_list: Array.isArray(raw?.url_list) ? raw.url_list.filter(v => typeof v === 'string' && v.length <= 4096).slice(0, 8) : []});
  const publish = data => {
    const row = data?.aweme_detail;
    if (!current() || typeof row?.aweme_id !== 'string' || row.aweme_id !== identity) return;
    const video = row.video || {};
    const images = row.images || row.image_post_info?.images || [];
    const rates = video.bit_rate || [];
    const projected = {aweme_id: identity, desc: typeof row.desc === 'string' ? row.desc.slice(0, 256000) : '',
      images: Array.isArray(images) ? images.slice(0, 61).map(address) : [], video: {
        width: video.width, height: video.height, is_bytevc1: video.is_bytevc1, is_h265: video.is_h265,
        play_addr: address(video.play_addr), cover: address(video.cover || video.origin_cover),
        bit_rate: Array.isArray(rates) ? rates.slice(0, 8).map(v => ({bit_rate:v.bit_rate,format:v.format,
          is_bytevc1:v.is_bytevc1,play_addr:address(v.play_addr)})) : []}};
    const encoded = JSON.stringify(projected);
    if (encoded.length <= 1024 * 1024 && new TextEncoder().encode(encoded).length <= 1024 * 1024)
      window.__SIYE_READING_DETAIL = projected;
  };
  const fetchOriginal = window.fetch;
  window.fetch = function(input, options) {
    const result = fetchOriginal.apply(this, arguments);
    const url = typeof input === 'string' || input instanceof URL ? String(input) : input?.url;
    const method = options?.method || input?.method || 'GET';
    if (method === 'GET' && allowed(url)) result.then(async response => {
      if (!response.ok || !allowed(response.url) || !current()) return;
      const copy = response.clone();
      const reader = copy.body?.getReader();
      if (!reader) return;
      const declared = copy.headers.get('content-length');
      if (declared && Number(declared) > 1024 * 1024) { await reader.cancel(); return; }
      let size = 0; const chunks = [];
      try {
        while (true) {
          const {done, value} = await reader.read(); if (done) break;
          size += value.length;
          if (size > 1024 * 1024) { await reader.cancel(); return; }
          chunks.push(value);
        }
        const bytes = new Uint8Array(size); let offset = 0;
        for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.length; }
        publish(JSON.parse(new TextDecoder().decode(bytes)));
      } finally { reader.releaseLock(); }
    }).catch(() => {});
    return result;
  };
  const openOriginal = XMLHttpRequest.prototype.open;
  const requests = new WeakMap();
  XMLHttpRequest.prototype.open = function(method, url) {
    requests.set(this, method === 'GET' && allowed(url));
    return openOriginal.apply(this, arguments);
  };
  const sendOriginal = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.send = function() {
    if (requests.get(this)) this.addEventListener('load', () => {
      if (this.status !== 200 || !allowed(this.responseURL) || !current()) return;
      try {
        const declared = this.getResponseHeader('content-length');
        if (declared && Number(declared) > 1024 * 1024) return;
        if (this.responseType === 'json') publish(this.response);
        else if ((!this.responseType || this.responseType === 'text') && this.responseText.length <= 1024 * 1024)
          publish(JSON.parse(this.responseText));
      } catch {}
    }, {once:true});
    return sendOriginal.apply(this, arguments);
  };
}
