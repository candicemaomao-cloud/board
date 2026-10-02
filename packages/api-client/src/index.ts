export type User = {
  id: number
  username: string
  role: string
  permissions: string[]
  total_amount?: number
}

export type DailyWatch = {
  id: number
  symbol: string
  sector: string
  current_price: number | null
  prev_close: number | null
  regression_line: number | null
  max_pain: number | null
  long_entry_price: number | null
  short_entry_price: number | null
  market_direction: string
  events?: string | null
  updated_at: string
}

export type ScreenRow = {
  symbol: string
  name?: string
  sector_zh?: string
  price?: number | null
  market_cap?: number | null
  revenue_growth_yoy?: number | null
  net_margin?: number | null
  pe?: number | null
  weekly_return?: number | null
  monthly_return?: number | null
  earnings_date?: string | null
  days_to_earnings?: number | null
  [key: string]: string | number | boolean | null | undefined
}

export type StockScreenPreset = { id: number; name: string; config: Record<string, unknown>; updated_at?: string | null }
export type AccountGoal = { total_amount?: number; principal: number; equity: number; available: number; target_profit: number; target_equity: number; remaining: number; progress_pct: number; monthly_return: number; assumed: { days?: number | null; years?: number | null; label: string } }
export type HomeMessage = { key: string; kind: 'macro' | 'earnings' | 'position'; title: string; detail: string; symbol?: string | null; tone: 'info' | 'warning' | 'positive' | 'negative' }
export type SystemRecipient = { id: number; name: string; notes?: string | null; channels?: string[]; telegram?: boolean; whatsapp?: boolean; wx?: boolean }

export type CryptoCoin = {
  id: number
  symbol: string
  name: string
  binance_symbol?: string | null
  price?: number | null
  change_24h?: number | null
  volume_24h?: number | null
  quote_source?: string | null
  error?: string | null
}

export type CryptoKline = { ts: number; open: number; high: number; low: number; close: number; volume: number }
export type CryptoStrategy = { id: number; name: string; symbol: string; timeframe: string; indicators: string[]; join: string; enabled: boolean; allow_push: boolean; last_hit?: boolean | null; last_price?: number | null; last_error?: string | null; last_checked_at?: string | null }
export type CryptoCustomStrategy = { id: number; name: string; strategy_name?: string; timeframe: string; watch_symbols: string[]; enabled: boolean; allow_push: boolean; last_direction?: string | null; last_action?: string | null; last_error?: string | null; last_checked_at?: string | null }

export type Trade = {
  id: number
  date: string
  symbol: string
  side: 'LONG' | 'SHORT'
  pnl_amount: number
  pnl_pct?: number | null
  tags: string[]
  notes?: string | null
  source: string
  created_at: string
}

export type Position = {
  id: number
  platform: string
  status: '未开始' | '开始' | '已结束'
  name: string
  quote_symbol?: string
  quote_source?: string
  shares: number
  open_price: number
  market_value: number
  open_amount: number
  fee: number
  close_amount?: number | null
  opened_on: string
  closed_on?: string | null
  expected_days: number
  holding_days: number
  pnl_amount: number
  pnl_pct?: number | null
  strategy_id?: number | null
  strategy_name?: string | null
  strategy_side?: 'long' | 'short' | null
  open_reason?: string | null
  close_reason?: string | null
  notes?: string | null
}

export type StrategyPlan = {
  id: number
  name: string
  side: 'long' | 'short' | string
  timeframe?: string
  source?: string | null
  notes?: string | null
}

export type TradePlan = {
  id: number
  kind: 'day' | 'week' | 'month'
  title: string
  content: string
  plan_date?: string | null
  created_at?: string | null
  updated_at?: string | null
}

export type MarketQuote = {
  symbol: string
  requested?: string
  name: string
  price: number | null
  day_pct: number
  week_pct: number
  kind?: string
  source?: string
  session_label?: string
}

export type StockJournal = {
  id: number
  log_date: string
  stance: '空' | '多' | '观望'
  content: string
  created_at?: string | null
  updated_at?: string | null
}

export interface TokenStore {
  get(): string | null
  set(token: string): void
  clear(): void
}

type ClientOptions = { baseUrl?: string; tokenStore: TokenStore; onUnauthorized?: () => void }

export class ApiError extends Error {
  constructor(message: string, public status: number) {
    super(message)
  }
}

function query(params?: Record<string, string | number | null | undefined>) {
  const values = new URLSearchParams()
  Object.entries(params || {}).forEach(([key, value]) => {
    if (value !== null && value !== undefined && value !== '') values.set(key, String(value))
  })
  const text = values.toString()
  return text ? `?${text}` : ''
}

export function createApiClient({ baseUrl = '', tokenStore, onUnauthorized }: ClientOptions) {
  async function request<T>(path: string, init: RequestInit = {}, authenticated = true): Promise<T> {
    const headers = new Headers(init.headers)
    if (init.body && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json')
    const token = tokenStore.get()
    if (authenticated && token) headers.set('Authorization', `Bearer ${token}`)
    let response: Response
    try {
      response = await fetch(`${baseUrl}${path}`, { ...init, headers })
    } catch {
      throw new ApiError('无法连接后端服务，请确认 FastAPI 已启动并检查 API 地址', 0)
    }
    const payload = response.status === 204 ? null : await response.json().catch(() => ({}))
    if (!response.ok) {
      if (response.status === 401 && authenticated) {
        tokenStore.clear()
        onUnauthorized?.()
      }
      const detail = payload?.detail
      const message = typeof detail === 'string'
        ? detail
        : Array.isArray(detail) ? detail.map(item => item?.msg || String(item)).join('；') : response.statusText || '请求失败'
      throw new ApiError(message, response.status)
    }
    return payload as T
  }

  return {
    login: (username: string, password: string) => request<{ access_token: string; user: User }>(
      '/api/auth/login', { method: 'POST', body: JSON.stringify({ username, password }) }, false,
    ),
    me: () => request<User>('/api/auth/me'),
    accountGoal: () => request<AccountGoal>('/api/settings'),
    homeMessages: () => request<{ date: string; items: HomeMessage[] }>('/api/home/messages'),
    dismissHomeMessage: (key: string) => request<void>(`/api/home/messages/${encodeURIComponent(key)}`, { method: 'DELETE' }),
    recipients: () => request<{ items: SystemRecipient[] }>('/api/recipients'),
    dailyWatches: () => request<DailyWatch[]>('/api/daily-watch'),
    cryptoCoins: () => request<{ items: CryptoCoin[] }>('/api/crypto/coins'),
    trades: () => request<Trade[]>('/api/trades'),
    positions: (platform?: string) => request<Position[]>(`/api/positions${query({ platform })}`),
    createPosition: (body: unknown) => request<Position>('/api/positions', { method: 'POST', body: JSON.stringify(body) }),
    updatePosition: (id: number, body: unknown) => request<Position>(`/api/positions/${id}`, { method: 'PUT', body: JSON.stringify(body) }),
    deletePosition: (id: number) => request<void>(`/api/positions/${id}`, { method: 'DELETE' }),
    strategyPlans: () => request<StrategyPlan[]>('/api/strategy/plans'),
    createStrategyPlan: (body: unknown) => request<StrategyPlan>('/api/strategy/plans', { method: 'POST', body: JSON.stringify(body) }),
    quotes: (items: Array<{ symbol: string; name?: string | null; source?: string | null }>) =>
      request<{ quotes: Array<{ symbol?: string; requested?: string; name?: string; price?: number | null; day_pct?: number | null; session?: string; session_label?: string; source?: string }>; market?: { label?: string; live?: boolean; key?: string; next_open?: number } }>(
        '/api/market/quotes', { method: 'POST', body: JSON.stringify({ items }) },
      ),
    stockEntryAnalysis: (id: number, body: unknown) => request<any>(`/api/daily-watch/${id}/entry-analysis`, { method: 'POST', body: JSON.stringify(body) }),
    stockEntryAnalysisBySymbol: (symbol: string, body: unknown) => request<any>(`/api/daily-watch/symbol/${encodeURIComponent(symbol)}/entry-analysis`, { method: 'POST', body: JSON.stringify(body) }),
    plans: (kind?: string) => request<{ items: TradePlan[] }>(`/api/plans${query({ kind })}`),
    createPlan: (body: unknown) => request<TradePlan>('/api/plans', { method: 'POST', body: JSON.stringify(body) }),
    deletePlan: (id: number) => request<void>(`/api/plans/${id}`, { method: 'DELETE' }),
    marketTape: () => request<{ indices: MarketQuote[]; market?: { label?: string } }>('/api/market/tape'),
    boardCalendar: () => request<{ today: string; by_date?: Record<string, Array<{ date: string; days: number; kind: string; key: string; name: string; ref?: string; open?: string | null }>> }>('/api/market/board-calendar'),
    macroGroup: (group: string) => request<any>(`/api/macro/${encodeURIComponent(group)}`),
    marketNews: () => request<{ news?: Array<{ title: string; url: string; source?: string; ago?: string; kind?: string }> }>('/api/market'),
    stockJournals: () => request<{ items: StockJournal[] }>('/api/stock-journal'),
    stockDetail: <T = unknown>(id: number, section: string) => request<T>(`/api/daily-watch/${id}/detail/${section}`),
    stockDetailBySymbol: <T = unknown>(symbol: string, section: string) => request<T>(`/api/daily-watch/symbol/${encodeURIComponent(symbol)}/detail/${section}`),
    screenOptions: () => request<{ sectors: string[] }>('/api/stock-screen/options'),
    runScreen: (body: unknown) => request<{ count: number; items: ScreenRow[]; source?: string; universe?: string; universe_count?: number; prefilter_count?: number; advanced_pass_count?: number; scanned_count?: number; missing_count?: number; price_period?: string; financial_period?: string }>('/api/stock-screen/run', { method: 'POST', body: JSON.stringify(body) }),
    screenPresets: () => request<{ items: StockScreenPreset[] }>('/api/stock-screen/presets'),
    saveScreenPreset: (body: unknown) => request<StockScreenPreset>('/api/stock-screen/presets', { method: 'POST', body: JSON.stringify(body) }),
    deleteScreenPreset: (id: number) => request<void>(`/api/stock-screen/presets/${id}`, { method: 'DELETE' }),
    addScreenRows: (rows: ScreenRow[]) => request<{ added: string[]; skipped: string[]; failed: string[] }>('/api/stock-screen/bulk-add', { method: 'POST', body: JSON.stringify({ rows }) }),
    fearPanel: () => request<unknown>(`/api/market/fear-panel${query({})}`),
    cryptoKlines: (id: number, interval = '1d', limit = 90) => request<{ symbol: string; interval: string; bars: CryptoKline[] }>(`/api/crypto/coins/${id}/klines${query({ interval, limit })}`),
    cryptoTech: (id: number, interval = '1d') => request<any>(`/api/crypto/coins/${id}/tech${query({ interval })}`),
    cryptoOnchain: (symbol: string) => request<any>(`/api/crypto/onchain${query({ symbol })}`),
    cryptoNews: (limit = 40) => request<any>(`/api/crypto/news${query({ limit })}`),
    cryptoIndicators: () => request<any>('/api/crypto/indicators'),
    cryptoStrategies: () => request<{ items: CryptoStrategy[] }>('/api/crypto/strategies'),
    cryptoCustomStrategies: () => request<{ items: CryptoCustomStrategy[] }>('/api/crypto/custom-strategies'),
  }
}
