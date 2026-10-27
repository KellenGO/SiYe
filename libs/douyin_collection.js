// Copyright (c) 2025 relakkes@gmail.com
// Licensed under NON-COMMERCIAL LEARNING LICENSE 1.1
//
// 从官方收藏页内部读取一页收藏，只保留适配器需要的公开字段。
//
// 为什么不从后端签名请求：抖音的 listcollection 由 Argus 安全插件按页面
// 上下文校验（缺 uifid 时直接 403 "Blocked by ArgusSecurityPlugin
// Uifid Not Found"），后端自己拼的 a_bogus 与页面实际发出的请求并不等价。
// 页面自身的 fetch 带着浏览器上下文，无需手工签名；这里只做读取与投影，
// 不写平台数据。
async ({cursor, count}) => {
  const endpoint = new URL('https://www.douyin.com/aweme/v1/web/aweme/listcollection/');
  endpoint.searchParams.set('device_platform', 'webapp');
  endpoint.searchParams.set('aid', '6383');
  endpoint.searchParams.set('channel', 'channel_pc_web');
  endpoint.searchParams.set('count', String(count));
  endpoint.searchParams.set('cursor', String(cursor));
  let response;
  try {
    response = await fetch(endpoint.toString(), {method: 'POST', credentials: 'include'});
  } catch (error) {
    return {status: 0, body: null};
  }
  if (response.status !== 200) return {status: response.status, body: null};
  const raw = await response.text();
  let data;
  try {
    data = JSON.parse(raw);
  } catch (error) {
    return {status: response.status, body: null};
  }
  if (!data || typeof data !== 'object') return {status: response.status, body: null};
  const rows = Array.isArray(data.aweme_list) ? data.aweme_list : null;
  if (!rows) return {status: response.status, body: null};

  const boundedText = (value, limit) =>
    (typeof value === 'string' ? value.slice(0, limit) : null);
  const metric = value => (typeof value === 'number' && Number.isFinite(value) && value >= 0
    ? Math.floor(value) : null);
  // 只保留公开 CDN 地址，且每个字段有界；不保留任何签名或跟踪参数。
  const addresses = value => ({
    url_list: Array.isArray(value && value.url_list)
      ? value.url_list.filter(url => typeof url === 'string' && url.length <= 4096).slice(0, 8)
      : [],
  });
  const project = row => {
    if (!row || typeof row !== 'object') return null;
    const video = row.video && typeof row.video === 'object' ? row.video : {};
    const author = row.author && typeof row.author === 'object' ? row.author : null;
    const statistics = row.statistics && typeof row.statistics === 'object' ? row.statistics : null;
    const images = Array.isArray(row.images) ? row.images : [];
    const item = {
      aweme_id: row.aweme_id === null || row.aweme_id === undefined ? '' : String(row.aweme_id),
      desc: boundedText(row.desc, 4096),
      create_time: typeof row.create_time === 'number' ? row.create_time : null,
      // 图集用 images 表达（适配器据此不采信背景音乐时长），封面也从中取。
      images: images.slice(0, 9).map(addresses),
      video: {
        duration: typeof video.duration === 'number' ? video.duration : null,
        cover: addresses(video.cover),
        origin_cover: addresses(video.origin_cover),
        animated_cover: addresses(video.animated_cover),
        dynamic_cover: addresses(video.dynamic_cover),
      },
    };
    if (author) item.author = {nickname: boundedText(author.nickname, 200)};
    if (statistics) {
      item.statistics = {
        digg_count: metric(statistics.digg_count),
        collect_count: metric(statistics.collect_count),
        comment_count: metric(statistics.comment_count),
        share_count: metric(statistics.share_count),
        play_count: metric(statistics.play_count),
      };
    }
    return item;
  };
  const next = data.cursor === undefined ? data.max_cursor : data.cursor;
  return {
    status: response.status,
    body: {
      status_code: data.status_code,
      has_more: data.has_more,
      // 游标是 16 位整数：以字符串回传，避免调用方按浮点数处理。
      cursor: next === null || next === undefined ? null : String(next),
      aweme_list: rows.slice(0, 20).map(project).filter(Boolean),
    },
  };
}
