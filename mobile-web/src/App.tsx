import { createContext, useContext, useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Navigate, NavLink, Route, Routes, useLocation, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { Activity, ArrowLeft, BarChart3, Bell, Bitcoin, BookOpenText, CalendarDays, CandlestickChart, ChevronDown, ChevronRight, ChevronUp, CircleDollarSign, Gauge, Home, ListFilter, ListTree, LogOut, Newspaper, Pencil, Plus, ReceiptText, RefreshCw, ScrollText, Trash2, UserRound, WalletCards, X } from 'lucide-react'
import { Button, Form, Input, SearchBar, TabBar, Toast } from 'antd-mobile'
import type { CryptoCoin, CryptoCustomStrategy, CryptoKline, CryptoStrategy, DailyWatch, HomeMessage, MarketQuote, Position, ScreenRow, StockJournal, StockScreenPreset, StrategyPlan, SystemRecipient, Trade, TradePlan, User } from '@pnl/api-client'
import { api, tokenStore } from './main'

const USER_KEY = 'pnl_mobile_user'
type AuthValue = { user: User | null; login: (name: string, password: string) => Promise<void>; logout: () => void }
const AuthContext = createContext<AuthValue | null>(null)
function useAuth() { return useContext(AuthContext)! }

function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(() => {
    try { return JSON.parse(localStorage.getItem(USER_KEY) || 'null') } catch { return null }
  })
  const logout = () => { tokenStore.clear(); localStorage.removeItem(USER_KEY); setUser(null) }
  useEffect(() => { window.addEventListener('pnl-mobile-auth-expired', logout); return () => window.removeEventListener('pnl-mobile-auth-expired', logout) }, [])
  const login = async (name: string, password: string) => {
    const result = await api.login(name, password)
    tokenStore.set(result.access_token); localStorage.setItem(USER_KEY, JSON.stringify(result.user)); setUser(result.user)
  }
  return <AuthContext.Provider value={{ user, login, logout }}>{children}</AuthContext.Provider>
}

function LoginPage() {
  const { user, login } = useAuth()
  const [username, setUsername] = useState(''), [password, setPassword] = useState(''), [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  if (user) return <Navigate to="/" replace />
  async function submit() {
    setBusy(true); setError('')
    try { await login(username.trim(), password); Toast.show({ icon: 'success', content: '登录成功' }) }
    catch (err) { const message = err instanceof Error ? err.message : '登录失败'; setError(message); Toast.show({ icon: 'fail', content: message }) }
    finally { setBusy(false) }
  }
  return <main className="login-page"><div className="login-glow"/><div className="login-card">
    <div className="brand-mark">P&L</div><h1>掌上投资看板</h1><p>查看股票、筛选结果与研究记录</p>
    <Form layout="vertical" className="mobile-form" onFinish={submit} footer={<Button block color="primary" size="large" loading={busy} disabled={!username || !password} type="submit">登录</Button>}>
      <Form.Item label="账号"><Input autoComplete="username" value={username} onChange={setUsername} clearable placeholder="请输入账号" /></Form.Item>
      <Form.Item label="密码"><Input type="password" autoComplete="current-password" value={password} onChange={setPassword} clearable placeholder="请输入密码" /></Form.Item>
      {error && <div className="form-error">{error}</div>}
    </Form>
  </div></main>
}

function Shell() {
  const { user } = useAuth()
  const location = useLocation(), navigate = useNavigate()
  if (!user) return <Navigate to="/login" replace />
  const activeKey = location.pathname.startsWith('/stocks') ? '/stocks'
    : location.pathname.startsWith('/crypto') ? '/crypto'
      : location.pathname.startsWith('/me') ? '/me' : '/'
  return <div className="app-shell">
    <div className="page-wrap"><Routes><Route index element={<HomePage/>}/><Route path="macro/:group" element={<MacroDetailPage/>}/><Route path="stocks" element={<StockHubPage/>}/><Route path="stocks/list" element={<StocksPage/>}/><Route path="stocks/positions" element={<AssetPositionsPage asset="stock"/>}/><Route path="stocks/screen" element={<ScreenerPage/>}/><Route path="stocks/screen/results" element={<ScreenerResultsPage/>}/><Route path="stocks/live" element={<StockLivePage/>}/><Route path="stocks/journal" element={<StockJournalPage/>}/><Route path="stocks/news" element={<StockNewsPage/>}/><Route path="stocks/symbol/:symbol" element={<StockDetailPage/>}/><Route path="stocks/:id/:symbol" element={<StockDetailPage/>}/><Route path="crypto" element={<CryptoPage/>}/><Route path="crypto/news" element={<CryptoNewsPage/>}/><Route path="crypto/positions" element={<AssetPositionsPage asset="crypto"/>}/><Route path="crypto/strategies" element={<CryptoStrategiesPage/>}/><Route path="crypto/custom-strategies" element={<CryptoCustomStrategiesPage/>}/><Route path="crypto/:id/:symbol" element={<CryptoDetailPage/>}/><Route path="me" element={<MePage/>}/><Route path="me/plans" element={<PlansPage/>}/><Route path="me/positions" element={<PositionsPage/>}/><Route path="me/trades" element={<TradesPage/>}/><Route path="me/recipients" element={<RecipientsPage/>}/><Route path="*" element={<Navigate to="/" replace/>}/></Routes></div>
    <nav className="bottom-nav"><TabBar activeKey={activeKey} onChange={navigate}><TabBar.Item key="/" icon={<Home/>} title="首页"/><TabBar.Item key="/stocks" icon={<BarChart3/>} title="股票"/><TabBar.Item key="/crypto" icon={<Bitcoin/>} title="虚拟币"/><TabBar.Item key="/me" icon={<UserRound/>} title="我的"/></TabBar></nav>
  </div>
}

function QueryState({ loading, error, retry }: { loading: boolean; error: unknown; retry: () => void }) {
  if (loading) return <div className="state-card"><span className="spinner"/>正在读取数据</div>
  if (error) return <div className="state-card error">{error instanceof Error ? error.message : '加载失败'}<button onClick={retry}>重试</button></div>
  return null
}
const money = (value?: number | null) => value == null ? '—' : `$${Number(value).toLocaleString('en-US', { maximumFractionDigits: 2 })}`
const CRYPTO_PLATFORMS = new Set(['币安', 'OKX'])
const isCryptoPlatform = (platform?: string | null) => CRYPTO_PLATFORMS.has(String(platform || '').trim())

type QuoteMap = Record<string, { price?: number | null; symbol?: string; day_pct?: number | null; session?: string; session_label?: string }>

function compactSymbol(raw?: string | null) {
  return String(raw || '').replace(/[^A-Za-z0-9.]/g, '').toUpperCase()
}

function positionQuoteItem(row: Position) {
  const compact = compactSymbol(row.quote_symbol || row.name)
  if (!compact) return null
  if (row.quote_symbol && row.quote_source) {
    return { symbol: compactSymbol(row.quote_symbol), name: row.name, source: row.quote_source }
  }
  if (isCryptoPlatform(row.platform) || compact.endsWith('USDT') || compact.endsWith('USDC')) {
    const symbol = compact.endsWith('USDT') || compact.endsWith('USDC') ? compact : `${compact}USDT`
    return { symbol, name: row.name, source: 'binance' }
  }
  return { symbol: compact, name: row.name, source: 'tradingview' }
}

function usePositionQuotes(rows: Position[]) {
  const items = rows.filter(row => row.status === '开始').map(positionQuoteItem).filter(Boolean) as Array<{ symbol: string; name: string; source: string }>
  const key = items.map(item => `${item.source}:${item.symbol}`).sort().join('|')
  const query = useQuery({
    queryKey: ['position-quotes', key],
    queryFn: () => api.quotes(items),
    enabled: items.length > 0,
    refetchInterval: 30_000,
    staleTime: 15_000,
  })
  const quotes: QuoteMap = {}
  const remember = (map: QuoteMap, keyName?: string | null, quote?: { price?: number | null; symbol?: string }) => {
    if (!keyName || !quote) return
    map[String(keyName).toUpperCase()] = quote
  }
  ;(query.data?.quotes || []).forEach((quote, index) => {
    const item = items[index]
    remember(quotes, quote.symbol, quote)
    remember(quotes, quote.requested, quote)
    remember(quotes, quote.name, quote)
    if (item) {
      remember(quotes, item.symbol, quote)
      remember(quotes, `${item.source}:${item.symbol}`, quote)
    }
  })
  return quotes
}

function liveQuote(row: Position, quotes: QuoteMap) {
  const item = positionQuoteItem(row)
  if (!item) return null
  return quotes[`${item.source}:${item.symbol}`.toUpperCase()]
    || quotes[item.symbol.toUpperCase()]
    || quotes[compactSymbol(row.name)]
    || quotes[compactSymbol(row.quote_symbol)]
    || null
}

function livePrice(row: Position, quotes: QuoteMap) {
  const price = liveQuote(row, quotes)?.price
  return price == null ? null : Number(price)
}

function livePnl(row: Position, quotes: QuoteMap) {
  if (row.status === '已结束') {
    return {
      amount: Number(row.pnl_amount || 0),
      percent: row.pnl_pct == null ? null : Number(row.pnl_pct) * 100,
      price: null as number | null,
    }
  }
  const px = livePrice(row, quotes)
  const open = Number(row.open_price || 0)
  const shares = Number(row.shares || 0)
  const openAmount = Number(row.open_amount || 0)
  const fee = Number(row.fee || 0)
  if (px == null || !(shares > 0)) {
    return { amount: Number(row.pnl_amount || 0), percent: row.pnl_pct == null ? null : Number(row.pnl_pct) * 100, price: null as number | null }
  }
  const value = px * shares
  const short = row.strategy_side === 'short'
  const amount = short ? openAmount - value - fee : value - openAmount - fee
  const percent = open > 0 ? (short ? (open - px) / open : (px - open) / open) * 100 : null
  return { amount, percent, price: px }
}
const compact = (value?: number | null) => {
  if (value == null) return '—'; const abs = Math.abs(value)
  return abs >= 1e12 ? `$${(value / 1e12).toFixed(1)}T` : abs >= 1e9 ? `$${(value / 1e9).toFixed(1)}B` : money(value)
}
const pct = (value?: number | null) => value == null ? '—' : `${value > 0 ? '+' : ''}${Number(value).toFixed(1)}%`

function HomePage() {
  const client = useQueryClient()
  const watches = useQuery({ queryKey: ['watches'], queryFn: api.dailyWatches })
  const tape = useQuery({ queryKey: ['market-tape'], queryFn: api.marketTape, staleTime: 60_000 })
  const macro = useQuery({ queryKey: ['monthly-macro'], queryFn: loadMonthlyMacro, staleTime: 10 * 60_000 })
  const goal = useQuery({ queryKey: ['account-goal'], queryFn: api.accountGoal, staleTime: 60_000 })
  const messages = useQuery({ queryKey: ['home-messages'], queryFn: api.homeMessages, staleTime: 60_000 })
  const dismiss = useMutation({ mutationFn: (key: string) => api.dismissHomeMessage(key), onSuccess: () => client.invalidateQueries({ queryKey: ['home-messages'] }), onError: error => Toast.show({ icon: 'fail', content: error.message }) })
  const rows = watches.data || []
  const up = rows.filter(row => (row.current_price || 0) > (row.prev_close || 0)).length
  const events = rows.filter(row => row.events).slice(0, 4)
  return <><section className="hero home-hero"><p>今日概览</p><h1>关注重要变化</h1><span>{new Date().toLocaleDateString('zh-CN', { month: 'long', day: 'numeric', weekday: 'long' })}</span></section>
    <QueryState loading={goal.isLoading} error={goal.error} retry={() => goal.refetch()}/>{goal.data && <HomeGoalCard goal={goal.data}/>} 
    <section className="home-message-section"><div className="market-section-head"><h2>今日消息</h2><span>{messages.data?.items.length || 0} 条</span></div><QueryState loading={messages.isLoading} error={messages.error} retry={() => messages.refetch()}/>{messages.data && <div className="home-message-list">{messages.data.items.map(item => <HomeMessageRow key={item.key} item={item} removing={dismiss.isPending && dismiss.variables === item.key} onRemove={() => dismiss.mutate(item.key)}/>) }{messages.data.items.length === 0 && <div className="home-message-empty"><Bell size={18}/><span>今天暂无新的重要消息</span></div>}</div>}</section>
    <QueryState loading={tape.isLoading} error={tape.error} retry={() => tape.refetch()}/>
    {tape.data && <MarketOverview rows={tape.data.indices}/>} 
    <QueryState loading={macro.isLoading} error={macro.error} retry={() => macro.refetch()}/>{macro.data && <MacroReleaseList rows={macro.data}/>} 
    <QueryState loading={watches.isLoading} error={watches.error} retry={() => watches.refetch()}/>{watches.data && <>
      <div className="summary-grid"><article><span>关注股票</span><strong>{rows.length}</strong></article><article><span>今日上涨</span><strong className="positive">{up}</strong></article><article><span>潜在事件</span><strong>{events.length}</strong></article></div>
      <SectionTitle title="最近关注" action="全部股票" to="/stocks/list"/><div className="home-watch-list">{rows.slice(0, 3).map(row => <StockMiniCard key={row.id} row={row}/>)}</div>
    </>}</>
}

function HomeGoalCard({ goal }: { goal: Awaited<ReturnType<typeof api.accountGoal>> }) {
  const total = goal.total_amount ?? goal.principal
  const target = 30_000_000, equity = Number(goal.equity || 0), monthly = Number(goal.monthly_return || 0)
  const daily = monthly > 0 ? (1 + monthly) ** (12 / 365) - 1 : 0
  const etaDays = equity >= target ? 0 : equity > 0 && daily > 0 ? Math.round(Math.log(target / equity) / Math.log(1 + daily)) : null
  const etaYears = etaDays == null ? null : etaDays / 365
  const eta = etaDays == null ? '暂无法估算' : etaDays === 0 ? '已达到目标' : `${etaDays.toLocaleString()} 天`
  const remaining = Math.max(target - equity, 0), width = Math.max(.5, Math.min(100, equity / target * 100))
  return <section className="home-goal-card"><header><div><CircleDollarSign size={18}/><span>资金目标</span></div><small>目标 {compact(target)}</small></header><div className="home-goal-values"><article><span>总金额</span><b>{money(total)}</b></article><article><span>可用金额</span><b className="positive">{money(goal.available)}</b></article></div><div className="goal-eta-line"><div><span>距离 3000 万</span><b>还差 {compact(remaining)}</b></div><div><span>预计完成</span><b>{eta}</b></div></div><div className="home-goal-progress"><i style={{ width: `${width}%` }}/></div><p>按月化 {(monthly * 100).toFixed(1)}% 复利估算 · 约 {etaYears == null ? '—' : etaYears.toFixed(1)} 年，实际进度会随盈亏变化</p></section>
}

function HomeMessageRow({ item, removing, onRemove }: { item: HomeMessage; removing: boolean; onRemove: () => void }) {
  const label = item.kind === 'earnings' ? '财报' : item.kind === 'macro' ? '宏观' : '持仓'
  return <article className={`home-message-row tone-${item.tone}`}><div><span>{label}</span><b>{item.title}</b><p>{item.detail}</p></div><button onClick={onRemove} disabled={removing} aria-label={`删除${item.title}`}><X size={15}/></button></article>
}

const marketPrice = (row: MarketQuote) => row.price == null ? '—' : row.kind === 'yield' ? `${row.price.toFixed(3)}%` : row.price.toLocaleString('en-US', { minimumFractionDigits: row.price >= 100 ? 2 : 0, maximumFractionDigits: row.price < 10 ? 3 : 2 })
const marketChange = (value: number) => `${value > 0 ? '+' : ''}${value.toFixed(2)}%`

function MarketOverview({ rows }: { rows: MarketQuote[] }) {
  const core = ['SPY', 'QQQ', 'DIA'].map(symbol => rows.find(row => row.requested === symbol || row.symbol === symbol)).filter(Boolean) as MarketQuote[]
  const marketSymbols = ['^VIX', 'DX-Y.NYB', '^TNX'], commoditySymbols = ['GC=F', 'CL=F']
  const marketRows = marketSymbols.map(symbol => rows.find(row => row.requested === symbol)).filter(Boolean) as MarketQuote[]
  const commodities = commoditySymbols.map(symbol => rows.find(row => row.requested === symbol)).filter(Boolean) as MarketQuote[]
  const vix = rows.find(row => row.requested === '^VIX')
  const riskHigh = Math.abs(vix?.week_pct || 0) >= 10
  return <section className="market-overview"><article className={`market-alert ${riskHigh ? 'risk-high' : ''}`}><span>⌁</span><div><b>{riskHigh ? '市场风险偏高' : '市场波动平稳'}</b><p>VIX 日内上涨 {marketChange(vix?.day_pct || 0)}，本周累计 {marketChange(vix?.week_pct || 0)}；{riskHigh ? '主要股指周线仍偏弱，注意波动放大。' : '主要指标暂未出现明显异常。'}</p></div></article>
    <div className="market-section-head"><h2>核心指数</h2><a href="#macro-market">详细行情 →</a></div>
    <div className="core-market-grid">{core.map(row => <MarketCard key={row.requested || row.symbol} row={row}/>)}</div>
    <div className="market-section-head" id="macro-market"><h2>市场指标</h2><span>日涨幅 / 周涨幅</span></div>
    <div className="macro-market-list">{marketRows.map(row => <MacroMarketRow key={row.requested || row.symbol} row={row}/>)}</div>
    <div className="market-section-head"><h2>商品</h2><span>日涨幅 / 周涨幅</span></div>
    <div className="macro-market-list commodity-list">{commodities.map(row => <MacroMarketRow key={row.requested || row.symbol} row={row}/>)}</div>
  </section>
}

function MarketCard({ row }: { row: MarketQuote }) {
  return <article className="market-card"><span>{row.symbol} · {row.name}</span><strong>{marketPrice(row)}</strong><div className={row.day_pct >= 0 ? 'positive' : 'negative'}>{marketChange(row.day_pct)}</div></article>
}

function MacroMarketRow({ row }: { row: MarketQuote }) {
  return <article><div><b>{row.symbol}</b><span>{row.name}</span></div><strong>{marketPrice(row)}</strong><em className={row.day_pct >= 0 ? 'positive' : 'negative'}>{marketChange(row.day_pct)}</em><em className={row.week_pct >= 0 ? 'positive' : 'negative'}>{marketChange(row.week_pct)}</em></article>
}

type MacroRelease = { group: string; name: string; releaseDate: string; reference?: string; payload: any }
const macroOrder = ['cpi', 'ppi', 'pce', 'nfp', 'pmi']
async function loadMonthlyMacro(): Promise<MacroRelease[]> {
  const calendar = await api.boardCalendar()
  const events = Object.values(calendar.by_date || {}).flat().filter(event => event.kind === 'macro' && event.open && event.days <= 0 && event.days >= -31)
  const latest = new Map<string, typeof events[number]>()
  events.sort((a, b) => b.date.localeCompare(a.date)).forEach(event => { if (!latest.has(event.open!)) latest.set(event.open!, event) })
  const groups = macroOrder.filter(group => latest.has(group))
  const results = await Promise.allSettled(groups.map(group => api.macroGroup(group)))
  return groups.flatMap((group, index) => results[index].status === 'fulfilled' ? [{ group, name: latest.get(group)?.name || group.toUpperCase(), releaseDate: latest.get(group)?.date || '', reference: latest.get(group)?.ref, payload: (results[index] as PromiseFulfilledResult<any>).value }] : [])
}
function macroPrimary(payload: any) {
  const id = payload?.default_ids?.[0]
  return payload?.series?.find((row: any) => row.id === id) || payload?.series?.[0]
}
function macroPointText(group: string, point: any) {
  if (!point) return '—'
  if (group === 'pmi') return Number(point.value).toFixed(1)
  if (group === 'nfp') return point.mom == null ? '—' : `${point.mom > 0 ? '+' : ''}${(Number(point.mom) / 10).toFixed(1)}万`
  return point.yoy == null ? '—' : `${Number(point.yoy).toFixed(2)}%`
}
function MacroReleaseList({ rows }: { rows: MacroRelease[] }) {
  return <section className="macro-releases"><div className="market-section-head"><h2>最近一个月宏观数据</h2><span>公布值 / 数据期</span></div><div className="macro-release-list">{rows.map(row => { const primary = macroPrimary(row.payload), last = primary?.latest; return <NavLink key={row.group} to={`/macro/${row.group}`}><div><b>{row.name}</b><span>{row.releaseDate} 公布 · {row.reference || last?.date || '—'}</span></div><strong>{macroPointText(row.group, last)}</strong><ChevronRight size={17}/></NavLink> })}{rows.length === 0 && <div className="empty-card">最近一个月暂无宏观数据公布</div>}</div></section>
}

function MacroDetailPage() {
  const { group = 'cpi' } = useParams()
  const query = useQuery({ queryKey: ['macro-detail', group], queryFn: () => api.macroGroup(group), staleTime: 10 * 60_000 })
  const payload = query.data, primary = macroPrimary(payload), points = primary?.points || [], recent = points.slice(-24)
  const metric = group === 'pmi' ? 'value' : group === 'nfp' ? 'mom' : 'yoy'
  const chartPoints = recent.map((point: any, index: number) => [index, Number(point[metric])] as ChartPoint).filter(validPoint)
  const last = points.at(-1), previous = points.at(-2)
  return <><SecondaryHeader title={`${String(group).toUpperCase()} 分析`} subtitle="与 PC 端使用同一数据源" fallback="/"/><QueryState loading={query.isLoading} error={query.error} retry={() => query.refetch()}/>{payload && <div className="macro-detail-page"><section className="macro-detail-hero"><span>{payload.source}</span><h1>{payload.title}</h1><p>{macroAnalysis(group, last, previous)}</p></section><div className="macro-detail-cards">{(payload.series || []).filter((row: any) => row.latest).slice(0, 6).map((row: any) => <article key={row.id}><span>{row.name}</span><strong>{macroPointText(group, row.latest)}</strong><small>{row.latest.date}{group !== 'pmi' && group !== 'nfp' ? ` · 环比 ${row.latest.mom == null ? '—' : `${Number(row.latest.mom).toFixed(2)}%`}` : ''}</small></article>)}</div><OptionChartCard title={`${primary?.name || payload.title}走势`} subtitle={`最近 ${recent.length} 期 · ${group === 'pmi' ? '指数' : group === 'nfp' ? '月度变化' : '同比变化 %'}`}><MiniLineChart series={chartPoints.length ? [{ label: primary?.name || payload.title, color: '#79a0ff', points: chartPoints }] : []} axisLabels={[recent[0]?.date || '', recent.at(-1)?.date || '']}/></OptionChartCard><section className="macro-reading"><h2>数据解读</h2><p>{macroLongAnalysis(group, last, previous)}</p><small>更新时间：{payload.updated_at || '—'}。宏观数据存在修订，历史值以数据源最新记录为准。</small></section></div>}</>
}
function macroAnalysis(group: string, last: any, previous: any) {
  if (!last) return '暂无足够数据进行分析。'
  if (group === 'pmi') return `最新值 ${Number(last.value).toFixed(1)}，${last.value >= 50 ? '处于扩张区间' : '处于收缩区间'}。`
  if (group === 'nfp') return `最新就业变化 ${macroPointText(group, last)}，较上一期${Number(last.mom || 0) >= Number(previous?.mom || 0) ? '增强' : '放缓'}。`
  return `最新同比 ${macroPointText(group, last)}，环比 ${last.mom == null ? '—' : `${Number(last.mom).toFixed(2)}%`}。`
}
function macroLongAnalysis(group: string, last: any, previous: any) {
  if (!last) return '数据不足。'
  if (group === 'cpi') return `CPI 是市场交易最敏感的通胀指标。当前同比${Number(last.yoy || 0) > Number(previous?.yoy || 0) ? '较上一期升温，可能增加利率维持高位的压力' : '较上一期放缓，有助于缓解通胀压力'}；核心项目通常比能源和食品更能反映持续性。`
  if (group === 'ppi') return `PPI 位于价格传导上游。当前同比${Number(last.yoy || 0) > Number(previous?.yoy || 0) ? '回升，需关注成本向消费端传导' : '放缓，企业投入成本压力有所缓解'}，仍需结合核心 PPI 与后续 CPI 判断。`
  if (group === 'pce') return `PCE 是美联储重点参考的通胀口径。当前变化应结合核心 PCE、就业和工资数据判断政策路径，单月下降不代表趋势已经确认。`
  if (group === 'pmi') return `PMI 以 50 为荣枯线。最新读数 ${Number(last.value).toFixed(1)}，显示企业活动${last.value >= 50 ? '扩张' : '收缩'}；新订单、就业和价格分项可用于判断增长持续性与通胀压力。`
  return `就业变化反映经济需求和企业用工强度。应同时观察失业率、平均时薪以及前值修订，单一月份容易受季节因素影响。`
}

function SectionTitle({ title, action, to }: { title: string; action?: string; to?: string }) {
  return <div className="section-title"><h2>{title}</h2>{action && to && <NavLink to={to}>{action} →</NavLink>}</div>
}
function StockMiniCard({ row }: { row: DailyWatch }) {
  const change = row.current_price != null && row.prev_close ? (row.current_price / row.prev_close - 1) * 100 : null
  return <NavLink to={`/stocks/${row.id}/${row.symbol}`} className="mini-stock"><div><b>{row.symbol}</b><span>{row.sector}</span></div><strong>{money(row.current_price)}</strong><small className={(change || 0) >= 0 ? 'positive' : 'negative'}>{pct(change)}</small></NavLink>
}

function StockHubPage() {
  const fear = useQuery({ queryKey: ['stock-fear-panel'], queryFn: api.fearPanel, staleTime: 5 * 60_000 })
  const watches = useQuery({ queryKey: ['watches'], queryFn: api.dailyWatches })
  const positions = useQuery({ queryKey: ['positions'], queryFn: () => api.positions() })
  const panel = fear.data as any
  const stockPositions = (positions.data || []).filter(row => !isCryptoPlatform(row.platform) && row.status === '开始')
  const quotes = usePositionQuotes(stockPositions)
  const derivMainCards = ((panel?.derivative_cards || []) as any[]).filter(c => c.layout !== 'sector_bars')
  const derivSectorCards = ((panel?.derivative_cards || []) as any[]).filter(c => c.layout === 'sector_bars')
  return <><PageHead title="股票" subtitle="市场情绪、筛选与实时研究"/>
    <section className="fear-summary"><div><span className="fear-icon"><Activity size={20}/></span><div><p>市场情绪</p><h2>{panel?.composite_label || '读取中'}</h2><small>六项指标等权综合</small></div></div><strong>{panel?.composite ?? '—'}</strong></section>
    <QueryState loading={fear.isLoading} error={fear.error} retry={() => fear.refetch()}/>
    {panel?.cards?.length > 0 && <><div className="stock-section-head"><h2>恐慌指标</h2><span>更新 {panel.updated_at || '—'}</span></div><div className="fear-grid">{panel.cards.map((card: any) => <article key={card.key} className={`fear-metric tone-${card.tone || 'neutral'}`}><div><span>{card.title}</span><small>{card.label}</small></div><strong>{card.display}</strong><em className={(card.delta || 0) > 0 ? 'negative' : (card.delta || 0) < 0 ? 'positive' : ''}>{card.delta_display || '—'}</em><i style={{ width: `${Math.max(0, Math.min(100, Number(card.bar) || 0))}%` }}/></article>)}</div></>}
    {(derivMainCards.length > 0 || derivSectorCards.length > 0) && <>
      <div className="stock-section-head"><h2>衍生品三层</h2><span>不计入上方等权</span></div>
      {derivMainCards.length > 0 && <div className="fear-grid fear-deriv-grid">{derivMainCards.map((card: any) => <article key={card.key} className={`fear-metric tone-${card.tone || 'neutral'}`}><div><span>{card.title}</span><small>{card.label}</small></div><strong>{card.display}</strong><em className={(card.delta || 0) > 0 ? 'negative' : (card.delta || 0) < 0 ? 'positive' : ''}>{card.delta_display || '—'}</em><i style={{ width: `${Math.max(0, Math.min(100, Number(card.bar) || 0))}%` }}/></article>)}</div>}
      {derivSectorCards.map((card: any) => <SectorOptionsCard key={card.key} card={card}/>)}
    </>}
    <SectionTitle title="股票列表" action="查看全部" to="/stocks/list"/>
    <QueryState loading={watches.isLoading} error={watches.error} retry={() => watches.refetch()}/>
    <div className="stock-hub-preview">{(watches.data || []).slice(0, 3).map(row => <StockRow key={row.id} row={row}/>)}</div>
    <SectionTitle title="股票持仓" action="查看全部" to="/stocks/positions"/>
    <QueryState loading={positions.isLoading} error={positions.error} retry={() => positions.refetch()}/>
    <div className="asset-position-preview">{stockPositions.slice(0, 2).map(row => <PositionRow key={row.id} row={row} quotes={quotes}/>)}{positions.data && stockPositions.length === 0 && <div className="empty-card">暂无股票持仓</div>}</div>
    <div className="stock-section-head submenu-title"><h2>股票工具</h2></div>
    <nav className="stock-tool-menu"><StockToolLink to="/stocks/list" icon={<ListTree/>} title="股票列表" detail="关注列表 + 开盘日实时行情与盘前盘中盘后时段"/><StockToolLink to="/stocks/screen" icon={<ListFilter/>} title="股票筛选" detail="按财务、估值和价格趋势寻找股票"/><StockToolLink to="/stocks/journal" icon={<BookOpenText/>} title="股票日志" detail="查看每日市场判断与分析记录"/><StockToolLink to="/stocks/news" icon={<Newspaper/>} title="新闻" detail="浏览最新市场与个股相关新闻"/></nav>
  </>
}

function SectorOptionsCard({ card }: { card: any }) {
  const rows = card.rows || []
  return <article className={`sector-opt-card tone-${card.tone || 'neutral'}`}>
    <header>
      <div><b>{card.title}</b><small>{card.label} · {card.display}</small></div>
    </header>
    <div className="sector-legend"><span className="leg call">Call 看涨</span><span className="leg put">Put 看跌</span><span className="sub">条长=成交量占比 · 右侧 Put/Call</span></div>
    <div className="sector-rows">{rows.map((row: any) => {
      const callShare = Number(row.call_share) || 0
      const putShare = Number(row.put_share) || 0
      const pcr = row.pcr == null ? '—' : Number(row.pcr).toFixed(2)
      return <div key={row.symbol} className="sector-row">
        <div className="sector-meta"><span className="sym">{row.symbol}</span><span className="name">{row.name}</span></div>
        <div className="sector-bar-track" title={`Call ${row.call_share ?? '—'}% · Put ${row.put_share ?? '—'}%`}>
          <div className="sector-bar call" style={{ width: `${callShare}%` }}/>
          <div className="sector-bar put" style={{ width: `${putShare}%` }}/>
        </div>
        <div className="sector-stats">
          <span className="share call">C {row.call_share != null ? `${row.call_share}%` : '—'}</span>
          <span className="share put">P {row.put_share != null ? `${row.put_share}%` : '—'}</span>
          <span className={`pcr tone-${row.tone || 'neutral'}`}>{pcr}</span>
        </div>
      </div>
    })}</div>
    {card.hint && <p className="sector-hint">{card.hint}</p>}
  </article>
}

function StockToolLink({ to, icon, title, detail }: { to: string; icon: React.ReactNode; title: string; detail: string }) {
  return <NavLink to={to}><span>{icon}</span><div><b>{title}</b><small>{detail}</small></div><ChevronRight size={18}/></NavLink>
}

function StockLivePage() {
  return <Navigate to="/stocks/list" replace />
}

function StockJournalPage() {
  const query = useQuery({ queryKey: ['stock-journals'], queryFn: api.stockJournals })
  const rows = query.data?.items || []
  return <><SecondaryHeader title="股票日志" subtitle="每日市场判断与分析记录" fallback="/stocks"/>
    <QueryState loading={query.isLoading} error={query.error} retry={() => query.refetch()}/>
    <div className="journal-list">{rows.map(row => <JournalRow key={row.id} row={row}/>)}{query.data && rows.length === 0 && <div className="empty-card">暂无股票日志</div>}</div>
  </>
}

function JournalRow({ row }: { row: StockJournal }) {
  const tone = row.stance === '多' ? 'positive' : row.stance === '空' ? 'negative' : 'neutral'
  return <article className="journal-row"><header><time>{row.log_date}</time><span className={tone}>{row.stance}</span></header><p>{row.content}</p></article>
}

function StockNewsPage() {
  const query = useQuery({ queryKey: ['stock-market-news'], queryFn: api.marketNews, staleTime: 5 * 60_000 })
  const rows = query.data?.news || []
  return <><SecondaryHeader title="股票新闻" subtitle="市场与个股最新动态" fallback="/stocks"/><QueryState loading={query.isLoading} error={query.error} retry={() => query.refetch()}/><div className="stock-news-list">{rows.map((item, index) => <a key={`${item.url}-${index}`} href={item.url} target="_blank" rel="noreferrer"><b>{item.title}</b><span>{[item.source, item.ago].filter(Boolean).join(' · ')}</span><ChevronRight size={17}/></a>)}{query.data && rows.length === 0 && <div className="empty-card">暂无新闻</div>}</div></>
}

function useWatchQuotes(rows: DailyWatch[]) {
  const items = rows.map(row => {
    const symbol = String(row.symbol || '').trim().toUpperCase()
    if (!symbol) return null
    return { symbol, name: symbol, source: 'tradingview' as const }
  }).filter(Boolean) as Array<{ symbol: string; name: string; source: string }>
  const key = items.map(item => item.symbol).sort().join('|')
  const query = useQuery({
    queryKey: ['watch-quotes', key],
    queryFn: () => api.quotes(items),
    enabled: items.length > 0,
    refetchInterval: (current) => current.state.data?.market?.live ? 30_000 : false,
    staleTime: 15_000,
  })
  const map: QuoteMap = {}
  const remember = (target: QuoteMap, name?: string | null, quote?: { price?: number | null; symbol?: string; day_pct?: number | null; session?: string; session_label?: string }) => {
    if (!name || !quote) return
    target[String(name).toUpperCase()] = quote
  }
  ;(query.data?.quotes || []).forEach((quote, index) => {
    const item = items[index]
    remember(map, quote.symbol, quote)
    remember(map, quote.requested, quote)
    remember(map, quote.name, quote)
    if (item) remember(map, item.symbol, quote)
  })
  return { quotes: map, market: query.data?.market || null, refetch: query.refetch, isFetching: query.isFetching }
}

function StocksPage() {
  const [search, setSearch] = useState('')
  const query = useQuery({ queryKey: ['watches'], queryFn: api.dailyWatches })
  const rows = useMemo(() => (query.data || []).filter(row => `${row.symbol} ${row.sector}`.toLowerCase().includes(search.toLowerCase())), [query.data, search])
  const { quotes, market, refetch, isFetching } = useWatchQuotes(rows)
  const sessionText = market?.label ? `美股${market.label}` : '行情'
  const subtitle = market?.live ? `${rows.length} 只 · ${sessionText} · 约 30 秒刷新` : `${rows.length} 只 · ${sessionText}`
  return <><SecondaryHeader title="股票列表" subtitle={subtitle} fallback="/stocks"/><div className="search-box"><SearchBar value={search} onChange={setSearch} placeholder="搜索代码或行业" clearable/><button onClick={() => { query.refetch(); refetch() }} aria-label="刷新"><RefreshCw size={17} className={isFetching ? 'spin' : undefined}/></button></div>
    <QueryState loading={query.isLoading} error={query.error} retry={() => query.refetch()}/><div className="stock-list">{rows.map(row => <StockRow key={row.id} row={row} quote={quotes[String(row.symbol || '').toUpperCase()]}/>)}</div></>
}

function StockRow({ row, quote }: { row: DailyWatch; quote?: { price?: number | null; day_pct?: number | null; session_label?: string } }) {
  const price = quote?.price ?? row.current_price
  const change = quote?.day_pct != null && Number.isFinite(Number(quote.day_pct))
    ? Number(quote.day_pct)
    : (price != null && row.prev_close ? (Number(price) / Number(row.prev_close) - 1) * 100 : null)
  return <NavLink className="stock-row" to={`/stocks/${row.id}/${row.symbol}`}><div className="ticker-logo">{row.symbol.slice(0, 2)}</div><div className="stock-name"><b>{row.symbol}</b><span>{row.sector} · {row.market_direction}{quote?.session_label ? ` · ${quote.session_label}` : ''}</span></div><div className="stock-price"><b>{money(price)}</b><span className={(change || 0) >= 0 ? 'positive' : 'negative'}>{pct(change)}</span></div></NavLink>
}

function CryptoPage() {
  const query = useQuery({ queryKey: ['crypto-coins'], queryFn: api.cryptoCoins, staleTime: 60_000 })
  const positions = useQuery({ queryKey: ['positions'], queryFn: () => api.positions() })
  const rows = query.data?.items || []
  const cryptoPositions = (positions.data || []).filter(row => isCryptoPlatform(row.platform) && row.status === '开始')
  const quotes = usePositionQuotes(cryptoPositions)
  return <><PageHead title="虚拟币" subtitle="关注币种与 24 小时行情"/>
    <QueryState loading={query.isLoading} error={query.error} retry={() => query.refetch()}/>
    <div className="crypto-list">{rows.map(row => <CryptoRow key={row.id} row={row}/>)}</div>
    <SectionTitle title="虚拟币持仓" action="查看全部" to="/crypto/positions"/>
    <QueryState loading={positions.isLoading} error={positions.error} retry={() => positions.refetch()}/>
    <div className="asset-position-preview">{cryptoPositions.slice(0, 2).map(row => <PositionRow key={row.id} row={row} quotes={quotes}/>)}{positions.data && cryptoPositions.length === 0 && <div className="empty-card">暂无虚拟币持仓</div>}</div>
    <div className="stock-section-head submenu-title"><h2>虚拟币工具</h2></div>
    <nav className="stock-tool-menu"><StockToolLink to="/crypto/news" icon={<Newspaper/>} title="新闻" detail="查看虚拟币市场与行业最新消息"/><StockToolLink to="/crypto/strategies" icon={<Gauge/>} title="指标策略" detail="组合技术与链上指标，查看监听状态"/><StockToolLink to="/crypto/custom-strategies" icon={<ScrollText/>} title="自定义策略" detail="导数策略、高级策略与三态趋势策略"/><StockToolLink to="/crypto/positions" icon={<WalletCards/>} title="虚拟币持仓" detail="查看币安 / OKX 当前与历史持仓"/></nav>
  </>
}

function CryptoRow({ row }: { row: CryptoCoin }) {
  const change = row.change_24h
  return <NavLink className="crypto-row" to={`/crypto/${row.id}/${row.symbol}`}><div className="crypto-logo">{row.symbol.slice(0, 1)}</div><div className="stock-name"><b>{row.symbol}</b><span>{row.name} · {row.binance_symbol || `${row.symbol}USDT`}</span></div><div className="stock-price"><b>{money(row.price)}</b><span className={(change || 0) >= 0 ? 'positive' : 'negative'}>{pct(change)}</span></div><ChevronRight size={17}/></NavLink>
}

function CryptoDetailPage() {
  const { id = '0', symbol = '' } = useParams(), coinId = Number(id)
  const [interval, setInterval] = useState('1d')
  const klines = useQuery({ queryKey: ['crypto-klines', coinId, interval], queryFn: () => api.cryptoKlines(coinId, interval, 120), enabled: coinId > 0 })
  const tech = useQuery({ queryKey: ['crypto-tech', coinId, interval], queryFn: () => api.cryptoTech(coinId, interval), enabled: coinId > 0 })
  const chain = useQuery({ queryKey: ['crypto-onchain', symbol], queryFn: () => api.cryptoOnchain(`${symbol}USDT`), enabled: !!symbol })
  const onchain = chain.data as any, technical = tech.data as any
  return <><SecondaryHeader title={`${symbol} 分析`} subtitle="K 线、技术指标与链上数据" fallback="/crypto"/>
    <div className="crypto-timeframes">{[['5m','5分'],['30m','30分'],['1h','1时'],['1d','日线']].map(([value,label]) => <button className={interval === value ? 'active' : ''} onClick={() => setInterval(value)} key={value}>{label}</button>)}</div>
    <QueryState loading={klines.isLoading} error={klines.error} retry={() => klines.refetch()}/>
    {klines.data && <section className="crypto-analysis-card"><header><div><CandlestickChart size={18}/><b>K 线走势</b></div><span>{klines.data.bars.length} 根</span></header><Candles bars={klines.data.bars}/></section>}
    <QueryState loading={tech.isLoading} error={tech.error} retry={() => tech.refetch()}/>
    {technical && <section className="crypto-analysis-card"><header><div><Gauge size={18}/><b>指标分析</b></div><span>{technical.hit_count}/{technical.total} 命中</span></header><div className="crypto-price-grid">{Object.entries(technical.prices || {}).slice(0, 8).map(([key,value]) => <article key={key}><span>{cryptoMetricLabel(key)}</span><b>{formatMetric(value)}</b></article>)}</div><div className="indicator-groups">{(technical.groups || []).map((group: any) => <section key={group.name}><h3>{group.name}</h3><div>{group.items.map((item: any) => <span className={item.hit ? 'hit' : ''} key={item.id}>{item.name}</span>)}</div></section>)}</div></section>}
    <QueryState loading={chain.isLoading} error={chain.error} retry={() => chain.refetch()}/>
    {onchain && <OnchainPanel data={onchain}/>} 
  </>
}

function Candles({ bars }: { bars: CryptoKline[] }) {
  const shown = bars.slice(-48)
  if (!shown.length) return <div className="empty-card">暂无 K 线</div>
  const high = Math.max(...shown.map(row => row.high)), low = Math.min(...shown.map(row => row.low)), range = high - low || 1
  const y = (value: number) => 12 + (high - value) / range * 156, width = 620, step = width / shown.length
  return <><svg className="candle-chart" viewBox={`0 0 ${width} 185`} preserveAspectRatio="none">{[0,1,2,3].map(i => <line key={i} x1="0" x2={width} y1={12+i*52} y2={12+i*52}/>)}{shown.map((row,index) => { const x = index * step + step / 2, up = row.close >= row.open, bodyY = Math.min(y(row.open), y(row.close)), bodyH = Math.max(2, Math.abs(y(row.open)-y(row.close))); return <g key={row.ts} className={up ? 'up' : 'down'}><line x1={x} x2={x} y1={y(row.high)} y2={y(row.low)}/><rect x={x-step*.28} y={bodyY} width={step*.56} height={bodyH}/></g> })}</svg><div className="candle-axis"><span>{new Date(shown[0].ts*1000).toLocaleDateString()}</span><span>{low.toLocaleString()} — {high.toLocaleString()}</span><span>{new Date(shown.at(-1)!.ts*1000).toLocaleDateString()}</span></div></>
}

function OnchainPanel({ data }: { data: any }) {
  const oi = data.open_interest || {}, funding = data.funding || {}, whales = data.whales || {}, sentiment = data.sentiment || {}
  const metrics = [
    ['未平仓量', oi.open_interest], ['持仓 24h', oi.change_24h_pct, '%'], ['资金费率', funding.latest_pct, '%'],
    ['大额成交', whales.items?.length], ['大额净流入', whales.net_quote], ['恐惧贪婪', sentiment.value ?? sentiment.market?.value ?? sentiment.market?.current?.value],
  ]
  return <section className="crypto-analysis-card onchain-card"><header><div><Activity size={18}/><b>链上与衍生品</b></div><span>与 PC 端同源</span></header><div className="crypto-price-grid">{metrics.map(([label,value,suffix]) => <article key={String(label)}><span>{label}</span><b>{formatMetric(value)}{value != null ? suffix : ''}</b></article>)}</div>{data.flows?.items?.length > 0 && <div className="onchain-flow-list">{data.flows.items.slice(0,5).map((row:any,index:number) => <div key={row.symbol || index}><span>{row.symbol || '交易所'} · {row.bias || '中性'}</span><b className={(row.change_24h || 0) >= 0 ? 'positive':'negative'}>{formatMetric(row.change_24h)}%</b></div>)}</div>}<p className="data-note">链上板块包含交易所持仓、资金费率及大额成交代理数据；它们反映市场活动，不等同于钱包地址的完整资金流。</p></section>
}

const cryptoMetricLabel = (key: string) => ({ last:'现价', rsi14:'RSI 14', atr14:'ATR 14', ma20:'MA 20', ma50:'MA 50', ma200:'MA 200', macd:'MACD', volume_ratio:'量比' } as Record<string,string>)[key] || key.toUpperCase()
function formatMetric(value: unknown) { const n = Number(value); return value == null || value === '' || !Number.isFinite(n) ? '—' : Math.abs(n) >= 1e9 ? `${(n/1e9).toFixed(2)}B` : Math.abs(n) >= 1e6 ? `${(n/1e6).toFixed(2)}M` : n.toLocaleString(undefined,{maximumFractionDigits:4}) }

function CryptoStrategiesPage() {
  const query = useQuery({ queryKey: ['crypto-strategies'], queryFn: api.cryptoStrategies })
  return <><SecondaryHeader title="指标策略" subtitle="与 PC 端策略和监听状态同步" fallback="/crypto"/><QueryState loading={query.isLoading} error={query.error} retry={() => query.refetch()}/><div className="strategy-list">{(query.data?.items || []).map(row => <StrategyRow key={row.id} row={row}/>)}{query.data && !query.data.items.length && <div className="empty-card">暂无指标策略，请先在 PC 端创建</div>}</div></>
}

function CryptoNewsPage() {
  const query = useQuery({ queryKey: ['crypto-news'], queryFn: () => api.cryptoNews(50), staleTime: 5 * 60_000 })
  const payload = query.data as any
  const rows = Array.isArray(payload) ? payload : payload?.items || payload?.news || []
  return <><SecondaryHeader title="虚拟币新闻" subtitle="与 PC 端使用相同新闻源" fallback="/crypto"/><QueryState loading={query.isLoading} error={query.error} retry={() => query.refetch()}/><div className="stock-news-list">{rows.map((item:any,index:number) => <a key={`${item.url || item.link || item.title}-${index}`} href={item.url || item.link || '#'} target="_blank" rel="noreferrer"><b>{item.title || item.name}</b><span>{[item.source, item.published_at || item.time || item.ago].filter(Boolean).join(' · ')}</span><ChevronRight size={17}/></a>)}{query.data && rows.length === 0 && <div className="empty-card">暂无虚拟币新闻</div>}</div></>
}
function StrategyRow({ row }: { row: CryptoStrategy }) {
  return <article className="strategy-row"><header><div><b>{row.name}</b><span>{row.symbol} · {row.timeframe}</span></div><em className={row.enabled ? 'running' : ''}>{row.enabled ? '监听中' : '未监听'}</em></header><p>{row.indicators?.join(row.join === 'or' ? ' / ' : ' + ') || '暂无指标'}</p><footer><span>最近价格 {money(row.last_price)}</span><b className={row.last_hit ? 'positive' : ''}>{row.last_hit == null ? '待检查' : row.last_hit ? '已命中' : '未命中'}</b></footer>{row.last_error && <small>{row.last_error}</small>}</article>
}

function CryptoCustomStrategiesPage() {
  const query = useQuery({ queryKey: ['crypto-custom-strategies'], queryFn: api.cryptoCustomStrategies })
  return <><SecondaryHeader title="自定义策略" subtitle="导数与趋势策略，数据同 PC 端" fallback="/crypto"/><QueryState loading={query.isLoading} error={query.error} retry={() => query.refetch()}/><div className="strategy-list">{(query.data?.items || []).map(row => <CustomStrategyRow key={row.id} row={row}/>)}{query.data && !query.data.items.length && <div className="empty-card">暂无自定义策略</div>}</div></>
}
function CustomStrategyRow({ row }: { row: CryptoCustomStrategy }) {
  return <article className="strategy-row"><header><div><b>{row.name}</b><span>{row.strategy_name || '自定义'} · {row.timeframe}</span></div><em className={row.enabled ? 'running' : ''}>{row.enabled ? '监听中' : '未监听'}</em></header><p>{row.watch_symbols?.join('、') || '暂无监听币种'}</p><footer><span>{row.last_direction || '观望'}</span><b>{row.last_action || '尚未产生动作'}</b></footer>{row.last_error && <small>{row.last_error}</small>}</article>
}

function AssetPositionsPage({ asset }: { asset: 'stock' | 'crypto' }) {
  const client = useQueryClient()
  const [editing, setEditing] = useState<Position | null>(null)
  const [showForm, setShowForm] = useState(false)
  const [platform, setPlatform] = useState(asset === 'crypto' ? '币安' : '众安')
  const [status, setStatus] = useState('开始')
  const [side, setSide] = useState<'long' | 'short'>('long')
  const [name, setName] = useState('')
  const [shares, setShares] = useState('')
  const [price, setPrice] = useState('')
  const [fee, setFee] = useState('0')
  const [openedOn, setOpenedOn] = useState(today())
  const [expectedDays, setExpectedDays] = useState('21')
  const [reason, setReason] = useState('')
  const query = useQuery({ queryKey: ['positions'], queryFn: () => api.positions() })
  const strategies = useQuery({ queryKey: ['strategy-plans'], queryFn: api.strategyPlans, staleTime: 5 * 60_000 })
  const rows = (query.data || []).filter(row => asset === 'crypto' ? isCryptoPlatform(row.platform) : !isCryptoPlatform(row.platform))
  const quotes = usePositionQuotes(rows)
  const resetForm = () => {
    setEditing(null); setPlatform(asset === 'crypto' ? '币安' : '众安'); setStatus('开始'); setSide('long'); setName(''); setShares(''); setPrice(''); setFee('0'); setOpenedOn(today()); setExpectedDays('21'); setReason('')
  }
  const openCreate = () => { resetForm(); setShowForm(true) }
  const openEdit = (row: Position) => {
    setEditing(row)
    setPlatform(row.platform)
    setStatus(row.status === '已结束' ? '开始' : row.status)
    setSide(row.strategy_side === 'short' ? 'short' : 'long')
    setName(row.name)
    setShares(String(row.shares ?? ''))
    setPrice(String(row.open_price ?? ''))
    setFee(String(row.fee ?? 0))
    setOpenedOn(row.opened_on || today())
    setExpectedDays(String(row.expected_days || 21))
    setReason(row.open_reason || '')
    setShowForm(true)
  }
  const closeForm = () => { setShowForm(false); resetForm() }
  const save = useMutation({
    mutationFn: async () => {
      const strategyId = await ensureSideStrategy(side, strategies.data || [])
      const payload = {
        platform,
        status,
        name: name.trim(),
        shares: Number(shares),
        open_price: Number(price),
        fee: Number(fee || 0),
        expected_days: Number(expectedDays || 21),
        open_reason: reason.trim() || null,
        strategy_id: strategyId,
      }
      if (editing) return api.updatePosition(editing.id, payload)
      return api.createPosition({ ...payload, opened_on: openedOn || null })
    },
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ['positions'] })
      client.invalidateQueries({ queryKey: ['strategy-plans'] })
      client.invalidateQueries({ queryKey: ['position-quotes'] })
      closeForm()
      Toast.show({ icon: 'success', content: editing ? '持仓已更新' : '持仓已添加' })
    },
    onError: (error: Error) => Toast.show({ icon: 'fail', content: error.message }),
  })
  const remove = useMutation({ mutationFn: (id: number) => api.deletePosition(id), onSuccess: () => { client.invalidateQueries({ queryKey: ['positions'] }); Toast.show({ icon: 'success', content: '持仓已删除' }) }, onError: error => Toast.show({ icon: 'fail', content: error.message }) })
  const deleteRow = (row: Position) => { if (window.confirm(`删除 ${row.name} 这笔持仓？`)) remove.mutate(row.id) }
  return <><SecondaryHeader title={asset === 'crypto' ? '虚拟币持仓' : '股票持仓'} subtitle="当前、计划与历史持仓" fallback={asset === 'crypto' ? '/crypto' : '/stocks'}/><div className="page-action"><button onClick={() => showForm ? closeForm() : openCreate()}>{showForm ? <X size={17}/> : <Plus size={17}/>} {showForm ? '取消' : '新增持仓'}</button></div>
    {showForm && <PositionEditor status={status} setStatus={setStatus} side={side} setSide={setSide} name={name} setName={setName} shares={shares} setShares={setShares} price={price} setPrice={setPrice} fee={fee} setFee={setFee} openedOn={openedOn} setOpenedOn={setOpenedOn} expectedDays={expectedDays} setExpectedDays={setExpectedDays} reason={reason} setReason={setReason} platform={platform} setPlatform={setPlatform} editing={Boolean(editing)} loading={save.isPending} onSave={() => save.mutate()} asset={asset}/>}
    <QueryState loading={query.isLoading} error={query.error} retry={() => query.refetch()}/>{query.data && <><PositionGroup title="当前持仓" rows={rows.filter(row => row.status === '开始')} empty="暂无当前持仓" quotes={quotes} onDelete={deleteRow} onEdit={openEdit}/><PositionGroup title="持仓计划" rows={rows.filter(row => row.status === '未开始')} empty="暂无持仓计划" quotes={quotes} onDelete={deleteRow} onEdit={openEdit}/><PositionGroup title="已结束" rows={rows.filter(row => row.status === '已结束')} empty="暂无历史持仓" quotes={quotes}/></>}</>
}

async function ensureSideStrategy(side: 'long' | 'short', plans: StrategyPlan[]) {
  const label = side === 'short' ? '做空' : '做多'
  const usable = plans.filter(plan => plan.source !== 'file')
  const preferred = usable.find(plan => plan.name === label && normalizeSide(plan.side) === side)
  const any = preferred || usable.find(plan => normalizeSide(plan.side) === side)
  if (any) return any.id
  const created = await api.createStrategyPlan({ name: label, side, formula: {}, timeframe: '1d' })
  return created.id
}

function normalizeSide(side?: string | null): 'long' | 'short' {
  const text = String(side || 'long').trim().toLowerCase()
  return text === 'short' || text === '做空' || text === '空' ? 'short' : 'long'
}

function PositionEditor({
  status, setStatus, side, setSide, name, setName, shares, setShares, price, setPrice, fee, setFee,
  openedOn, setOpenedOn, expectedDays, setExpectedDays, reason, setReason, platform, setPlatform,
  editing, loading, onSave, asset,
}: {
  status: string; setStatus: (v: string) => void
  side: 'long' | 'short'; setSide: (v: 'long' | 'short') => void
  name: string; setName: (v: string) => void
  shares: string; setShares: (v: string) => void
  price: string; setPrice: (v: string) => void
  fee: string; setFee: (v: string) => void
  openedOn: string; setOpenedOn: (v: string) => void
  expectedDays: string; setExpectedDays: (v: string) => void
  reason: string; setReason: (v: string) => void
  platform: string
  setPlatform: (v: string) => void
  editing: boolean
  loading: boolean
  onSave: () => void
  asset?: 'stock' | 'crypto'
}) {
  const cryptoMode = asset === 'crypto' || isCryptoPlatform(platform)
  return <section className="mobile-editor position-editor">
    <label>平台<select value={platform} onChange={event => setPlatform(event.target.value)} disabled={asset === 'stock'}>
      {asset === 'crypto' ? <><option>币安</option><option>OKX</option></> : asset === 'stock' ? <option>众安</option> : <><option>众安</option><option>币安</option><option>OKX</option></>}
    </select></label>
    <label>状态<select value={status} onChange={event => setStatus(event.target.value)}><option>开始</option><option>未开始</option></select></label>
    <label className="full-field">方向
      <div className="side-toggle">
        <button type="button" className={side === 'long' ? 'active long' : ''} onClick={() => setSide('long')}>做多</button>
        <button type="button" className={side === 'short' ? 'active short' : ''} onClick={() => setSide('short')}>做空</button>
      </div>
    </label>
    <label>代码<input value={name} onChange={event => setName(event.target.value.toUpperCase())} placeholder={cryptoMode ? 'BTCUSDT' : 'AAPL'}/></label>
    <label>数量<input type="number" min="0" step="any" value={shares} onChange={event => setShares(event.target.value)} placeholder="0"/></label>
    <label>开仓价<input type="number" min="0" step="any" value={price} onChange={event => setPrice(event.target.value)} placeholder="0.00"/></label>
    <label>手续费<input type="number" min="0" step="any" value={fee} onChange={event => setFee(event.target.value)}/></label>
    <label>开仓日期<input type="date" value={openedOn} onChange={event => setOpenedOn(event.target.value)} disabled={editing}/></label>
    <label>预计持仓天数<input type="number" min="1" max="252" value={expectedDays} onChange={event => setExpectedDays(event.target.value)}/></label>
    <label className="full-field">开仓原因<input value={reason} onChange={event => setReason(event.target.value)} placeholder="可选"/></label>
    <Button className="full-field" block color="primary" loading={loading} disabled={!name.trim() || !(Number(shares) > 0) || !(Number(price) > 0)} onClick={onSave}>{editing ? '保存修改' : '保存持仓'}</Button>
  </section>
}

function MePage() {
  const { user, logout } = useAuth()
  const me = useQuery({ queryKey: ['me'], queryFn: api.me })
  const goal = useQuery({ queryKey: ['account-goal'], queryFn: api.accountGoal, staleTime: 60_000 })
  const positions = useQuery({ queryKey: ['positions'], queryFn: () => api.positions() })
  const plans = useQuery({ queryKey: ['plans'], queryFn: () => api.plans() })
  const profile = me.data || user
  const rows = positions.data || [], active = rows.filter(row => row.status === '开始'), closed = rows.filter(row => row.status === '已结束')
  const invested = active.reduce((sum, row) => sum + Number(row.open_amount || 0), 0)
  const closedPnl = closed.reduce((sum, row) => sum + Number(row.pnl_amount || 0), 0)
  const grossProfit = closed.filter(row => row.pnl_amount > 0).reduce((sum, row) => sum + row.pnl_amount, 0)
  const grossLoss = Math.abs(closed.filter(row => row.pnl_amount < 0).reduce((sum, row) => sum + row.pnl_amount, 0))
  const profitFactor = grossLoss > 0 ? (grossProfit / grossLoss).toFixed(2) : grossProfit > 0 ? '∞' : '—'
  const todayPlans = (plans.data?.items || []).filter(row => row.kind === 'day' && row.plan_date === today())
  const principal = Number(goal.data?.total_amount ?? goal.data?.principal ?? profile?.total_amount ?? 0)
  const equity = Number(goal.data?.equity ?? principal), cumulativePnl = equity - principal
  const cumulativeReturn = principal > 0 ? cumulativePnl / principal * 100 : 0
  return <><section className="profile-card"><div className="profile-avatar">{profile?.username?.slice(0, 1).toUpperCase()}</div><div><h1>{profile?.username}</h1><p>{profile?.role === 'admin' ? '管理员' : '用户'}</p></div></section>
    <QueryState loading={goal.isLoading} error={goal.error} retry={() => goal.refetch()}/>
    {!goal.isLoading && <section className="balance-card account-balance-card"><span>账户总资产</span><strong>{money(equity)}</strong><div className="account-balance-stats"><article><span>可用金额</span><b>{money(goal.data?.available ?? principal)}</b></article><article><span>累计盈亏</span><b className={cumulativePnl >= 0 ? 'positive' : 'negative'}>{cumulativePnl >= 0 ? '+' : ''}{money(cumulativePnl)}</b></article><article><span>累计收益率</span><b className={cumulativeReturn >= 0 ? 'positive' : 'negative'}>{cumulativeReturn >= 0 ? '+' : ''}{cumulativeReturn.toFixed(2)}%</b></article></div><small>起始资金 {money(principal)} · 数据来自交易记录与当前持仓</small></section>}
    <section className="today-plan"><header><div><span>今日计划</span><small>{today()}</small></div><NavLink to="/me/plans?new=1"><Plus size={15}/>添加计划</NavLink></header><QueryState loading={plans.isLoading} error={plans.error} retry={() => plans.refetch()}/>{plans.data && <div className="today-plan-content">{todayPlans.map(row => <article key={row.id}><b>{row.title}</b>{row.content && <p>{row.content}</p>}</article>)}{todayPlans.length === 0 && <div className="today-plan-empty"><CalendarDays size={20}/><span>今天还没有计划</span></div>}</div>}</section>
    <SectionTitle title="持仓概览" action="更多详情" to="/me/positions"/>
    <QueryState loading={positions.isLoading} error={positions.error} retry={() => positions.refetch()}/>
    {positions.data && <section className="me-position-overview"><article><span>进行中</span><strong>{active.length}</strong></article><article><span>开仓金额</span><strong>{compact(invested)}</strong></article><article><span>已结盈亏</span><strong className={closedPnl >= 0 ? 'positive' : 'negative'}>{money(closedPnl)}</strong></article><article><span>盈亏比</span><strong className={profitFactor !== '—' && profitFactor !== '∞' && Number(profitFactor) < 1 ? 'negative' : 'positive'}>{profitFactor}</strong><small>总盈利 / 总亏损</small></article></section>}
    <nav className="profile-menu"><NavLink to="/me/plans"><span className="profile-menu-icon"><CalendarDays size={19}/></span><span><b>计划</b><small>管理日、周和月度交易计划</small></span><ChevronRight size={19}/></NavLink><NavLink to="/me/trades"><span className="profile-menu-icon"><ReceiptText size={19}/></span><span><b>交易记录</b><small>查看个人历史交易与盈亏</small></span><ChevronRight size={19}/></NavLink><NavLink to="/me/recipients"><span className="profile-menu-icon"><Bell size={19}/></span><span><b>系统推送人</b><small>查看系统消息接收人与推送渠道</small></span><ChevronRight size={19}/></NavLink></nav>
    <Button block className="logout-button" onClick={logout}><LogOut size={17}/>退出登录</Button>
  </>
}

const today = () => {
  const date = new Date()
  const offset = date.getTimezoneOffset() * 60_000
  return new Date(date.getTime() - offset).toISOString().slice(0, 10)
}
function PlansPage() {
  const client = useQueryClient(), [searchParams] = useSearchParams(), [showForm, setShowForm] = useState(() => searchParams.get('new') === '1')
  const [kind, setKind] = useState<'day' | 'week' | 'month'>('day'), [title, setTitle] = useState(''), [content, setContent] = useState(''), [date, setDate] = useState(today())
  const query = useQuery({ queryKey: ['plans'], queryFn: () => api.plans() })
  const create = useMutation({ mutationFn: () => api.createPlan({ kind, title: title.trim(), content: content.trim() || null, plan_date: date || null }), onSuccess: () => { client.invalidateQueries({ queryKey: ['plans'] }); setTitle(''); setContent(''); setShowForm(false); Toast.show({ icon: 'success', content: '计划已添加' }) }, onError: error => Toast.show({ icon: 'fail', content: error.message }) })
  const remove = useMutation({ mutationFn: (id: number) => api.deletePlan(id), onSuccess: () => { client.invalidateQueries({ queryKey: ['plans'] }); Toast.show({ icon: 'success', content: '计划已删除' }) }, onError: error => Toast.show({ icon: 'fail', content: error.message }) })
  const rows = query.data?.items || []
  const deleteRow = (row: TradePlan) => { if (window.confirm(`删除“${row.title}”？`)) remove.mutate(row.id) }
  return <><SecondaryHeader title="我的计划" subtitle="与 PC 端实时同步" fallback="/me"/><div className="page-action"><button onClick={() => setShowForm(value => !value)}>{showForm ? <X size={17}/> : <Plus size={17}/>} {showForm ? '取消' : '新增计划'}</button></div>
    {showForm && <section className="mobile-editor"><label>类型<select value={kind} onChange={event => setKind(event.target.value as typeof kind)}><option value="day">日计划</option><option value="week">周计划</option><option value="month">月计划</option></select></label><label>日期<input type="date" value={date} onChange={event => setDate(event.target.value)}/></label><label>标题<input value={title} onChange={event => setTitle(event.target.value)} placeholder="例如：本周只做突破"/></label><label>内容<textarea value={content} onChange={event => setContent(event.target.value)} rows={5} placeholder="偏向、关注标的、风险限制…"/></label><Button block color="primary" loading={create.isPending} disabled={!title.trim()} onClick={() => create.mutate()}>保存计划</Button></section>}
    <QueryState loading={query.isLoading} error={query.error} retry={() => query.refetch()}/><div className="plan-list">{rows.map(row => <article className="plan-row" key={row.id}><div><span>{row.kind === 'day' ? '日计划' : row.kind === 'week' ? '周计划' : '月计划'} · {row.plan_date || '未定日期'}</span><b>{row.title}</b>{row.content && <p>{row.content}</p>}</div><button onClick={() => deleteRow(row)} disabled={remove.isPending} aria-label="删除计划"><Trash2 size={17}/></button></article>)}{query.data && rows.length === 0 && <div className="empty-card">暂无计划</div>}</div>
  </>
}

function PositionsPage() {
  const client = useQueryClient()
  const [editing, setEditing] = useState<Position | null>(null)
  const [showForm, setShowForm] = useState(false)
  const [platform, setPlatform] = useState('众安')
  const [status, setStatus] = useState('未开始')
  const [side, setSide] = useState<'long' | 'short'>('long')
  const [name, setName] = useState('')
  const [shares, setShares] = useState('')
  const [price, setPrice] = useState('')
  const [fee, setFee] = useState('0')
  const [openedOn, setOpenedOn] = useState(today())
  const [expectedDays, setExpectedDays] = useState('21')
  const [reason, setReason] = useState('')
  const query = useQuery({ queryKey: ['positions'], queryFn: () => api.positions() })
  const strategies = useQuery({ queryKey: ['strategy-plans'], queryFn: api.strategyPlans, staleTime: 5 * 60_000 })
  const rows = query.data || [], active = rows.filter(row => row.status === '开始'), planned = rows.filter(row => row.status === '未开始'), closed = rows.filter(row => row.status === '已结束')
  const quotes = usePositionQuotes(rows)
  const invested = active.reduce((sum, row) => sum + Number(row.open_amount || 0), 0)
  const closedPnl = closed.reduce((sum, row) => sum + Number(row.pnl_amount || 0), 0)
  const resetForm = () => {
    setEditing(null); setPlatform('众安'); setStatus('未开始'); setSide('long'); setName(''); setShares(''); setPrice(''); setFee('0'); setOpenedOn(today()); setExpectedDays('21'); setReason('')
  }
  const openCreate = () => { resetForm(); setShowForm(true) }
  const openEdit = (row: Position) => {
    setEditing(row)
    setPlatform(row.platform)
    setStatus(row.status === '已结束' ? '开始' : row.status)
    setSide(row.strategy_side === 'short' ? 'short' : 'long')
    setName(row.name)
    setShares(String(row.shares ?? ''))
    setPrice(String(row.open_price ?? ''))
    setFee(String(row.fee ?? 0))
    setOpenedOn(row.opened_on || today())
    setExpectedDays(String(row.expected_days || 21))
    setReason(row.open_reason || '')
    setShowForm(true)
  }
  const closeForm = () => { setShowForm(false); resetForm() }
  const save = useMutation({
    mutationFn: async () => {
      const strategyId = await ensureSideStrategy(side, strategies.data || [])
      const payload = {
        platform,
        status,
        name: name.trim(),
        shares: Number(shares),
        open_price: Number(price),
        fee: Number(fee || 0),
        expected_days: Number(expectedDays || 21),
        open_reason: reason.trim() || null,
        strategy_id: strategyId,
      }
      if (editing) return api.updatePosition(editing.id, payload)
      return api.createPosition({ ...payload, opened_on: openedOn || null })
    },
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ['positions'] })
      client.invalidateQueries({ queryKey: ['strategy-plans'] })
      client.invalidateQueries({ queryKey: ['position-quotes'] })
      closeForm()
      Toast.show({ icon: 'success', content: editing ? '持仓已更新' : '持仓已添加' })
    },
    onError: (error: Error) => Toast.show({ icon: 'fail', content: error.message }),
  })
  const remove = useMutation({ mutationFn: (id: number) => api.deletePosition(id), onSuccess: () => { client.invalidateQueries({ queryKey: ['positions'] }); Toast.show({ icon: 'success', content: '持仓已删除' }) }, onError: error => Toast.show({ icon: 'fail', content: error.message }) })
  const deleteRow = (row: Position) => { if (window.confirm(`删除 ${row.name} 这笔持仓？`)) remove.mutate(row.id) }
  return <><SecondaryHeader title="我的持仓" subtitle="当前、计划与历史持仓" fallback="/me"/><div className="page-action"><button onClick={() => showForm ? closeForm() : openCreate()}>{showForm ? <X size={17}/> : <Plus size={17}/>} {showForm ? '取消' : '新增持仓'}</button></div>
    {showForm && <PositionEditor status={status} setStatus={setStatus} side={side} setSide={setSide} name={name} setName={setName} shares={shares} setShares={setShares} price={price} setPrice={setPrice} fee={fee} setFee={setFee} openedOn={openedOn} setOpenedOn={setOpenedOn} expectedDays={expectedDays} setExpectedDays={setExpectedDays} reason={reason} setReason={setReason} platform={platform} setPlatform={setPlatform} editing={Boolean(editing)} loading={save.isPending} onSave={() => save.mutate()}/>}
    <QueryState loading={query.isLoading} error={query.error} retry={() => query.refetch()}/>
    {query.data && <><div className="position-summary"><article><span>进行中</span><strong>{active.length}</strong></article><article><span>开仓金额</span><strong>{compact(invested)}</strong></article><article><span>已结盈亏</span><strong className={closedPnl >= 0 ? 'positive' : 'negative'}>{money(closedPnl)}</strong></article></div>
      <PositionGroup title="当前持仓" rows={active} empty="暂无进行中的持仓" quotes={quotes} onDelete={deleteRow} onEdit={openEdit}/><PositionGroup title="持仓计划" rows={planned} empty="暂无未开始的计划" quotes={quotes} onDelete={deleteRow} onEdit={openEdit}/><PositionGroup title="已结束" rows={closed} empty="暂无已结束的持仓" quotes={quotes}/>
    </>}
  </>
}

function PositionGroup({ title, rows, empty, quotes = {}, onDelete, onEdit }: { title: string; rows: Position[]; empty: string; quotes?: QuoteMap; onDelete?: (row: Position) => void; onEdit?: (row: Position) => void }) {
  return <section className="position-group"><h2>{title}<span>{rows.length}</span></h2><div className="position-list">{rows.map(row => <PositionRow key={row.id} row={row} quotes={quotes} onDelete={onDelete} onEdit={onEdit}/>)}{rows.length === 0 && <div className="empty-card">{empty}</div>}</div></section>
}

function PositionRow({ row, quotes = {}, onDelete, onEdit }: { row: Position; quotes?: QuoteMap; onDelete?: (row: Position) => void; onEdit?: (row: Position) => void }) {
  const live = livePnl(row, quotes)
  const pnl = live.amount
  const percent = live.percent
  const sideLabel = row.strategy_side === 'short' ? '做空' : row.strategy_side === 'long' ? '做多' : null
  const noteParts = [row.strategy_name, row.open_reason].filter(Boolean).filter((part, index, list) => list.indexOf(part) === index)
  const note = noteParts.filter(part => !(sideLabel && part === sideLabel)).join(' · ')
  return <article className="position-row"><header><div><b>{row.name}</b><span>{row.platform} · {row.status}{sideLabel ? ` · ${sideLabel}` : ''}</span></div><div className="position-head-actions"><em className={pnl >= 0 ? 'positive' : 'negative'}>{pnl >= 0 ? '+' : ''}{money(pnl)}</em>{onEdit && <button className="edit-button" onClick={() => onEdit(row)} aria-label="编辑持仓"><Pencil size={15}/></button>}{onDelete && <button onClick={() => onDelete(row)} aria-label="删除持仓"><Trash2 size={16}/></button>}</div></header><div className="position-details"><span>现价 <b>{live.price == null ? '—' : money(live.price)}</b></span><span>成本 <b>{money(row.open_price)}</b></span><span>开仓金额 <b>{money(row.open_amount)}</b></span><span>数量 <b>{row.shares}</b></span><span>市值 <b>{live.price == null ? money(row.open_amount) : money(live.price * Number(row.shares || 0))}</b></span><span>持仓 <b>{row.holding_days} 天</b></span></div>{percent != null && <div className={`position-pnl ${pnl >= 0 ? 'positive' : 'negative'}`}>{percent >= 0 ? '+' : ''}{percent.toFixed(2)}%</div>}{note && <p>{note}</p>}</article>
}

function TradesPage() {
  const trades = useQuery({ queryKey: ['trades'], queryFn: api.trades })
  return <><SecondaryHeader title="交易记录" subtitle="个人历史交易与盈亏" fallback="/me"/>
    <QueryState loading={trades.isLoading} error={trades.error} retry={() => trades.refetch()}/>
    <div className="trade-list">{(trades.data || []).map(trade => <TradeRow key={trade.id} trade={trade}/>)}{trades.data?.length === 0 && <div className="empty-card">暂无交易记录</div>}</div>
  </>
}

function RecipientsPage() {
  const query = useQuery({ queryKey: ['recipients'], queryFn: api.recipients })
  const rows = query.data?.items || []
  return <><SecondaryHeader title="系统推送人" subtitle="与 PC 端推送设置同步" fallback="/me"/><QueryState loading={query.isLoading} error={query.error} retry={() => query.refetch()}/><div className="recipient-list">{rows.map(row => <RecipientRow key={row.id} row={row}/>)}{query.data && rows.length === 0 && <div className="empty-card">暂无系统推送人</div>}</div></>
}

function RecipientRow({ row }: { row: SystemRecipient }) {
  const channels = row.channels?.length ? row.channels : [row.telegram && 'Telegram', row.whatsapp && 'WhatsApp', row.wx && '微信'].filter(Boolean) as string[]
  return <article className="recipient-row"><div className="recipient-avatar">{row.name.slice(0, 1).toUpperCase()}</div><div><b>{row.name}</b>{row.notes && <p>{row.notes}</p>}<div>{channels.length ? channels.map(channel => <span key={channel}>{channel}</span>) : <span className="disconnected">未接通</span>}</div></div></article>
}

function TradeRow({ trade }: { trade: Trade }) {
  return <article className="trade-row"><div><b>{trade.symbol}</b><span>{trade.date} · {trade.side === 'LONG' ? '做多' : '做空'}</span></div><strong className={trade.pnl_amount >= 0 ? 'positive' : 'negative'}>{trade.pnl_amount >= 0 ? '+' : ''}{money(trade.pnl_amount)}</strong>{trade.notes && <p>{trade.notes}</p>}</article>
}

function PageHead({ title, subtitle }: { title: string; subtitle?: string }) { return <div className="page-head"><div><h1>{title}</h1>{subtitle && <p>{subtitle}</p>}</div></div> }

function SecondaryHeader({ title, subtitle, fallback }: { title: string; subtitle?: string; fallback: string }) {
  const navigate = useNavigate()
  const goBack = () => window.history.length > 1 ? navigate(-1) : navigate(fallback, { replace: true })
  return <><header className="secondary-header"><button onClick={goBack} aria-label="返回"><ArrowLeft size={20}/></button><div><h1>{title}</h1>{subtitle && <p>{subtitle}</p>}</div></header><div className="secondary-header-spacer"/></>
}

const detailSections = [['financials', '财务摘要'], ['ratings', '机构评级'], ['options', '期权'], ['news', '近期新闻'], ['events', '下一事件']] as const
function StockDetailPage() {
  const { id, symbol = '' } = useParams(), numericId = Number(id)
  const client = useQueryClient()
  const watches = useQuery({ queryKey: ['watches'], queryFn: api.dailyWatches, enabled: !Number.isFinite(numericId) })
  const alreadyListed = Number.isFinite(numericId) || (watches.data || []).some(row => row.symbol.toUpperCase() === symbol.toUpperCase())
  const addToList = useMutation({ mutationFn: () => api.addScreenRows([{ symbol: symbol.toUpperCase() }]), onSuccess: data => { client.invalidateQueries({ queryKey: ['watches'] }); Toast.show({ icon: 'success', content: data.added.length ? '已加入股票列表' : '已在股票列表中' }) }, onError: error => Toast.show({ icon: 'fail', content: error.message }) })
  const [open, setOpen] = useState<string[]>(() => detailSections.map(([key]) => key))
  const toggle = (key: string) => setOpen(keys => keys.includes(key) ? keys.filter(item => item !== key) : [...keys, key])
  return <><SecondaryHeader title="股票详情" subtitle={symbol} fallback={Number.isFinite(numericId) ? '/stocks/list' : '/stocks/screen/results'}/><div className="detail-hero"><div className="detail-hero-top"><span>{symbol}</span>{!alreadyListed && watches.data && <button onClick={() => addToList.mutate()} disabled={addToList.isPending}><Plus size={15}/>{addToList.isPending ? '添加中' : '加入股票列表'}</button>}</div><h1>{symbol} 研究详情</h1><p>盘前 / 盘中 / 盘后挂单参考与研究数据</p></div>
    <EntryAnalysisPanel id={numericId} symbol={symbol}/>
    <div className="detail-sections">{detailSections.map(([key, label]) => <section className="detail-section" key={key}><button className="detail-section-toggle" onClick={() => toggle(key)} aria-expanded={open.includes(key)}><span>{label}</span>{open.includes(key) ? <ChevronUp size={19}/> : <ChevronDown size={19}/>}</button>{open.includes(key) && <DetailSection section={key} id={numericId} symbol={symbol}/>}</section>)}</div></>
}

function EntryAnalysisPanel({ id, symbol }: { id: number; symbol: string }) {
  const [horizon, setHorizon] = useState(10)
  const [cost, setCost] = useState(20)
  const [borrow, setBorrow] = useState(5)
  const analyze = useMutation({
    mutationFn: () => {
      const body = { horizon, cost_bps: cost, borrow_pct: borrow }
      return Number.isFinite(id) ? api.stockEntryAnalysis(id, body) : api.stockEntryAnalysisBySymbol(symbol, body)
    },
    onError: (error: Error) => Toast.show({ icon: 'fail', content: error.message }),
  })
  const result = analyze.data
  const moneyPx = (value?: number | null) => value == null ? '—' : `$${Number(value).toLocaleString('en-US', { maximumFractionDigits: 2 })}`
  const pctText = (value?: number | null) => value == null ? '—' : `${Number(value).toFixed(2)}%`
  return <section className="entry-analysis-card">
    <header><div><BarChart3 size={18}/><b>买卖价分析</b></div><span>下一交易日挂单参考</span></header>
    <p className="entry-lead">按盘前 / 盘中 / 盘后均可计算；候选限价默认对下一交易日有效，开盘跌穿止损则取消。</p>
    <div className="entry-controls">
      <label>持有周期<select value={horizon} onChange={event => setHorizon(Number(event.target.value))}><option value={3}>短线 3 日</option><option value={10}>波段 10 日</option><option value={30}>中期 30 日</option><option value={60}>中期 60 日</option></select></label>
      <label>往返成本 bp<input type="number" min={0} max={500} value={cost} onChange={event => setCost(Number(event.target.value) || 0)}/></label>
      <label>借券年化 %<input type="number" min={0} max={200} step="0.1" value={borrow} onChange={event => setBorrow(Number(event.target.value) || 0)}/></label>
    </div>
    <Button block color="primary" loading={analyze.isPending} onClick={() => analyze.mutate()}>{analyze.isPending ? '计算中…' : '计算买卖价格'}</Button>
    {analyze.error && <div className="state-card error">{(analyze.error as Error).message}</div>}
    {result && <>
      <div className="entry-meta"><span>截至 {result.asof}</span><span>趋势 {result.trend}</span><span>参考价 {moneyPx(result.price)}</span></div>
      <div className="entry-plan-list">{(result.sides || []).map((plan: any) => <article key={plan.side} className={`entry-plan ${plan.side}`}>
        <header><b>{plan.side === 'long' ? '买入候选' : '做空候选'}</b><span>{plan.status}</span></header>
        <p>{plan.reason}</p>
        <div className="entry-plan-grid"><span>限价 <b>{moneyPx(plan.entry)}</b></span><span>止损 <b>{moneyPx(plan.stop)}</b></span><span>目标 <b>{moneyPx(plan.target)}</b></span><span>盈亏比 <b>{plan.reward_risk ? `${plan.reward_risk}:1` : '—'}</b></span></div>
        <small>历史成交 {plan.history?.trades ?? 0}/{plan.history?.opportunities ?? 0} · 成交率 {pctText(plan.history?.fill_rate)} · 胜率 {pctText(plan.history?.win_rate)}</small>
      </article>)}</div>
    </>}
  </section>
}
function DetailSection({ section, id, symbol }: { section: string; id: number; symbol: string }) {
  const query = useQuery({ queryKey: ['stock-detail', id, symbol, section], queryFn: () => Number.isFinite(id) ? api.stockDetail<any>(id, section) : api.stockDetailBySymbol<any>(symbol, section), staleTime: 5 * 60_000 })
  if (section === 'ratings' && query.data && query.data.available === false) return null
  return <div className="accordion-body"><QueryState loading={query.isLoading} error={query.error} retry={() => query.refetch()}/>{query.data && <DetailContent section={section} data={query.data}/>}</div>
}
function DetailContent({ section, data }: { section: string; data: any }) {
  if (section === 'financials') {
    const company = data.company || {}, value = company.valuation || {}
    const quarter = data.quarter || {}, rows = [...(quarter.income || []), ...(quarter.balance || []), ...(quarter.cash || []), ...(quarter.ratios || [])]
    return <><div className="financial-source">{[company.exchange, company.sector, company.industry, data.source].filter(Boolean).join(' · ')}</div><div className="metric-grid financial-metrics"><Metric label="现价" value={money(company.price)}/><Metric label="市值" value={compact(company.market_cap)}/><Metric label="一年目标价" value={money(company.target)}/><Metric label="52 周高 / 低" value={company.high_52w != null || company.low_52w != null ? `${money(company.high_52w)} / ${money(company.low_52w)}` : company.range_52w || '—'}/><Metric label="PE（TTM）" value={multiple(value.trailing_pe)}/><Metric label="预期 PE" value={multiple(value.forward_pe)}/><Metric label="EPS（TTM）" value={money(value.eps_ttm)}/><Metric label="营收（TTM）" value={compact(value.revenue_ttm)}/><Metric label="自由现金流（TTM）" value={compact(value.free_cash_flow_ttm)}/><Metric label="净利率（TTM）" value={percentValue(value.net_margin_ttm)}/><Metric label="年度股息 / 股" value={money(value.annual_dividend_rate)}/><Metric label="股息率" value={percentValue(value.dividend_yield)}/><Metric label="派息率" value={percentValue(value.payout_ratio)}/><Metric label="除息日" value={value.ex_dividend_date || '—'}/><Metric label="5 年平均股息率" value={percentValue(value.five_year_avg_dividend_yield)}/></div>
      {data.notes?.length > 0 && <><h3 className="detail-subtitle">财务要点</h3><div className="financial-notes">{data.notes.map((note: any, index: number) => <article key={`${note.title}-${index}`} className={note.tone || ''}><span>{note.title}</span><p>{note.text}</p></article>)}</div></>}
      {rows.length > 0 && <><h3 className="detail-subtitle">最新季度财务</h3><p className="financial-period">报告期 {quarter.periods?.[0] || '—'}</p><div className="metric-grid quarter-metrics">{rows.map((row: any) => <Metric key={row.name} label={financialLabel(row.name)} value={formatFinancial(row.values?.[0], row.name)}/>)}</div></>}
      <p className="detail-copy">{company.about || '暂无公司简介'}</p></>
  }
  if (section === 'ratings') return <><div className="rating-summary"><span>综合意见</span><b>{data.summary?.label || '—'}</b><small>{data.summary?.total || 0} 位分析师</small></div><div className="metric-grid"><Metric label="上调" value={data.action_summary?.upgrade ?? '—'}/><Metric label="下调" value={data.action_summary?.downgrade ?? '—'}/><Metric label="平均目标价" value={money(data.targets?.mean)}/><Metric label="目标价中位数" value={money(data.targets?.median)}/></div></>
  if (section === 'options') return <OptionsDetail data={data}/>
  if (section === 'news') return <div className="news-list">{(data.items || []).map((item: any) => <a key={item.url} href={item.url} target="_blank" rel="noreferrer"><b>{item.title}</b><span>{item.source}</span></a>)}</div>
  if (section === 'events') return <div className="stack">{(data.items || []).map((item: any, index: number) => <article className="event-card" key={index}><b>{item.date}</b><p>{item.title}</p></article>)}</div>
  return null
}
function Metric({ label, value }: { label: string; value: React.ReactNode }) { return <div className="metric"><span>{label}</span><strong>{value}</strong></div> }
const multiple = (value?: number | null) => value == null || !Number.isFinite(Number(value)) ? '—' : `${Number(value).toFixed(1)}x`
const percentValue = (value?: number | null) => value == null || !Number.isFinite(Number(value)) ? '—' : `${Number(value).toFixed(2)}%`
const financialLabels: Record<string, string> = { 'Total Revenue': '营业收入', 'Gross Profit': '毛利润', 'Operating Income': '营业利润', 'Net Income': '净利润', 'Cash and Cash Equivalents': '现金及等价物', 'Total Current Assets': '流动资产', 'Total Assets': '总资产', 'Total Current Liabilities': '流动负债', 'Long-Term Debt': '长期债务', 'Total Liabilities': '总负债', 'Total Equity': '股东权益', 'Net Cash Flow-Operating': '经营现金流', 'Capital Expenditures': '资本开支', 'Net Cash Flow': '净现金流', 'Current Ratio': '流动比率', 'Gross Margin': '毛利率', 'Operating Margin': '营业利润率', 'Profit Margin': '净利率' }
const financialLabel = (name: string) => financialLabels[name] || name
function formatFinancial(value: any, name: string) {
  if (value == null || !Number.isFinite(Number(value))) return '—'
  if (name.includes('Margin')) return `${Number(value).toFixed(2)}%`
  if (name.includes('Ratio')) return Number(value).toFixed(2)
  return compact(Number(value))
}

type ChartPoint = [number, number]
function OptionsDetail({ data }: { data: any }) {
  const callOi = Number(data.diagnostics?.total_call_oi) || 0, putOi = Number(data.diagnostics?.total_put_oi) || 0
  const ratio = callOi > 0 ? putOi / callOi : null
  const callIv = Number(data.iv?.call_iv), putIv = Number(data.iv?.put_iv)
  const range = data.expected_range || {}
  const smileSeries = [
    { label: 'Call IV', color: '#79a0ff', points: optionPoints(data.volatility_smile?.calls).map(([x, y]) => [x, y * 100] as ChartPoint) },
    { label: 'Put IV', color: '#ed89b5', points: optionPoints(data.volatility_smile?.puts).map(([x, y]) => [x, y * 100] as ChartPoint) },
  ].filter(series => series.points.length)
  const pain = (data.max_pain?.curve || []).map((row: any) => [Number(row.strike), Number(row.loss)] as ChartPoint).filter(validPoint)
  const gamma = (data.gamma_by_strike || []).map((row: any) => ({ label: String(row.strike), value: Number(row.gamma_exposure) })).filter((row: any) => Number.isFinite(row.value))
  return <div className="options-detail">
    <div className="option-meta"><span>{data.source || '期权链'} · 到期日 {data.expiration || '—'}</span>{data.days_to_exp != null && <b>剩余 {data.days_to_exp} 天</b>}</div>
    <div className="metric-grid"><Metric label="标的现价" value={money(data.spot)}/><Metric label="Max Pain" value={money(data.max_pain?.strike)}/><Metric label="Call IV" value={Number.isFinite(callIv) ? `${(callIv * 100).toFixed(2)}%` : '—'}/><Metric label="Put IV" value={Number.isFinite(putIv) ? `${(putIv * 100).toFixed(2)}%` : '—'}/><Metric label="Put / Call OI" value={ratio == null ? '—' : ratio.toFixed(2)}/><Metric label="总持仓量" value={(callOi + putOi) > 0 ? (callOi + putOi).toLocaleString() : '—'}/></div>
    {Number.isFinite(Number(range.expected_low)) && Number.isFinite(Number(range.expected_high)) && <div className="option-range"><span>隐含预期区间</span><strong>{money(range.expected_low)} – {money(range.expected_high)}</strong><small>基于隐含波动率估算至到期日的价格范围</small></div>}
    <OptionChartCard title="波动率微笑" subtitle="各行权价的 Call / Put 隐含波动率"><MiniLineChart series={smileSeries} marker={Number(data.spot)}/></OptionChartCard>
    <OptionChartCard title="持仓结构" subtitle="Call 与 Put 总持仓量"><MiniBarChart rows={[{ label: 'Call', value: callOi }, { label: 'Put', value: putOi }]}/></OptionChartCard>
    <OptionChartCard title="Max Pain 曲线" subtitle="各行权价对应的到期内在价值总额"><MiniLineChart series={pain.length ? [{ label: '到期价值', color: '#a796f4', points: pain }] : []} marker={Number(data.max_pain?.strike)}/></OptionChartCard>
    <OptionChartCard title="Gamma 敞口" subtitle="各行权价的正负净 Gamma"><MiniBarChart rows={gamma} signed/></OptionChartCard>
    {data.warnings?.length > 0 && <div className="option-warnings">{data.warnings.map((warning: string) => <p key={warning}>{warning}</p>)}</div>}
  </div>
}

function optionPoints(raw: any): ChartPoint[] {
  return (raw?.strikes || []).map((strike: any, index: number) => [Number(strike), Number(raw.ivs?.[index])] as ChartPoint).filter(validPoint).sort((a: ChartPoint, b: ChartPoint) => a[0] - b[0])
}
function validPoint(point: ChartPoint) { return Number.isFinite(point[0]) && Number.isFinite(point[1]) }
function OptionChartCard({ title, subtitle, children }: { title: string; subtitle: string; children: React.ReactNode }) {
  return <section className="option-chart"><h3>{title}</h3><p>{subtitle}</p>{children}</section>
}
function MiniLineChart({ series, marker, axisLabels }: { series: { label: string; color: string; points: ChartPoint[] }[]; marker?: number; axisLabels?: [string, string] }) {
  const all = series.flatMap(item => item.points)
  if (!all.length) return <div className="chart-empty">暂无足够数据</div>
  const xs = all.map(point => point[0]), ys = all.map(point => point[1]), minX = Math.min(...xs), maxX = Math.max(...xs), minY = Math.min(...ys), maxY = Math.max(...ys)
  const x = (value: number) => 12 + (value - minX) / (maxX - minX || 1) * 276
  const y = (value: number) => 116 - (value - minY) / (maxY - minY || 1) * 94
  return <><div className="chart-legend">{series.map(item => <span key={item.label}><i style={{ background: item.color }}/>{item.label}</span>)}</div><svg className="mini-chart" viewBox="0 0 300 132" role="img"><line x1="12" y1="116" x2="288" y2="116"/><line x1="12" y1="22" x2="12" y2="116"/>{Number.isFinite(marker) && marker! >= minX && marker! <= maxX && <line className="chart-marker" x1={x(marker!)} y1="18" x2={x(marker!)} y2="116"/>}{series.map(item => <polyline key={item.label} points={item.points.map(point => `${x(point[0])},${y(point[1])}`).join(' ')} fill="none" stroke={item.color} strokeWidth="2.2"/>)}</svg><div className="chart-axis"><span>{axisLabels?.[0] || minX.toLocaleString()}</span><span>{axisLabels?.[1] || maxX.toLocaleString()}</span></div></>
}
function MiniBarChart({ rows, signed = false }: { rows: { label: string; value: number }[]; signed?: boolean }) {
  const usable = rows.filter(row => Number.isFinite(row.value) && (signed || row.value > 0)).slice(0, 28)
  if (!usable.length || !usable.some(row => row.value !== 0)) return <div className="chart-empty">暂无足够持仓数据</div>
  const max = Math.max(...usable.map(row => Math.abs(row.value)), 1)
  return <div className={`mini-bars ${signed ? 'signed' : ''}`}>{usable.map((row, index) => <div key={`${row.label}-${index}`}><i style={{ height: `${Math.max(3, Math.abs(row.value) / max * 84)}%` }} className={row.value < 0 ? 'down' : 'up'}/><span>{usable.length <= 8 || index % Math.ceil(usable.length / 6) === 0 ? row.label : ''}</span></div>)}</div>
}

type ScreenRange = { min: string; max: string }
type MobileScreenResult = Awaited<ReturnType<typeof api.runScreen>>
const mobileScreenMemory: { config: any | null; result: MobileScreenResult | null } = { config: null, result: null }
const SCREEN_RANGE_KEYS = ['market_cap','price','avg_dollar_volume_20d','revenue_growth_yoy','net_margin','pe','weekly_return','monthly_return','regression_deviation','revenue_growth_streak','revenue_acceleration_streak','eps_growth_yoy','gross_margin_change_yoy','operating_margin_change_yoy','fcf_positive_years','fcf_margin','debt_to_assets','net_debt_ebitda','interest_coverage','sector_pe_percentile','relative_spy_1m','relative_spy_3m','relative_spy_6m','days_above_ma50','days_above_ma200','days_since_ma50_breakout','days_since_ma200_breakout','distance_52w_high','distance_52w_low','volume_ratio_20d','volatility_60d','max_drawdown_1y','days_to_earnings'] as const
const ADVANCED_SCREEN_KEYS = new Set(SCREEN_RANGE_KEYS.slice(9))
const emptyScreenRanges = () => Object.fromEntries(SCREEN_RANGE_KEYS.map(key => [key, { min: '', max: '' }])) as Record<string, ScreenRange>
type BuiltinScreenPreset = { id: string; name: string; purpose: string; incomplete?: string; config: any }
const BUILTIN_SCREEN_PRESETS: BuiltinScreenPreset[] = [
  { id:'growth', name:'盈利成长', purpose:'增长且已经赚钱的公司', config:{ sort:'revenue_growth_yoy', sort_dir:'desc', ranges:{ market_cap:{min:10}, avg_dollar_volume_20d:{min:20}, revenue_growth_yoy:{min:15}, net_margin:{min:10}, revenue_growth_streak:{min:4}, gross_margin_change_yoy:{min:0} }, profitable_only:true, free_cash_flow_positive:true } },
  { id:'cash', name:'现金流与估值', purpose:'盈利、现金流为正且 PE 较低', config:{ sort:'pe', sort_dir:'asc', ranges:{ market_cap:{min:10}, avg_dollar_volume_20d:{min:20}, revenue_growth_yoy:{min:0}, net_margin:{min:8}, pe:{min:5,max:25}, fcf_positive_years:{min:3}, fcf_margin:{min:8} }, profitable_only:true, free_cash_flow_positive:true } },
  { id:'pullback', name:'上升趋势中的回落', purpose:'中期趋势向上、近期回落', incomplete:'均线关系与均线变化尚未接入', config:{ sort:'weekly_return', sort_dir:'asc', ranges:{ market_cap:{min:10}, avg_dollar_volume_20d:{min:30}, weekly_return:{min:-8,max:-2}, distance_52w_high:{min:-15,max:-5}, days_to_earnings:{min:7} }, above_ma50:'above', earnings_mode:'exclude' } },
  { id:'breakout', name:'放量创近期新高', purpose:'价格与成交量同时变化', incomplete:'近期最高收盘价条件尚未接入', config:{ sort:'volume_ratio_20d', sort_dir:'desc', ranges:{ market_cap:{min:2}, price:{min:5}, avg_dollar_volume_20d:{min:30}, volume_ratio_20d:{min:1.5}, days_to_earnings:{min:3} }, above_ma50:'above', earnings_mode:'exclude' } },
  { id:'regression', name:'回归线下方观察', purpose:'低于上升价格趋势的股票', incomplete:'回归斜率与持续天数尚未接入', config:{ sort:'regression_deviation', sort_dir:'asc', ranges:{ market_cap:{min:10}, avg_dollar_volume_20d:{min:20}, regression_deviation:{min:-2.5,max:-1} }, profitable_only:true, free_cash_flow_positive:true } },
  { id:'earnings', name:'财报研究名单', purpose:'未来两周发布财报的公司', config:{ sort:'days_to_earnings', sort_dir:'asc', universe:'all', ranges:{ market_cap:{min:10}, avg_dollar_volume_20d:{min:20} }, earnings_within_days:14 } },
]

function ScreenerPage() {
  const client = useQueryClient(), navigate = useNavigate()
  const [ranges, setRanges] = useState(() => { const value = emptyScreenRanges(); value.market_cap.min='10'; value.avg_dollar_volume_20d.min='20'; return value })
  const [logic,setLogic]=useState('all'), [universe,setUniverse]=useState('spy'), [sort,setSort]=useState('market_cap'), [sortDir,setSortDir]=useState('desc')
  const [sectors,setSectors]=useState<string[]>([]), [profitable,setProfitable]=useState(false), [fcfPositive,setFcfPositive]=useState(false), [ma50,setMa50]=useState('any'), [turnedProfitable,setTurnedProfitable]=useState(false)
  const [earningsOnly,setEarningsOnly]=useState(false), [earningsDays,setEarningsDays]=useState('30'), [earningsMode,setEarningsMode]=useState('include'), [presetName,setPresetName]=useState('')
  const [presetNotice,setPresetNotice]=useState(''), [incomplete,setIncomplete]=useState(false)
  const options = useQuery({ queryKey:['screen-options'], queryFn:api.screenOptions })
  const presets = useQuery({ queryKey:['screen-presets'], queryFn:api.screenPresets })
  const number = (value:string, scale=1) => value === '' || !Number.isFinite(Number(value)) ? null : Number(value)*scale
  const buildConfig = () => ({ logic, sort, sort_dir:sortDir, universe, columns:['price','market_cap','sector_zh','avg_dollar_volume_20d','revenue_growth_yoy','net_margin','free_cash_flow_positive','pe','weekly_return','monthly_return','above_ma50','regression_deviation','earnings_date'], filters:{ ranges:Object.fromEntries(Object.entries(ranges).map(([key,value]) => { const scale=key==='market_cap'?1e9:key==='avg_dollar_volume_20d'?1e6:1; return [key,{min:number(value.min,scale),max:number(value.max,scale)}] })), sectors, profitable_only:profitable, free_cash_flow_positive:fcfPositive, above_ma50:ma50==='above'?true:ma50==='below'?false:null, advanced_enabled:turnedProfitable||Object.entries(ranges).some(([key,value])=>ADVANCED_SCREEN_KEYS.has(key as any)&&(value.min!==''||value.max!=='')), turned_profitable:turnedProfitable, earnings_mode:earningsMode, earnings_within_days:earningsOnly?Number(earningsDays):null } })
  const screen = useMutation({ mutationFn:()=>{const config=buildConfig();mobileScreenMemory.config=config;return api.runScreen(config)}, onSuccess:data=>{mobileScreenMemory.result=data;client.setQueryData(['mobile-screen-result'],data);navigate('/stocks/screen/results')}, onError:error=>Toast.show({icon:'fail',content:error.message}) })
  const savePreset = useMutation({ mutationFn:()=>api.saveScreenPreset({name:presetName.trim(),config:buildConfig()}), onSuccess:()=>{client.invalidateQueries({queryKey:['screen-presets']});setPresetName('');Toast.show({icon:'success',content:'方案已保存'})}, onError:error=>Toast.show({icon:'fail',content:error.message}) })
  const deletePreset = useMutation({ mutationFn:(id:number)=>api.deleteScreenPreset(id), onSuccess:()=>client.invalidateQueries({queryKey:['screen-presets']}) })
  const setRange=(key:string,side:keyof ScreenRange,value:string)=>setRanges(old=>({...old,[key]:{...old[key],[side]:value}}))
  const reset=()=>{const value=emptyScreenRanges();value.market_cap.min='10';value.avg_dollar_volume_20d.min='20';setRanges(value);setLogic('all');setUniverse('spy');setSort('market_cap');setSortDir('desc');setSectors([]);setProfitable(false);setFcfPositive(false);setMa50('any');setTurnedProfitable(false);setEarningsOnly(false);setEarningsMode('include');setIncomplete(false);setPresetNotice('')}
  const loadConfig=(config:any)=>{reset();const next=emptyScreenRanges();Object.entries(config.filters?.ranges||{}).forEach(([key,value]:any)=>{if(!next[key])return;const scale=key==='market_cap'?1e9:key==='avg_dollar_volume_20d'?1e6:1;next[key]={min:value.min==null?'':String(value.min/scale),max:value.max==null?'':String(value.max/scale)}});setRanges(next);setLogic(config.logic||'all');setUniverse(config.universe||'spy');setSort(config.sort||'market_cap');setSortDir(config.sort_dir||'desc');const f=config.filters||{};setSectors(f.sectors||[]);setProfitable(!!f.profitable_only);setFcfPositive(!!f.free_cash_flow_positive);setMa50(f.above_ma50===true?'above':f.above_ma50===false?'below':'any');setTurnedProfitable(!!f.turned_profitable);setEarningsMode(f.earnings_mode||'include');setEarningsOnly(!!f.earnings_within_days);setEarningsDays(String(f.earnings_within_days||30))}
  useEffect(()=>{if(mobileScreenMemory.config)loadConfig(mobileScreenMemory.config)},[])
  const applyBuiltin=(preset:BuiltinScreenPreset)=>{reset();const next=emptyScreenRanges();Object.entries(preset.config.ranges||{}).forEach(([key,value]:any)=>{if(next[key])next[key]={min:value.min==null?'':String(value.min),max:value.max==null?'':String(value.max)}});setRanges(next);setSort(preset.config.sort||'market_cap');setSortDir(preset.config.sort_dir||'desc');setUniverse(preset.config.universe||'spy');setProfitable(!!preset.config.profitable_only);setFcfPositive(!!preset.config.free_cash_flow_positive);setMa50(preset.config.above_ma50||'any');setEarningsMode(preset.config.earnings_mode||'include');setEarningsOnly(!!preset.config.earnings_within_days);setEarningsDays(String(preset.config.earnings_within_days||30));setIncomplete(!!preset.incomplete);setPresetNotice(preset.incomplete?`${preset.name}：${preset.incomplete}，已填入当前支持条件。`:`${preset.name}：${preset.purpose}`)}
  const rangeField=(key:string,label:string,side:keyof ScreenRange='min')=><label>{label}<input type="number" value={ranges[key][side]} onChange={event=>setRange(key,side,event.target.value)} placeholder="不限"/></label>
  const pair=(key:string,label:string)=><>{rangeField(key,`${label}下限`)}{rangeField(key,`${label}上限`,'max')}</>
  return <><SecondaryHeader title="股票筛选" subtitle="与 PC 端使用同一套条件" fallback="/stocks"/><p className="screen-lead">按股票池、基础信息、财务表现、估值和价格走势组合筛选。缺失数据不会按 0 处理。</p>
    <section className="mobile-screen-card"><div className="mobile-screen-head"><div><b>筛选条件</b><small>默认同时满足全部条件</small></div><div className="screen-logic"><button className={logic==='all'?'active':''} onClick={()=>setLogic('all')}>同时满足</button><button className={logic==='any'?'active':''} onClick={()=>setLogic('any')}>满足任一</button></div></div>
      <div className="screen-basic-groups"><fieldset><legend>基础信息</legend><div className="mobile-filter-grid">{pair('market_cap','市值（十亿美元）')}{pair('price','股价（美元）')}{rangeField('avg_dollar_volume_20d','20日日均成交额下限（百万美元）')}</div><div className="sector-picker"><span>行业（可多选）</span><div>{(options.data?.sectors||[]).map(item=><button type="button" key={item} className={sectors.includes(item)?'active':''} onClick={()=>setSectors(old=>old.includes(item)?old.filter(value=>value!==item):[...old,item])}>{item}</button>)}</div></div></fieldset>
        <fieldset><legend>财务表现与估值</legend><div className="earnings-inline"><label><input type="checkbox" checked={earningsOnly} onChange={event=>setEarningsOnly(event.target.checked)}/>只看未来</label><select value={earningsDays} disabled={!earningsOnly} onChange={event=>setEarningsDays(event.target.value)}><option value="7">7 天</option><option value="14">14 天</option><option value="30">30 天</option></select><span>发布财报</span></div><div className="mobile-filter-grid">{pair('revenue_growth_yoy','营收同比（%）')}{rangeField('net_margin','净利率下限（%）')}{pair('pe','PE ')}</div><div className="check-row"><label><input type="checkbox" checked={profitable} onChange={event=>setProfitable(event.target.checked)}/>仅盈利公司</label><label><input type="checkbox" checked={fcfPositive} onChange={event=>setFcfPositive(event.target.checked)}/>自由现金流为正</label></div><p>PE 仅使用正值；亏损公司的负 PE 按缺失处理。</p></fieldset>
        <fieldset><legend>价格走势</legend><div className="mobile-filter-grid">{pair('weekly_return','周涨跌（%）')}{pair('monthly_return','月涨跌（%）')}{pair('regression_deviation','回归线偏离（%）')}<label>股价与 50 日均线<select value={ma50} onChange={event=>setMa50(event.target.value)}><option value="any">不限</option><option value="above">高于均线</option><option value="below">低于均线</option></select></label></div></fieldset></div>
      <details className="mobile-advanced"><summary><span><b>高级搜索</b><small>变化、比较和连续性条件</small></span><ChevronDown size={18}/></summary><div className="advanced-mobile-body"><ScreenAdvancedGroup title="增长持续性">{rangeField('revenue_growth_streak','营收连续同比增长（季度）')}{rangeField('revenue_acceleration_streak','营收增速连续提高（季度）')}{pair('eps_growth_yoy','EPS 同比（%）')}</ScreenAdvancedGroup><ScreenAdvancedGroup title="盈利改善">{rangeField('gross_margin_change_yoy','毛利率同比提升（百分点）')}{rangeField('operating_margin_change_yoy','营业利润率同比提升（百分点）')}<label className="wide-check"><input type="checkbox" checked={turnedProfitable} onChange={event=>setTurnedProfitable(event.target.checked)}/>最近一季同比扭亏</label></ScreenAdvancedGroup><ScreenAdvancedGroup title="现金流质量">{rangeField('fcf_positive_years','FCF 连续为正（年）')}{pair('fcf_margin','自由现金流率（%）')}</ScreenAdvancedGroup><ScreenAdvancedGroup title="债务情况">{rangeField('debt_to_assets','资产负债率上限（%）','max')}{rangeField('net_debt_ebitda','净负债 / EBITDA 上限','max')}{rangeField('interest_coverage','利息覆盖倍数下限')}</ScreenAdvancedGroup><ScreenAdvancedGroup title="相对估值与强弱">{rangeField('sector_pe_percentile','行业 PE 百分位上限（%）','max')}{rangeField('relative_spy_1m','近 1 月超额 SPY 下限（%）')}{rangeField('relative_spy_3m','近 3 月超额 SPY 下限（%）')}{rangeField('relative_spy_6m','近 6 月超额 SPY 下限（%）')}</ScreenAdvancedGroup><ScreenAdvancedGroup title="趋势变化">{rangeField('days_above_ma50','连续站上 MA50（天）')}{rangeField('days_above_ma200','连续站上 MA200（天）')}{rangeField('days_since_ma50_breakout','距突破 MA50 天数上限','max')}{rangeField('days_since_ma200_breakout','距突破 MA200 天数上限','max')}</ScreenAdvancedGroup><ScreenAdvancedGroup title="位置、成交量与风险">{rangeField('distance_52w_high','距 52 周高点下限（%）')}{rangeField('distance_52w_low','距 52 周低点涨幅上限（%）','max')}{rangeField('volume_ratio_20d','成交量 / 20 日均量下限')}{rangeField('volatility_60d','60 日波动率上限（%）','max')}{pair('max_drawdown_1y','一年最大回撤（%）')}</ScreenAdvancedGroup><ScreenAdvancedGroup title="财报时间"><label>处理方式<select value={earningsMode} onChange={event=>setEarningsMode(event.target.value)}><option value="include">包含该区间</option><option value="exclude">排除该区间</option></select></label>{pair('days_to_earnings','距财报天数')}</ScreenAdvancedGroup></div></details>
      <div className="mobile-screen-toolbar"><label>股票池<select value={universe} onChange={event=>setUniverse(event.target.value)}><option value="spy">SPY 标普500</option><option value="qqq">QQQ 纳斯达克100</option><option value="dia">DIA 道琼斯30</option><option value="all">全部</option></select></label><label>排序<select value={sort} onChange={event=>setSort(event.target.value)}><option value="market_cap">市值</option><option value="revenue_growth_yoy">营收同比</option><option value="pe">PE</option><option value="weekly_return">周涨跌</option><option value="volume_ratio_20d">放量倍数</option><option value="days_to_earnings">距财报天数</option></select></label><label>方向<select value={sortDir} onChange={event=>setSortDir(event.target.value)}><option value="desc">从高到低</option><option value="asc">从低到高</option></select></label><Button block color="primary" size="large" loading={screen.isPending} disabled={incomplete} onClick={()=>screen.mutate()}>{incomplete?'缺少高级条件':'运行筛选'}</Button></div></section>
    <SectionTitle title="内置方案"/><div className="mobile-presets">{BUILTIN_SCREEN_PRESETS.map(preset=><button key={preset.id} onClick={()=>applyBuiltin(preset)}><b>{preset.name}</b><span>{preset.purpose}</span><small>{preset.incomplete?'缺少部分条件':'可直接运行'}</small></button>)}</div>{presetNotice&&<p className={`preset-message ${incomplete?'warning':''}`}>{presetNotice}</p>}
    <section className="saved-preset-bar"><select defaultValue="" onChange={event=>{const row=presets.data?.items.find(item=>String(item.id)===event.target.value);if(row)loadConfig(row.config)}}><option value="">加载我的方案</option>{presets.data?.items.map(row=><option value={row.id} key={row.id}>{row.name}</option>)}</select><input value={presetName} onChange={event=>setPresetName(event.target.value)} placeholder="方案名称"/><button disabled={!presetName.trim()||savePreset.isPending} onClick={()=>savePreset.mutate()}>保存</button></section>{(presets.data?.items||[]).length>0&&<div className="saved-preset-chips">{presets.data?.items.map((row:StockScreenPreset)=><span key={row.id}>{row.name}<button onClick={()=>deletePreset.mutate(row.id)}>×</button></span>)}</div>}
    {screen.error&&<div className="state-card error">{screen.error.message}</div>}
  </>
}

function ScreenAdvancedGroup({title,children}:{title:string;children:React.ReactNode}) { return <fieldset><legend>{title}</legend><div className="mobile-filter-grid">{children}</div></fieldset> }

function ScreenerResultsPage() {
  const client=useQueryClient(), navigate=useNavigate()
  const result=(client.getQueryData(['mobile-screen-result'])||mobileScreenMemory.result) as MobileScreenResult|null
  const watches=useQuery({queryKey:['watches'],queryFn:api.dailyWatches})
  const [industry,setIndustry]=useState('')
  const rows=(result?.items||[]).filter(row=>!industry||row.sector_zh===industry)
  const listedSymbols=new Set((watches.data||[]).map(row=>row.symbol.toUpperCase()))
  const sectors=[...new Set((result?.items||[]).map(row=>row.sector_zh).filter((value):value is string=>typeof value==='string'&&!!value))].sort()
  const add=useMutation({mutationFn:(row:ScreenRow)=>api.addScreenRows([row]),onSuccess:data=>{client.invalidateQueries({queryKey:['watches']});Toast.show({icon:'success',content:`已加入 ${data.added.length} 只，已存在 ${data.skipped.length} 只`})},onError:error=>Toast.show({icon:'fail',content:error.message})})
  const display=(row:ScreenRow,key:string)=>{const value=row[key];if(value==null)return'缺失';if(key==='market_cap'||key==='avg_dollar_volume_20d')return compact(Number(value));if(['revenue_growth_yoy','net_margin','weekly_return','monthly_return','regression_deviation'].includes(key))return pct(Number(value));return String(value)}
  return <><SecondaryHeader title="筛选结果" subtitle={result?`${result.count} 只股票`:'尚未运行筛选'} fallback="/stocks/screen"/>{!result?<div className="empty-card screen-result-empty"><ListFilter size={22}/><b>还没有筛选结果</b><span>请先设置条件并运行筛选</span><Button color="primary" onClick={()=>navigate('/stocks/screen')}>返回筛选</Button></div>:<><section className="screen-run-summary"><b>筛选结果 · {result.count} 只</b><p>股票池 {result.universe_count??'—'} 只 → 基础通过 {result.prefilter_count??'—'} 只 → 高级通过 {result.advanced_pass_count??result.count} 只；当前精算 {result.scanned_count??'—'} 只，缺失数据 {result.missing_count??0} 只。</p>{result.source&&<small>{result.source}</small>}</section><div className="result-page-toolbar"><label>行业筛选<select value={industry} onChange={event=>setIndustry(event.target.value)}><option value="">全部行业</option>{sectors.map(value=><option key={value}>{value}</option>)}</select></label><button onClick={()=>navigate('/stocks/screen')}><ListFilter size={15}/>修改条件</button></div>{rows.length===0?<div className="empty-card">没有股票满足当前条件，可以放宽范围或切换“满足任一”。</div>:<div className="screen-results">{rows.map(row=><article key={row.symbol}><button className="result-main" onClick={()=>navigate(`/stocks/symbol/${row.symbol}`)}><div><b>{row.symbol}</b><span>{row.name||row.sector_zh}</span></div><strong>{money(row.price)}</strong></button><div className="result-metrics"><span>市值 {display(row,'market_cap')}</span><span>行业 {display(row,'sector_zh')}</span><span>营收 {display(row,'revenue_growth_yoy')}</span><span>净利率 {display(row,'net_margin')}</span><span>PE {display(row,'pe')}</span><span>周 {display(row,'weekly_return')}</span></div>{watches.data&&!listedSymbols.has(row.symbol.toUpperCase())&&<button className="add-button" onClick={()=>add.mutate(row)} disabled={add.isPending}>加入股票列表</button>}</article>)}</div>}</>}</>
}

export default function App() { return <AuthProvider><Routes><Route path="/login" element={<LoginPage/>}/><Route path="/*" element={<Shell/>}/></Routes></AuthProvider> }
