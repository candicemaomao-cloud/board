/**
 * 实时播报股票列表。
 * 把你想盯的代码填进 LIVE_STOCKS，保存后刷新「实时播报」页即可。
 *
 * source:
 *   'tradingview'  美股 / ETF / 指数，代码写成 NVDA，或完整 TradingView 代码 NASDAQ:NVDA
 *   'binance'      币安 U 本位，例如 SKHYUSDT
 */
export const LIVE_STOCKS = [
  { symbol: 'NVDA', name: '英伟达', source: 'tradingview' },
  { symbol: 'AAPL', name: '苹果', source: 'tradingview' },
  { symbol: 'TSLA', name: '特斯拉', source: 'tradingview' },
  { symbol: 'PODD', name: '医疗股', source: 'tradingview' },
  { symbol: 'MU', name: '美光半导体', source: 'tradingview' },
  { symbol: 'INTC', name: '英特尔', source: 'tradingview' },
  { symbol: 'SPCX', name: 'spcx', source: 'tradingview' },
  { symbol: 'TSM', name: '台积电', source: 'tradingview' },
  { symbol: 'AMD', name: 'AMD', source: 'tradingview' },
  { symbol: 'GOOG', name: '谷歌', source: 'tradingview' },
  { symbol: 'MUD', name: '做空美光', source: 'tradingview' },
  { symbol: 'MSFT', name: '微软', source: 'tradingview' },
  { symbol: 'WMT', name: '沃尔玛', source: 'tradingview' },
  { symbol: 'COST', name: 'Costco', source: 'tradingview' },
  { symbol: 'AMZN', name: '亚马逊', source: 'tradingview' },
  { symbol: 'PLD', name: '物流', source: 'tradingview' },
  { symbol: 'AMT', name: '房地产', source: 'tradingview' },
]

/** 持仓相对开仓价亏损达到或超过这个百分比时，画红线 */
export const LOSS_ALERT_PCT = 5
