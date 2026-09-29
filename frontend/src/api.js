import { clearSession, getToken } from './auth'

async function request(path, options = {}) {
  const { headers: extraHeaders, skipAuth, ...rest } = options
  const isForm = typeof FormData !== 'undefined' && rest.body instanceof FormData
  const headers = { ...(extraHeaders || {}) }
  if (!isForm && !headers['Content-Type']) headers['Content-Type'] = 'application/json'
  if (isForm) delete headers['Content-Type']
  const tok = getToken()
  if (!skipAuth && tok && !headers.Authorization) headers.Authorization = `Bearer ${tok}`
  const res = await fetch(path, { headers, ...rest })
  if (res.status === 401 && !skipAuth && !path.includes('/api/auth/login')) {
    clearSession()
    if (typeof window !== 'undefined' && !window.location.hash.includes('login')) {
      window.dispatchEvent(new CustomEvent('pnl-auth-expired'))
    }
  }
  if (res.status === 204) return null
  const data = await res.json().catch(() => ({}))
  if (!res.ok) {
    const detail = data.detail
    let message = res.statusText || '请求失败'
    if (typeof detail === 'string') message = detail
    else if (Array.isArray(detail)) message = detail.map((x) => x.msg || x).join('; ')
    throw new Error(message)
  }
  return data
}

function qs(params) {
  const sp = new URLSearchParams()
  Object.entries(params || {}).forEach(([k, v]) => {
    if (v != null && v !== '') sp.set(k, v)
  })
  const s = sp.toString()
  return s ? `?${s}` : ''
}

export const TEMPLATE_CSV = `date,symbol,side,pnl_amount,notes
2026-08-08,NVDA,LONG,320,周线突破
2026-08-08,AAPL,SHORT,-85,假跌破
`

export const api = {
  overview: (params) => request(`/api/analytics/overview${qs(params)}`),
  trades: (params) => request(`/api/trades${qs(params)}`),
  tags: () => request('/api/trades/meta/tags'),
  createTrade: (body) => request('/api/trades', { method: 'POST', body: JSON.stringify(body) }),
  deleteTrade: (id) => request(`/api/trades/${id}`, { method: 'DELETE' }),
  createSnapshot: (body) => request('/api/snapshots', { method: 'POST', body: JSON.stringify(body) }),
  updateSettings: (body) => request('/api/settings', { method: 'PUT', body: JSON.stringify(body) }),
  settings: () => request('/api/settings'),
  market: () => request('/api/market'),
  marketBoardCalendar: () => request('/api/market/board-calendar'),
  marketBoardCalendarEarnings: (params) => request(`/api/market/board-calendar/earnings${qs(params)}`),
  marketFearGreed: (params) => request(`/api/market/fear-greed${qs(params)}`),
  marketFearPanel: (params) => request(`/api/market/fear-panel${qs(params)}`),
  marketVixHistory: (params) => request(`/api/market/vix-history${qs(params)}`),
  marketFearPath: (params) => request(`/api/market/fear-path${qs(params)}`),
    tape: () => request('/api/market/tape'),
    quotes: (items) => request('/api/market/quotes', { method: 'POST', body: JSON.stringify({ items }) }),
  liveSymbols: () => request('/api/market/live-symbols'),
  addLiveSymbol: (body) => request('/api/market/live-symbols', { method: 'POST', body: JSON.stringify(body) }),
  removeLiveSymbol: (symbol) =>
    request(`/api/market/live-symbols/${encodeURIComponent(symbol)}`, { method: 'DELETE' }),
  replaceLiveSymbols: (items) =>
    request('/api/market/live-symbols', { method: 'PUT', body: JSON.stringify({ items }) }),
    macro: (group, params) => request(`/api/macro/${group}${qs(params)}`),
    macroHistory: (group, ids) => request(`/api/macro/${group}/history${qs({ ids: ids.join(',') })}`),
    fedRisk: (params) => request(`/api/macro/fed-risk${qs(params)}`),
    macroEventStudyOptions: () => request('/api/macro/event-study/options'),
    macroEventStudy: (params) => request(`/api/macro/event-study${qs(params)}`),
  strategyIndicators: () => request('/api/strategy/indicators'),
  strategyPlans: () => request('/api/strategy/plans'),
  createStrategy: (body) => request('/api/strategy/plans', { method: 'POST', body: JSON.stringify(body) }),
  updateStrategy: (id, body) => request(`/api/strategy/plans/${encodeURIComponent(id)}`, { method: 'PUT', body: JSON.stringify(body) }),
  deleteStrategy: (id) => request(`/api/strategy/plans/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  strategyCalc: (body) => request('/api/strategy/calc', { method: 'POST', body: JSON.stringify(body) }),
  strategyScan: (body) => request('/api/strategy/scan', { method: 'POST', body: JSON.stringify(body) }),
  strategyIntradayBt: (body) => request('/api/strategy/intraday-bt', { method: 'POST', body: JSON.stringify(body) }),
  strategyIntradayBtPool: (body) => request('/api/strategy/intraday-bt-pool', { method: 'POST', body: JSON.stringify(body) }),
  newStrategyBacktest: (body) => request('/api/newstrategy/backtest', { method: 'POST', body: JSON.stringify(body) }),
  stockWatches: () => request('/api/stock-watches'),
  createStockWatch: (body) => request('/api/stock-watches', { method: 'POST', body: JSON.stringify(body) }),
  updateStockWatch: (id, body) =>
    request(`/api/stock-watches/${encodeURIComponent(id)}`, { method: 'PUT', body: JSON.stringify(body) }),
  deleteStockWatch: (id) => request(`/api/stock-watches/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  setStockWatchMonitor: (id, enabled) =>
    request(`/api/stock-watches/${encodeURIComponent(id)}/monitor`, {
      method: 'PUT',
      body: JSON.stringify({ enabled: !!enabled }),
    }),
  setStockWatchPush: (id, enabled) =>
    request(`/api/stock-watches/${encodeURIComponent(id)}/push`, {
      method: 'PUT',
      body: JSON.stringify({ enabled: !!enabled }),
    }),
  refreshStockWatch: (id) =>
    request(`/api/stock-watches/${encodeURIComponent(id)}/refresh`, { method: 'POST' }),
  testStockWatchPush: (id) =>
    request(`/api/stock-watches/${encodeURIComponent(id)}/test-push`, { method: 'POST' }),
  arbPlans: () => request('/api/arb'),
  createArb: (body) => request('/api/arb', { method: 'POST', body: JSON.stringify(body) }),
  updateArb: (id, body) => request(`/api/arb/${encodeURIComponent(id)}`, { method: 'PUT', body: JSON.stringify(body) }),
  deleteArb: (id) => request(`/api/arb/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  runArb: (id) => request(`/api/arb/${encodeURIComponent(id)}/run`, { method: 'POST' }),
  pairUniverse: () => request('/api/pairs/universe'),
  pairScan: (body) => request('/api/pairs/scan', { method: 'POST', body: JSON.stringify(body || {}) }),
  pairScanCached: () => request('/api/pairs/scan'),
  pairScanStatus: () => request('/api/pairs/scan/status'),
  pairToStrategy: (body) => request('/api/pairs/to-strategy', { method: 'POST', body: JSON.stringify(body) }),
  intradayUniverse: () => request('/api/intraday/universe'),
  intradayScan: (body) => request('/api/intraday/scan', { method: 'POST', body: JSON.stringify(body || {}) }),
  intradayCached: () => request('/api/intraday/scan'),
  intradayStatus: () => request('/api/intraday/scan/status'),
  intradayBacktest: (body) => request('/api/intraday/backtest', { method: 'POST', body: JSON.stringify(body) }),
  intradayToStrategy: (body) => request('/api/intraday/to-strategy', { method: 'POST', body: JSON.stringify(body) }),
  fundamentals: (symbol) => request(`/api/fundamentals${qs({ symbol })}`),
  risk: (params) => request(`/api/risk${qs(params)}`),
  factorExposure: (body) => request('/api/factors/exposure', { method: 'POST', body: JSON.stringify(body) }),
  portfolio: (body) => request('/api/portfolio', { method: 'POST', body: JSON.stringify(body) }),
  creditMigrate: (body) => request('/api/portfolio/credit-migrate', { method: 'POST', body: JSON.stringify(body) }),
  budgetModels: () => request('/api/budget-models'),
  runBudgetModel: (body) => request('/api/budget-models/run', { method: 'POST', body: JSON.stringify(body) }),
  budgetTargetEntry: (body) =>
    request('/api/budget-models/target-entry', { method: 'POST', body: JSON.stringify(body) }),
  kakeyaDirections: () => request('/api/budget-models/kakeya/directions'),
  runKakeyaModel: (body) =>
    request('/api/budget-models/kakeya/run', { method: 'POST', body: JSON.stringify(body) }),
  riskPortfolios: () => request('/api/risk-portfolios'),
  riskPortfolio: (id) => request(`/api/risk-portfolios/${id}`),
  createRiskPortfolio: (body) => request('/api/risk-portfolios', { method: 'POST', body: JSON.stringify(body) }),
  updateRiskPortfolio: (id, body) =>
    request(`/api/risk-portfolios/${id}`, { method: 'PUT', body: JSON.stringify(body) }),
  setRiskPortfolioEnabled: (id, enabled) =>
    request(`/api/risk-portfolios/${id}/enable`, {
      method: 'PUT',
      body: JSON.stringify({ enabled: !!enabled }),
    }),
  scanRiskPortfolio: (id) => request(`/api/risk-portfolios/${id}/scan`, { method: 'POST' }),
  deleteRiskPortfolio: (id) => request(`/api/risk-portfolios/${id}`, { method: 'DELETE' }),
  alerts: () => request('/api/alerts'),
  createAlert: (body) => request('/api/alerts', { method: 'POST', body: JSON.stringify(body) }),
  updateAlert: (id, body) => request(`/api/alerts/${id}`, { method: 'PUT', body: JSON.stringify(body) }),
  setAlertPush: (id, body) =>
    request(`/api/alerts/${id}/push`, {
      method: 'PUT',
      body: JSON.stringify(typeof body === 'boolean' ? { enabled: body } : body),
    }),
  deleteAlert: (id) => request(`/api/alerts/${id}`, { method: 'DELETE' }),
  refreshAlerts: (force = false) => request(`/api/alerts/refresh${qs({ force })}`, { method: 'POST' }),
  alertNotify: () => request('/api/alerts/notify'),
  saveAlertNotify: (body) => request('/api/alerts/notify', { method: 'PUT', body: JSON.stringify(body) }),
  testAlertNotify: () => request('/api/alerts/notify/test', { method: 'POST' }),
  recipients: () => request('/api/recipients'),
  createRecipient: (body) => request('/api/recipients', { method: 'POST', body: JSON.stringify(body) }),
  updateRecipient: (id, body) => request(`/api/recipients/${id}`, { method: 'PUT', body: JSON.stringify(body) }),
  deleteRecipient: (id) => request(`/api/recipients/${id}`, { method: 'DELETE' }),
  testRecipient: (id) => request(`/api/recipients/${id}/test`, { method: 'POST' }),
  binanceQuote: (symbol) => request(`/api/binance/quote${qs({ symbol })}`),
  syncBinance: (body) => request('/api/binance/sync', { method: 'POST', body: JSON.stringify(body) }),
  importTrades: ({ file, text } = {}) => {
    const body = new FormData()
    if (file) body.append('file', file)
    if (text) body.append('text', text)
    return request('/api/trades/import', { method: 'POST', body })
  },
  optionsAnalysis: (params) => request(`/api/options${qs(params)}`),
  regressionScreener: (params) => request(`/api/screener/regression${qs(params)}`),
  stockScreenOptions: () => request('/api/stock-screen/options'),
  runStockScreen: (body) => request('/api/stock-screen/run', { method: 'POST', body: JSON.stringify(body) }),
  stockScreenPresets: () => request('/api/stock-screen/presets'),
  saveStockScreenPreset: (body) => request('/api/stock-screen/presets', { method: 'POST', body: JSON.stringify(body) }),
  deleteStockScreenPreset: (id) => request(`/api/stock-screen/presets/${id}`, { method: 'DELETE' }),
  bulkAddStockScreen: (rows) => request('/api/stock-screen/bulk-add', { method: 'POST', body: JSON.stringify({ rows }) }),
  stockEntryAnalysis: (id, body) => request(`/api/daily-watch/${id}/entry-analysis`, { method: 'POST', body: JSON.stringify(body) }),
  stockEntryAnalysisBySymbol: (symbol, body) => request(`/api/daily-watch/symbol/${encodeURIComponent(symbol)}/entry-analysis`, { method: 'POST', body: JSON.stringify(body) }),
  stockDetail: (id, section) => request(`/api/daily-watch/${id}/detail/${section}`),
  stockDetailBySymbol: (symbol, section) => request(`/api/daily-watch/symbol/${encodeURIComponent(symbol)}/detail/${section}`),
  stockAnalysisNotes: (id) => request(`/api/daily-watch/${id}/notes`),
  addStockAnalysisNote: (id, content) => request(`/api/daily-watch/${id}/notes`, { method: 'POST', body: JSON.stringify({ content }) }),
  dailyWatches: () => request('/api/daily-watch'),
  lookupDailyWatch: (symbol) => request(`/api/daily-watch/lookup${qs({ symbol })}`),
  createDailyWatch: (body) => request('/api/daily-watch', { method: 'POST', body: JSON.stringify(body) }),
  updateDailyWatch: (id, body) => request(`/api/daily-watch/${id}`, { method: 'PUT', body: JSON.stringify(body) }),
  deleteDailyWatch: (id) => request(`/api/daily-watch/${id}`, { method: 'DELETE' }),
  refreshDailyWatchPrice: (id) => request(`/api/daily-watch/${id}/refresh-price`, { method: 'POST' }),
  refreshDailyWatchOptions: (id) => request(`/api/daily-watch/${id}/refresh-options`, { method: 'POST' }),
  refreshDailyWatchRegression: (id) => request(`/api/daily-watch/${id}/refresh-regression`, { method: 'POST' }),
  dailyWatchAnalysis: (params) => request(`/api/daily-watch/analysis${qs(params)}`),
  stockJournals: (params) => request(`/api/stock-journal${qs(params)}`),
  createStockJournal: (body) => request('/api/stock-journal', { method: 'POST', body: JSON.stringify(body) }),
  updateStockJournal: (id, body) => request(`/api/stock-journal/${id}`, { method: 'PUT', body: JSON.stringify(body) }),
  appendStockJournal: (body) => request('/api/stock-journal/append', { method: 'POST', body: JSON.stringify(body) }),
  deleteStockJournal: (id) => request(`/api/stock-journal/${id}`, { method: 'DELETE' }),
  earningsStocks: (params) => request(`/api/earnings-stocks${qs(params)}`),
  addEarningsStock: (body) => request('/api/earnings-stocks/add', { method: 'POST', body: JSON.stringify(body) }),
  positions: (params) => request(`/api/positions${qs(params)}`),
  positionOutlook: (body) => request('/api/positions/outlook', { method: 'POST', body: JSON.stringify(body) }),
  createPosition: (body) => request('/api/positions', { method: 'POST', body: JSON.stringify(body) }),
  updatePosition: (id, body) => request(`/api/positions/${id}`, { method: 'PUT', body: JSON.stringify(body) }),
  closePosition: (id, body) => request(`/api/positions/${id}/close`, { method: 'POST', body: JSON.stringify(body) }),
  editClosedPosition: (id, body) =>
    request(`/api/positions/${id}/closed-record`, { method: 'PUT', body: JSON.stringify(body) }),
  deletePosition: (id) => request(`/api/positions/${id}`, { method: 'DELETE' }),
  plans: (params) => request(`/api/plans${qs(params)}`),
  createPlan: (body) => request('/api/plans', { method: 'POST', body: JSON.stringify(body) }),
  updatePlan: (id, body) => request(`/api/plans/${id}`, { method: 'PUT', body: JSON.stringify(body) }),
  deletePlan: (id) => request(`/api/plans/${id}`, { method: 'DELETE' }),
  cryptoCoins: (params) => request(`/api/crypto/coins${qs(params)}`),
  createCryptoCoin: (body) => request('/api/crypto/coins', { method: 'POST', body: JSON.stringify(body) }),
  updateCryptoCoin: (id, body) => request(`/api/crypto/coins/${id}`, { method: 'PUT', body: JSON.stringify(body) }),
  deleteCryptoCoin: (id) => request(`/api/crypto/coins/${id}`, { method: 'DELETE' }),
  cryptoRankings: (params) => request(`/api/crypto/rankings${qs(params)}`),
  cryptoSearch: (q) => request(`/api/crypto/search${qs({ q })}`),
  cryptoKlines: (id, params) => request(`/api/crypto/coins/${id}/klines${qs(params)}`),
  cryptoTech: (id, params) => request(`/api/crypto/coins/${id}/tech${qs(params)}`),
  cryptoIndicators: () => request('/api/crypto/indicators'),
  cryptoFearGreed: (params) => request(`/api/crypto/sentiment/fear-greed${qs(params)}`),
  cryptoOnchain: (params) => request(`/api/crypto/onchain${qs(params)}`),
  cryptoNews: (params) => request(`/api/crypto/news${qs(params)}`),
  newsPush: (scope) => request(`/api/news-push/${scope}`),
  saveNewsPush: (scope, body) => request(`/api/news-push/${scope}`, { method: 'PUT', body: JSON.stringify(body) }),
  runNewsPush: (scope, push = false) => request(`/api/news-push/${scope}/run?push=${push ? 'true' : 'false'}`, { method: 'POST' }),
  testNewsPush: (scope, body = {}) => request(`/api/news-push/${scope}/test`, { method: 'POST', body: JSON.stringify(body) }),
  cryptoStrategies: () => request('/api/crypto/strategies'),
  createCryptoStrategy: (body) => request('/api/crypto/strategies', { method: 'POST', body: JSON.stringify(body) }),
  updateCryptoStrategy: (id, body) => request(`/api/crypto/strategies/${id}`, { method: 'PUT', body: JSON.stringify(body) }),
  deleteCryptoStrategy: (id) => request(`/api/crypto/strategies/${id}`, { method: 'DELETE' }),
  refreshCryptoStrategies: () => request('/api/crypto/strategies/refresh', { method: 'POST' }),
  checkCryptoStrategy: (id) => request(`/api/crypto/strategies/${id}/check`, { method: 'POST' }),
  cryptoStrategyBacktest: (id, body) =>
    request(`/api/crypto/strategies/${id}/backtest`, { method: 'POST', body: JSON.stringify(body || {}) }),
  cryptoCustomCatalog: () => request('/api/crypto/custom-strategies/catalog'),
  cryptoCustomStrategies: () => request('/api/crypto/custom-strategies'),
  createCryptoCustomStrategy: (body) =>
    request('/api/crypto/custom-strategies', { method: 'POST', body: JSON.stringify(body) }),
  updateCryptoCustomStrategy: (id, body) =>
    request(`/api/crypto/custom-strategies/${id}`, { method: 'PUT', body: JSON.stringify(body) }),
  deleteCryptoCustomStrategy: (id) => request(`/api/crypto/custom-strategies/${id}`, { method: 'DELETE' }),
  checkCryptoCustomStrategy: (id) =>
    request(`/api/crypto/custom-strategies/${id}/check`, { method: 'POST' }),
  testPushCryptoCustomStrategy: (id) =>
    request(`/api/crypto/custom-strategies/${id}/test-push`, { method: 'POST' }),
  cryptoCustomBacktest: (body) =>
    request('/api/crypto/custom-strategies/backtest', { method: 'POST', body: JSON.stringify(body) }),
  login: (body) => request('/api/auth/login', { method: 'POST', body: JSON.stringify(body), skipAuth: true }),
  me: () => request('/api/auth/me'),
  permissionCatalog: () => request('/api/auth/permission-catalog'),
  listUsers: () => request('/api/auth/users'),
  createUser: (body) => request('/api/auth/users', { method: 'POST', body: JSON.stringify(body) }),
  updateUser: (id, body) => request(`/api/auth/users/${id}`, { method: 'PUT', body: JSON.stringify(body) }),
  deleteUser: (id) => request(`/api/auth/users/${id}`, { method: 'DELETE' }),
}
