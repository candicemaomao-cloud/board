// 3 个工具，给 news-agent.js 里手写的 tool-calling 循环用。
// 用 @langchain/core 的 tool() 辅助函数定义——这是目前 LangChain.js 推荐的写法
// （取代早期的 DynamicStructuredTool 类），schema 用 zod 描述，模型会看着这份
// schema 自己决定要不要调用、传什么参数。
import { tool } from "@langchain/core/tools";
import { z } from "zod";
import { getNews } from "./newsSource.js";

// mock 股价——按用户的优先级列表，这一步先不接真实行情（Python 后端已经有真实
// 行情接口，真要接只要把下面这个查表换成一次 fetch，其他地方不用动）。
const MOCK_PRICES = {
  NVDA: { price: 217.52, change_pct: 1.84 },
  TSLA: { price: 356.16, change_pct: -0.62 },
  AAPL: { price: 313.45, change_pct: 1.15 },
  MSFT: { price: 512.3, change_pct: 0.44 },
  AVGO: { price: 368.71, change_pct: 2.47 },
  MU: { price: 933.28, change_pct: 0.02 },
};

function matchesKeyword(item, keyword) {
  const k = keyword.trim().toLowerCase();
  if (!k) return false;
  const haystack = [item.title, item.title_en, item.summary].filter(Boolean).join(" ").toLowerCase();
  return haystack.includes(k);
}

export const searchNewsTool = tool(
  async ({ keyword }) => {
    const news = await getNews();
    const hits = news.filter((item) => matchesKeyword(item, keyword));
    if (!hits.length) {
      return JSON.stringify({ count: 0, message: `没有找到包含"${keyword}"的新闻` });
    }
    return JSON.stringify({
      count: hits.length,
      items: hits.map((item) => ({
        title: item.title,
        source: item.source,
        kind: item.kind,
        ago: item.ago,
        published: item.published,
        url: item.url,
      })),
    });
  },
  {
    name: "search_news",
    description: "按关键词搜索新闻标题/摘要，返回匹配到的新闻列表（标题/来源/时间/链接）。关键词支持中英文，会同时匹配中文标题、英文标题和摘要。",
    schema: z.object({
      keyword: z.string().describe("要搜索的关键词，比如公司名、股票代码、事件名（如\"美联储\"、\"Nvidia\"、\"降息\"）"),
    }),
  }
);

export const getStockPriceTool = tool(
  async ({ symbol }) => {
    const code = symbol.trim().toUpperCase();
    const hit = MOCK_PRICES[code];
    if (!hit) {
      return JSON.stringify({
        symbol: code,
        found: false,
        message: `没有 ${code} 的模拟行情数据，当前只有这几个：${Object.keys(MOCK_PRICES).join("、")}`,
      });
    }
    return JSON.stringify({ symbol: code, found: true, ...hit });
  },
  {
    name: "get_stock_price",
    description: "查询股票现价和涨跌幅（目前是模拟数据，只覆盖几只常见美股，用于演示 agent 怎么组合调用多个工具）。",
    schema: z.object({
      symbol: z.string().describe("股票代码，比如 NVDA、TSLA"),
    }),
  }
);

export const getNewsContextTool = tool(
  async ({ keyword }) => {
    const news = await getNews();
    const hits = news.filter((item) => matchesKeyword(item, keyword));
    if (!hits.length) {
      return JSON.stringify({ count: 0, message: `没有找到跟"${keyword}"相关的新闻，没法做综合研判` });
    }
    const context = hits
      .map((item, i) => `【${i + 1}】${item.title}（${item.source}，${item.ago}）\n${item.summary || "(无摘要)"}`)
      .join("\n\n");
    return JSON.stringify({ count: hits.length, context });
  },
  {
    name: "get_news_context_for_analysis",
    description: "拿某个关键词相关的所有新闻原文（标题+摘要拼在一起），给模型做综合研判、写分析用——比 search_news 返回的信息更完整，但也更长，只在真的需要「综合分析」而不是「列个清单」的时候用。",
    schema: z.object({
      keyword: z.string().describe("要综合研判的主题关键词"),
    }),
  }
);

export const allTools = [searchNewsTool, getStockPriceTool, getNewsContextTool];
