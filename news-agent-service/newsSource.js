// 真实新闻数据源：直接打 Python 后端（backend/app/routers/market.py）的 /api/market，
// 取里面的 news 数组——跟看板"新闻"页面上显示的是同一份数据，不是另外接的接口。
// 拉取失败（比如 Python 后端没起来）就退回用 mockNewsData，保证 agent 服务不会因为
// 另一个服务挂了就直接不能用。
import { mockNewsData } from "./mockNewsData.js";

const NEWS_API_BASE = process.env.NEWS_API_BASE || "http://127.0.0.1:8001";
const CACHE_TTL_MS = 60 * 1000; // 新闻不是逐秒变的，缓存 1 分钟，别每个问题都重新拉一遍

let cache = { at: 0, data: null };

export async function getNews() {
  const now = Date.now();
  if (cache.data && now - cache.at < CACHE_TTL_MS) {
    return cache.data;
  }
  try {
    const res = await fetch(`${NEWS_API_BASE}/api/market`, { signal: AbortSignal.timeout(8000) });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const payload = await res.json();
    const news = Array.isArray(payload.news) ? payload.news : [];
    if (!news.length) throw new Error("news 数组是空的");
    cache = { at: now, data: news };
    return news;
  } catch (err) {
    console.warn(`[newsSource] 拉真实新闻失败（${err.message}），退回用 mock 数据`);
    return mockNewsData;
  }
}
