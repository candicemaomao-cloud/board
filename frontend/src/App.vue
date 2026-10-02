<script setup>
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from './api'
import { authState, can, clearSession, patchCurrentUser, setSession } from './auth'
import { isoDate, rangeDates } from './format'
import { armQuoteTimer, clearQuoteTimer } from './marketHours'
import LoginPage from './components/LoginPage.vue'
import UsersPage from './components/UsersPage.vue'
import StatCards from './components/StatCards.vue'
import EquityChart from './components/EquityChart.vue'
import DailyBarChart from './components/DailyBarChart.vue'
import CalendarHeatmap from './components/CalendarHeatmap.vue'
import MonthChart from './components/MonthChart.vue'
import TagChart from './components/TagChart.vue'
import ReviewPanel from './components/ReviewPanel.vue'
import TradeTable from './components/TradeTable.vue'
import GoalPanel from './components/GoalPanel.vue'
import TapeBar from './components/TapeBar.vue'
import PlansPage from './components/PlansPage.vue'
import PositionsPage from './components/PositionsPage.vue'
import NewsPage from './components/NewsPage.vue'
import MacroPage from './components/MacroPage.vue'
import MacroRiskPage from './components/MacroRiskPage.vue'
import MacroEventStudyPage from './components/MacroEventStudyPage.vue'
import StrategyPage from './components/StrategyPage.vue'
import IndicatorVerifyPage from './components/IndicatorVerifyPage.vue'
import CryptoAnalysisPage from './components/CryptoAnalysisPage.vue'
import CryptoNewsPage from './components/CryptoNewsPage.vue'
import CryptoStrategyPage from './components/CryptoStrategyPage.vue'
import CryptoStrategyBacktestPage from './components/CryptoStrategyBacktestPage.vue'
import CryptoCustomStrategyPage from './components/CryptoCustomStrategyPage.vue'
import CryptoCustomBacktestPage from './components/CryptoCustomBacktestPage.vue'
import IntradayStockPage from './components/IntradayStockPage.vue'
import NewStrategyPage from './components/NewStrategyPage.vue'
import StockWatchPage from './components/StockWatchPage.vue'
import OptionsPage from './components/OptionsPage.vue'
import RegressionScreenerPage from './components/RegressionScreenerPage.vue'
import StockScreenerPage from './components/StockScreenerPage.vue'
import DailyWatchPage from './components/DailyWatchPage.vue'
import StockDetailPage from './components/StockDetailPage.vue'
import DailyWatchAnalysisPage from './components/DailyWatchAnalysisPage.vue'
import StockJournalPage from './components/StockJournalPage.vue'
import FearVixStudyPage from './components/FearVixStudyPage.vue'
import EarningsStocksPage from './components/EarningsStocksPage.vue'
import ArbStrategyPage from './components/ArbStrategyPage.vue'
import PairScanPage from './components/PairScanPage.vue'
import IntradayScanPage from './components/IntradayScanPage.vue'
import AlertsPage from './components/AlertsPage.vue'
import RecipientsPage from './components/RecipientsPage.vue'
import FundamentalsPage from './components/FundamentalsPage.vue'
import RiskModelPage from './components/RiskModelPage.vue'
import PortfolioPage from './components/PortfolioPage.vue'
import RiskPortfoliosPage from './components/RiskPortfoliosPage.vue'
import BudgetModelPage from './components/BudgetModelPage.vue'
import KakeyaModelPage from './components/KakeyaModelPage.vue'
import BoardCalendar from './components/BoardCalendar.vue'

const RANGE_OPTIONS = [
  { key: '8w', label: '近 8 周' },
  { key: '26w', label: '近 26 周' },
  { key: 'ytd', label: '本年' },
  { key: 'all', label: '全部' },
]

const NAV = [
  { key: 'board', label: '看板', perm: 'menu.board' },
  { key: 'plans', label: '计划', perm: 'menu.plans' },
  { key: 'positions', label: '持仓', perm: 'menu.positions' },
  { key: 'riskHub', label: '风险', perm: 'menu.riskHub' },
  { key: 'macro', label: '宏观因素', perm: 'menu.macro' },
  { key: 'indicators', label: '指标', perm: 'menu.indicators' },
  { key: 'stockAnalysis', label: '股票分析', perm: 'menu.stockAnalysis' },
  { key: 'cryptoAnalysis', label: '虚拟币分析', perm: 'menu.cryptoAnalysis' },
  { key: 'strategy', label: '策略', perm: 'menu.strategy' },
  { key: 'alerts', label: '推送', perm: 'menu.alerts' },
  { key: 'users', label: '用户管理', perm: 'menu.users' },
]
const PLAN_NAV = [
  { key: 'dayPlan', label: '日计划', perm: 'menu.dayPlan', kind: 'day' },
  { key: 'weekPlan', label: '周计划', perm: 'menu.weekPlan', kind: 'week' },
  { key: 'monthPlan', label: '月计划', perm: 'menu.monthPlan', kind: 'month' },
]
const PLAN_VIEWS = new Set(PLAN_NAV.map((x) => x.key))
/** 有子菜单的父项；默认全部收起 */
const PARENT_KEYS = ['plans', 'riskHub', 'macro', 'indicators', 'stockAnalysis', 'cryptoAnalysis', 'strategy', 'alerts']
const MACRO_NAV = [
  { key: 'fedRisk', label: '风险指数', perm: 'menu.macro' },
  { key: 'eventStudy', label: '宏观统计', perm: 'menu.macro' },
  { key: 'cpi', label: 'CPI', perm: 'menu.macro' },
  { key: 'ppi', label: 'PPI', perm: 'menu.macro' },
  { key: 'pce', label: 'PCE', perm: 'menu.macro' },
  { key: 'nfp', label: '非农', perm: 'menu.macro' },
  { key: 'pmi', label: 'PMI', perm: 'menu.macro' },
  { key: 'treasury', label: '美债收益率', perm: 'menu.macro' },
]
const PUSH_NAV = [
  { key: 'list', label: '列表', perm: 'menu.alerts' },
  { key: 'people', label: '推送人', perm: 'menu.recipients' },
]
const STRAT_NAV = [
  { key: 'strategy', label: '套利策略', perm: 'menu.strategy' },
  { key: 'pairs', label: '找配对', perm: 'menu.pairs' },
  { key: 'intraday', label: '日内配对', perm: 'menu.intraday' },
]
const IND_NAV = [
  { key: 'indicatorList', label: '指标列表', perm: 'menu.indicatorList' },
  { key: 'indicatorVerify', label: '指标验证', perm: 'menu.indicatorVerify' },
]
const STOCK_NAV = [
  { key: 'stockScreener', label: '股票筛选', perm: 'menu.stockScreener' },
  { key: 'fundamentals', label: '财报分析', perm: 'menu.fundamentals' },
  { key: 'earningsStocks', label: '财报股票', perm: 'menu.earningsStocks', hidden: true },
  { key: 'options', label: '期权分析', perm: 'menu.options' },
  { key: 'regression', label: '股票回归线', perm: 'menu.regression' },
  { key: 'dailyWatch', label: '股票列表', perm: 'menu.dailyWatch' },
  { key: 'stockJournal', label: '股票日志', perm: 'menu.stockJournal' },
  { key: 'fearVix', label: '恐慌指标', perm: 'menu.fearVix' },
  { key: 'news', label: '新闻', perm: 'menu.news' },
  // 实时播报已并入股票列表（开盘日轮询）；保留 key 仅作旧书签兼容跳转
  { key: 'live', label: '实时播报', perm: 'menu.live', hidden: true },
]
const CRYPTO_NAV = [
  { key: 'cryptoMarket', label: '币列表', perm: 'menu.cryptoMarket' },
  { key: 'cryptoNews', label: '新闻', perm: 'menu.cryptoNews' },
  { key: 'cryptoStrategy', label: '指标策略', perm: 'menu.cryptoStrategy' },
  { key: 'cryptoCustomStrategy', label: '自定义策略', perm: 'menu.cryptoCustomStrategy' },
]
const IND_VIEWS = new Set(['indicatorList', 'indicatorVerify', 'intradayStock', 'stockWatch', 'newStrategy'])
const STOCK_VIEWS = new Set([...STOCK_NAV.map((x) => x.key), 'dailyWatchAnalysis', 'stockDetail'])
const CRYPTO_VIEWS = new Set([
  ...CRYPTO_NAV.map((x) => x.key),
  'cryptoCustomBacktest',
  'cryptoStrategyBacktest',
])
const RISK_NAV = [
  { key: 'risk', label: '风险模型', perm: 'menu.risk' },
  { key: 'riskPortfolios', label: '风险组合', perm: 'menu.riskPortfolios' },
  {
    key: 'budgetHub',
    label: '预算模型',
    perm: 'menu.budgetModels',
    children: [
      { key: 'budgetModels', label: '赚亏展望', perm: 'menu.budgetModels' },
      { key: 'kakeyaModel', label: '三维挂谷模型', perm: 'menu.kakeyaModel' },
    ],
  },
  { key: 'portfolio', label: '组合测算', perm: 'menu.portfolio' },
]
const BUDGET_NAV = RISK_NAV.find((x) => x.key === 'budgetHub')?.children || []
const BUDGET_VIEWS = new Set(BUDGET_NAV.map((x) => x.key))
const RISK_VIEWS = new Set([
  ...RISK_NAV.filter((x) => !x.children).map((x) => x.key),
  ...BUDGET_VIEWS,
])
const range = ref('all')
const view = ref('board')
const detailStock = ref(null)
const detailBackView = ref('dailyWatch')
const stockToolSymbol = ref('')
function openStockDetail(row, backView = 'dailyWatch') { detailStock.value = row; detailBackView.value = backView; view.value = 'stockDetail' }
function openScreenDetail(row) { openStockDetail({ ...row, sector: row.sector_zh, current_price: row.price }, 'stockScreener') }
function openEarningsDetail(row) { openStockDetail({ ...row, id: undefined, sector: row.sector_zh || row.sector, current_price: row.price }, 'earningsStocks') }
const DETAIL_BACK_LABEL = { stockScreener: '股票筛选', earningsStocks: '财报股票', dailyWatch: '股票列表' }
const addingDetail = ref(false)
async function addDetailToList() {
  const row = detailStock.value
  if (!row || row.in_list || addingDetail.value) return
  addingDetail.value = true
  try {
    await api.addEarningsStock({ symbol: row.symbol, name: row.name, sector_zh: row.sector_zh || row.sector, earnings_date: row.earnings_date })
    row.in_list = true
    ElMessage.success(`「${row.symbol}」已加入股票列表`)
  } catch (e) {
    if (/已在股票列表/.test(e.message || '')) row.in_list = true
    ElMessage.warning(e.message || '加入失败')
  } finally {
    addingDetail.value = false
  }
}
function openStockTool(tool, symbol) {
  stockToolSymbol.value = symbol
  openParent('stockAnalysis')
  view.value = tool
}
const macroTab = ref('fedRisk')
const pushTab = ref('list')
const riskTab = ref('risk')
const budgetTab = ref('budgetModels')
/** 父菜单展开状态，默认全部 false（收起） */
const navOpen = reactive(Object.fromEntries(PARENT_KEYS.map((k) => [k, false])))
const budgetOpen = ref(false)
/** 从风险组合列表带入测算页：{ id?, name?, legs?, loss_limit?, window?, horizon_days?, notes? } */
const portfolioDraft = ref(null)
const cryptoCustomBtId = ref(null)
const cryptoStrategyBtId = ref(null)
const overview = ref(null)
const trades = ref([])
const loading = ref(true)
const error = ref('')
const modal = ref(null)
const market = ref(null)
const marketLoading = ref(false)
const tape = ref(null)
let tapeTimer

const tradeForm = reactive({
  date: isoDate(),
  symbol: '',
  side: 'LONG',
  pnl_amount: '',
  pnl_pct: '',
  tags: '',
  notes: '',
})

const snapForm = reactive({
  total_amount: '',
})

const params = computed(() => rangeDates(range.value))
const pageTitle = computed(() => {
  if (PLAN_VIEWS.has(view.value)) {
    const sub = PLAN_NAV.find((x) => x.key === view.value)
    return sub ? `计划 · ${sub.label}` : '计划'
  }
  if (view.value === 'macro') {
    const sub = MACRO_NAV.find((x) => x.key === macroTab.value)
    return sub ? `宏观因素 · ${sub.label}` : '宏观因素'
  }
  if (view.value === 'alerts') {
    const sub = PUSH_NAV.find((x) => x.key === pushTab.value)
    return sub ? `推送 · ${sub.label}` : '推送'
  }
  if (RISK_VIEWS.has(view.value)) {
    if (BUDGET_VIEWS.has(view.value)) {
      const sub = BUDGET_NAV.find((x) => x.key === view.value)
      return sub ? `风险 · 预算模型 · ${sub.label}` : '风险 · 预算模型'
    }
    const sub = RISK_NAV.find((x) => x.key === view.value)
    return sub ? `风险 · ${sub.label}` : '风险'
  }
  if (view.value === 'strategy' || view.value === 'pairs' || view.value === 'intraday') {
    const sub = STRAT_NAV.find((x) => x.key === view.value)
    return sub ? `策略 · ${sub.label}` : '策略'
  }
  if (IND_VIEWS.has(view.value)) {
    const sub = IND_NAV.find((x) => x.key === view.value)
    return sub ? `指标 · ${sub.label}` : '指标'
  }
  if (STOCK_VIEWS.has(view.value)) {
    if (view.value === 'stockDetail') return `股票分析 · ${DETAIL_BACK_LABEL[detailBackView.value] || '股票列表'} · ${detailStock.value?.symbol || ''} 详情`
    if (view.value === 'dailyWatchAnalysis') return '股票分析 · 股票列表 · 分析'
    const sub = STOCK_NAV.find((x) => x.key === view.value)
    return sub ? `股票分析 · ${sub.label}` : '股票分析'
  }
  if (CRYPTO_VIEWS.has(view.value)) {
    if (view.value === 'cryptoCustomBacktest') return '虚拟币分析 · 自定义策略 · 回测'
    if (view.value === 'cryptoStrategyBacktest') return '虚拟币分析 · 指标策略 · 回测'
    const sub = CRYPTO_NAV.find((x) => x.key === view.value)
    return sub ? `虚拟币分析 · ${sub.label}` : '虚拟币分析'
  }
  if (view.value === 'users') return '用户管理'
  return NAV.find((x) => x.key === view.value)?.label || '看板'
})

const loggedIn = authState.isLoggedIn
const currentUser = authState.user

const visibleNav = computed(() => NAV.filter((item) => can(item.perm)))
const visiblePlanNav = computed(() => PLAN_NAV.filter((item) => can(item.perm)))
const visibleMacroNav = computed(() => MACRO_NAV.filter((item) => can(item.perm)))
const visiblePushNav = computed(() => PUSH_NAV.filter((item) => can(item.perm)))
const visibleStratNav = computed(() => STRAT_NAV.filter((item) => can(item.perm)))
const visibleIndNav = computed(() => IND_NAV.filter((item) => can(item.perm)))
const visibleStockNav = computed(() => STOCK_NAV.filter((item) => !item.hidden && can(item.perm)))
const visibleCryptoNav = computed(() =>
  CRYPTO_NAV.filter((item) => {
    if (item.key === 'cryptoCustomStrategy') {
      return can(item.perm) || can('menu.cryptoStrategy')
    }
    return can(item.perm)
  }),
)
const visibleRiskNav = computed(() =>
  RISK_NAV.map((sub) => {
    if (sub.children) {
      const children = sub.children.filter((c) => can(c.perm))
      if (!children.length && !can(sub.perm)) return null
      return { ...sub, children }
    }
    return can(sub.perm) ? sub : null
  }).filter(Boolean),
)

function hasChildren(key) {
  return PARENT_KEYS.includes(key)
}

function isParentActive(key) {
  if (key === 'plans') return PLAN_VIEWS.has(view.value)
  if (key === 'riskHub') return RISK_VIEWS.has(view.value)
  if (key === 'macro') return view.value === 'macro'
  if (key === 'alerts') return view.value === 'alerts'
  if (key === 'strategy') return view.value === 'strategy' || view.value === 'pairs' || view.value === 'intraday'
  if (key === 'indicators') return IND_VIEWS.has(view.value)
  if (key === 'stockAnalysis') return STOCK_VIEWS.has(view.value)
  if (key === 'cryptoAnalysis') return CRYPTO_VIEWS.has(view.value)
  return view.value === key
}

function toggleNav(key) {
  if (!hasChildren(key)) return
  navOpen[key] = !navOpen[key]
}

function openParent(key) {
  if (hasChildren(key)) navOpen[key] = true
}

function firstAllowedView() {
  const order = [
    'board', 'dayPlan', 'positions', 'risk', 'macro',
    'indicatorList', 'fundamentals', 'cryptoMarket', 'strategy', 'alerts', 'users',
  ]
  for (const key of order) {
    if (PLAN_VIEWS.has(key)) {
      const item = PLAN_NAV.find((n) => n.key === key)
      if (item && can(item.perm)) return key
      continue
    }
    if (key === 'indicatorList' && can('menu.indicatorList')) return 'indicatorList'
    if (key === 'fundamentals' && can('menu.fundamentals')) return 'fundamentals'
    if (key === 'cryptoMarket' && can('menu.cryptoMarket')) return 'cryptoMarket'
    const item = NAV.find((n) => n.key === key)
    if (item && can(item.perm)) {
      if (key === 'riskHub') return 'risk'
      if (key === 'indicators') return 'indicatorList'
      if (key === 'stockAnalysis') {
        const first = STOCK_NAV.find((n) => !n.hidden && can(n.perm))
        return first?.key || 'fundamentals'
      }
      if (key === 'cryptoAnalysis') {
        const first = CRYPTO_NAV.find((n) => can(n.perm))
        return first?.key || 'cryptoMarket'
      }
      return key
    }
  }
  if (can('menu.dayPlan')) return 'dayPlan'
  if (can('menu.risk')) return 'risk'
  return 'board'
}

function ensureViewAllowed() {
  const map = {
    board: 'menu.board',
    dayPlan: 'menu.dayPlan',
    weekPlan: 'menu.weekPlan',
    monthPlan: 'menu.monthPlan',
    positions: 'menu.positions',
    news: 'menu.news',
    fundamentals: 'menu.fundamentals',
    risk: 'menu.risk',
    riskPortfolios: 'menu.riskPortfolios',
    budgetModels: 'menu.budgetModels',
    kakeyaModel: 'menu.kakeyaModel',
    portfolio: 'menu.portfolio',
    macro: 'menu.macro',
    indicatorList: 'menu.indicatorList',
    indicatorVerify: 'menu.indicatorVerify',
    options: 'menu.options',
    regression: 'menu.regression',
    dailyWatch: 'menu.dailyWatch',
    dailyWatchAnalysis: 'menu.dailyWatch',
    stockDetail: 'menu.dailyWatch',
    stockJournal: 'menu.stockJournal',
    fearVix: 'menu.fearVix',
    earningsStocks: 'menu.earningsStocks',
    stockScreener: 'menu.stockScreener',
    cryptoAnalysis: 'menu.cryptoMarket',
    cryptoMarket: 'menu.cryptoMarket',
    cryptoNews: 'menu.cryptoNews',
    cryptoStrategy: 'menu.cryptoStrategy',
    cryptoStrategyBacktest: 'menu.cryptoStrategy',
    cryptoCustomStrategy: 'menu.cryptoCustomStrategy',
    cryptoCustomBacktest: 'menu.cryptoCustomStrategy',
    strategy: 'menu.strategy',
    pairs: 'menu.pairs',
    intraday: 'menu.intraday',
    alerts: 'menu.alerts',
    live: 'menu.live',
    users: 'menu.users',
  }
  // 旧 view 兼容
  if (view.value === 'indicators') view.value = 'indicatorList'
  if (view.value === 'earningsStocks') view.value = 'stockScreener'
  if (
    view.value === 'cryptoAnalysis' ||
    view.value === 'cryptoTech' ||
    view.value === 'cryptoOnchain' ||
    view.value === 'cryptoSentiment'
  ) {
    view.value = 'cryptoMarket'
  }
  const need = view.value === 'stockDetail' && detailBackView.value !== 'dailyWatch'
    ? map[detailBackView.value]
    : map[view.value]
  if (need && !can(need)) {
    if (
      (view.value === 'cryptoCustomStrategy' || view.value === 'cryptoCustomBacktest') &&
      can('menu.cryptoStrategy')
    ) {
      /* 旧权限兼容：有指标策略就能进自定义策略 */
    } else {
      view.value = firstAllowedView()
    }
  }
}

async function onLoginOk() {
  ensureViewAllowed()
  await load()
  loadTape()
  loadMarket()
}

function logout() {
  clearSession()
  overview.value = null
  trades.value = []
  view.value = 'board'
}

function onAuthExpired() {
  clearSession()
}

function nextRelease(key) {
  return market.value?.econ_calendar?.next_by_key?.[key] || null
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const [ov, list] = await Promise.all([api.overview(params.value), api.trades(params.value)])
    overview.value = ov
    trades.value = list
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

function resetTrade() {
  Object.assign(tradeForm, {
    date: isoDate(),
    symbol: '',
    side: 'LONG',
    pnl_amount: '',
    pnl_pct: '',
    tags: '',
    notes: '',
  })
}

function resetSnap() {
  const cur =
    currentUser.value?.total_amount ??
    overview.value?.goal?.total_amount ??
    overview.value?.cards?.total_equity ??
    ''
  Object.assign(snapForm, {
    total_amount: cur === '' || cur == null ? '' : Number(cur),
  })
}

async function submitTrade() {
  error.value = ''
  try {
    await api.createTrade({
      date: tradeForm.date,
      symbol: tradeForm.symbol,
      side: tradeForm.side,
      pnl_amount: Number(tradeForm.pnl_amount),
      pnl_pct: tradeForm.pnl_pct === '' ? null : Number(tradeForm.pnl_pct) / 100,
      tags: tradeForm.tags.split(/[,，]/).map((s) => s.trim()).filter(Boolean),
      notes: tradeForm.notes || null,
    })
    modal.value = null
    resetTrade()
    await load()
  } catch (e) {
    error.value = e.message
  }
}

async function submitSnap() {
  error.value = ''
  const amount = Number(snapForm.total_amount)
  if (!Number.isFinite(amount) || amount < 0) {
    error.value = '请填写有效的总金额'
    return
  }
  try {
    const goal = await api.updateSettings({ total_amount: amount })
    patchCurrentUser({ total_amount: Number(goal?.total_amount ?? amount) })
    modal.value = null
    resetSnap()
    await load()
  } catch (e) {
    error.value = e.message
  }
}

async function removeTrade(id) {
  if (!confirm('删除这笔交易？')) return
  await api.deleteTrade(id)
  await load()
}

async function saveGoal(payload) {
  error.value = ''
  try {
    const goal = await api.updateSettings(payload)
    if (goal?.total_amount != null) {
      patchCurrentUser({ total_amount: Number(goal.total_amount) })
    } else if (payload.total_amount != null) {
      patchCurrentUser({ total_amount: Number(payload.total_amount) })
    }
    await load()
  } catch (e) {
    error.value = e.message
  }
}

async function loadMarket() {
  marketLoading.value = true
  try {
    market.value = await api.market()
  } catch {
    market.value = market.value || {
      fomc: { upcoming: [] },
      econ_calendar: { months: [], next: [], next_by_key: {} },
      fed_releases: [],
      fed_speeches: [],
      news: [],
    }
  } finally {
    marketLoading.value = false
  }
}

async function loadTape() {
  try {
    tape.value = await api.tape()
  } catch {
    tape.value = tape.value || { indices: [], watchlist: [] }
  }
  clearQuoteTimer(tapeTimer)
  tapeTimer = armQuoteTimer(loadTape, tape.value?.market, { liveMs: 60_000 })
}

function switchView(key) {
  if (PLAN_VIEWS.has(key)) {
    openParent('plans')
    view.value = key
    return
  }
  if (MACRO_NAV.some((item) => item.key === key)) {
    openParent('macro')
    view.value = 'macro'
    macroTab.value = key
    return
  }
  if (RISK_VIEWS.has(key)) {
    openParent('riskHub')
    if (BUDGET_VIEWS.has(key)) budgetOpen.value = true
    view.value = key
    riskTab.value = key
    if (BUDGET_VIEWS.has(key)) budgetTab.value = key
    return
  }
  view.value = key
  if (key === 'news') loadMarket()
}

function openStockTab(key) {
  openParent('stockAnalysis')
  if (['fundamentals', 'options', 'regression'].includes(key)) stockToolSymbol.value = ''
  view.value = key
  if (key === 'news') loadMarket()
}

function onParentClick(key) {
  toggleNav(key)
  if (!navOpen[key]) return
  if (key === 'macro') openMacro()
  else if (key === 'strategy') openStrategy()
  else if (key === 'alerts') openPush()
  else if (key === 'riskHub') openRisk()
  else if (key === 'indicators') {
    const first = visibleIndNav.value[0]
    view.value = first?.key || 'indicatorList'
  }
  else if (key === 'stockAnalysis') {
    const first = visibleStockNav.value[0]
    if (first) view.value = first.key
  }
  else if (key === 'cryptoAnalysis') {
    const first = visibleCryptoNav.value[0]
    if (first) view.value = first.key
  }
  else if (key === 'plans') {
    const first = visiblePlanNav.value[0]
    if (first) view.value = first.key
  }
}

function openMacro() {
  openParent('macro')
  view.value = 'macro'
}

function openPush() {
  openParent('alerts')
  view.value = 'alerts'
  pushTab.value = 'list'
}

function openPushTab(key) {
  openParent('alerts')
  view.value = 'alerts'
  pushTab.value = key
}

function openRisk() {
  openParent('riskHub')
  const key = RISK_VIEWS.has(riskTab.value) ? riskTab.value : 'risk'
  view.value = key
  riskTab.value = key
}

function openRiskTab(key) {
  openParent('riskHub')
  if (key === 'budgetHub') {
    budgetOpen.value = !budgetOpen.value
    if (!budgetOpen.value) return
    const b = BUDGET_VIEWS.has(budgetTab.value) ? budgetTab.value : 'budgetModels'
    view.value = b
    riskTab.value = b
    budgetTab.value = b
    return
  }
  view.value = key
  riskTab.value = key
}

function openBudgetTab(key) {
  openParent('riskHub')
  budgetOpen.value = true
  view.value = key
  riskTab.value = key
  budgetTab.value = key
}

function openStrategy() {
  openParent('strategy')
  view.value = 'strategy'
}

function openPlanTab(key) {
  openParent('plans')
  view.value = key
}

function openMacroTab(key) {
  openParent('macro')
  view.value = 'macro'
  macroTab.value = key
}

function onBoardCalOpen(key) {
  if (key === 'dailyWatch') {
    openParent('stockAnalysis')
    view.value = 'dailyWatch'
    return
  }
  openMacroTab(key)
}

function openCryptoCustomBacktest(id) {
  cryptoCustomBtId.value = id
  view.value = 'cryptoCustomBacktest'
}

function openCryptoStrategyBacktest(id) {
  cryptoStrategyBtId.value = id
  view.value = 'cryptoStrategyBacktest'
}

function openRiskPortfolioNew() {
  portfolioDraft.value = {
    id: null,
    name: '',
    notes: '',
    legs: [
      { symbol: 'AAPL', weight: '40', amount: '', side: 'long', entry_price: '' },
      { symbol: 'TLT', weight: '40', amount: '', side: 'long', entry_price: '' },
      { symbol: 'GLD', weight: '20', amount: '', side: 'long', entry_price: '' },
    ],
    loss_limit: 0.05,
    window: 252,
    horizon_days: 21,
    budget_capital: 10000,
    entry_date: '',
    budget_days: 30,
  }
  view.value = 'portfolio'
  riskTab.value = 'portfolio'
}

function openRiskPortfolioEdit(row) {
  portfolioDraft.value = {
    id: row.id,
    name: row.name || '',
    notes: row.notes || '',
    legs: (row.legs || []).map((l) => ({
      symbol: l.symbol,
      weight: l.weight != null && l.weight !== '' ? String(l.weight) : '',
      amount: l.amount != null && l.amount !== '' ? String(l.amount) : '',
      side: l.side === 'short' ? 'short' : 'long',
      entry_price: l.entry_price != null && l.entry_price !== '' ? String(l.entry_price) : '',
    })),
    loss_limit: row.loss_limit ?? 0.05,
    window: row.window || 252,
    horizon_days: row.horizon_days || 21,
    budget_capital: row.budget_capital,
    entry_date: row.entry_date || row.budget_start_date || '',
    budget_days: row.budget_days || 30,
    budget_start_date: row.budget_start_date,
  }
  view.value = 'portfolio'
  riskTab.value = 'portfolio'
}

function onPortfolioSaved() {
  portfolioDraft.value = null
  view.value = 'riskPortfolios'
  riskTab.value = 'riskPortfolios'
}

function onPortfolioBackToList() {
  portfolioDraft.value = null
  view.value = 'riskPortfolios'
  riskTab.value = 'riskPortfolios'
}

onMounted(async () => {
  window.addEventListener('pnl-auth-expired', onAuthExpired)
  if (!loggedIn.value) return
  try {
    const me = await api.me()
    setSession(authState.token.value, me)
  } catch {
    clearSession()
    return
  }
  ensureViewAllowed()
  load()
  loadTape()
  loadMarket()
})

onUnmounted(() => {
  window.removeEventListener('pnl-auth-expired', onAuthExpired)
  clearQuoteTimer(tapeTimer)
})
</script>

<template>
  <LoginPage v-if="!loggedIn" @ok="onLoginOk" />
  <div v-else class="shell">
    <aside class="sidebar">
      <div class="brand">
        <h1>P&L Board</h1>
        <p>周线复盘 · 资金曲线 · 财富自由</p>
      </div>
      <nav class="side-nav">
        <template v-for="item in visibleNav" :key="item.key">
          <button
            class="side-link"
            :class="{
              active: isParentActive(item.key),
              'side-link-parent': hasChildren(item.key),
              open: hasChildren(item.key) && navOpen[item.key],
            }"
            @click="
              hasChildren(item.key)
                ? onParentClick(item.key)
                : switchView(item.key)
            "
          >
            <span>{{ item.label }}</span>
            <span v-if="hasChildren(item.key)" class="side-caret">{{ navOpen[item.key] ? '▾' : '▸' }}</span>
          </button>
          <div v-if="item.key === 'plans' && navOpen.plans" class="side-sub">
            <button
              v-for="sub in visiblePlanNav"
              :key="sub.key"
              class="side-link side-link-sub"
              :class="{ active: view === sub.key }"
              @click="openPlanTab(sub.key)"
            >
              {{ sub.label }}
            </button>
          </div>
          <div v-else-if="item.key === 'macro' && navOpen.macro" class="side-sub">
            <button
              v-for="sub in visibleMacroNav"
              :key="sub.key"
              class="side-link side-link-sub"
              :class="{ active: view === 'macro' && macroTab === sub.key }"
              @click="openMacroTab(sub.key)"
            >
              <span>{{ sub.label }}</span>
              <small v-if="!['fedRisk', 'eventStudy', 'treasury'].includes(sub.key) && nextRelease(sub.key)">
                {{ nextRelease(sub.key).label }} {{ nextRelease(sub.key).time_et }}
              </small>
            </button>
          </div>
          <div v-else-if="item.key === 'riskHub' && navOpen.riskHub" class="side-sub">
            <template v-for="sub in visibleRiskNav" :key="sub.key">
              <button
                class="side-link side-link-sub"
                :class="{
                  active: sub.children
                    ? BUDGET_VIEWS.has(view)
                    : view === sub.key,
                  'side-link-parent': !!sub.children,
                  open: !!sub.children && budgetOpen,
                }"
                @click="openRiskTab(sub.key)"
              >
                <span>{{ sub.label }}</span>
                <span v-if="sub.children" class="side-caret">{{ budgetOpen ? '▾' : '▸' }}</span>
              </button>
              <div v-if="sub.children && budgetOpen" class="side-sub side-sub-nested">
                <button
                  v-for="child in sub.children"
                  :key="child.key"
                  class="side-link side-link-sub"
                  :class="{ active: view === child.key }"
                  @click="openBudgetTab(child.key)"
                >
                  {{ child.label }}
                </button>
              </div>
            </template>
          </div>
          <div v-else-if="item.key === 'indicators' && navOpen.indicators" class="side-sub">
            <button
              v-for="sub in visibleIndNav"
              :key="sub.key"
              class="side-link side-link-sub"
              :class="{ active: view === sub.key }"
              @click="openParent('indicators'); view = sub.key"
            >
              {{ sub.label }}
            </button>
          </div>
          <div v-else-if="item.key === 'stockAnalysis' && navOpen.stockAnalysis" class="side-sub">
            <button
              v-for="sub in visibleStockNav"
              :key="sub.key"
              class="side-link side-link-sub"
              :class="{ active: view === sub.key || (sub.key === 'dailyWatch' && (view === 'dailyWatchAnalysis' || (view === 'stockDetail' && detailBackView === 'dailyWatch'))) || (view === 'stockDetail' && detailBackView === sub.key) }"
              @click="openStockTab(sub.key)"
            >
              {{ sub.label }}
            </button>
          </div>
          <div v-else-if="item.key === 'cryptoAnalysis' && navOpen.cryptoAnalysis" class="side-sub">
            <button
              v-for="sub in visibleCryptoNav"
              :key="sub.key"
              class="side-link side-link-sub"
              :class="{ active: view === sub.key }"
              @click="openParent('cryptoAnalysis'); view = sub.key"
            >
              {{ sub.label }}
            </button>
          </div>
          <div v-else-if="item.key === 'strategy' && navOpen.strategy" class="side-sub">
            <button
              v-for="sub in visibleStratNav"
              :key="sub.key"
              class="side-link side-link-sub"
              :class="{ active: view === sub.key }"
              @click="openParent('strategy'); view = sub.key"
            >
              {{ sub.label }}
            </button>
          </div>
          <div v-else-if="item.key === 'alerts' && navOpen.alerts" class="side-sub">
            <button
              v-for="sub in visiblePushNav"
              :key="sub.key"
              class="side-link side-link-sub"
              :class="{ active: view === 'alerts' && pushTab === sub.key }"
              @click="openPushTab(sub.key)"
            >
              {{ sub.label }}
            </button>
          </div>
        </template>
      </nav>
      <div class="side-user">
        <div class="sub">{{ currentUser?.username }} · {{ currentUser?.role }}</div>
        <div class="sub" v-if="currentUser?.total_amount != null">
          总金额 {{ Number(currentUser.total_amount || 0).toLocaleString() }}
        </div>
        <el-button size="small" style="margin-top: 8px; width: 100%" @click="logout">退出</el-button>
      </div>
    </aside>
    <div class="page" :class="{ 'page-macro': view === 'macro' }">
    <header class="topbar">
      <div class="page-title">{{ pageTitle }}</div>
      <div class="top-actions" v-if="view === 'board'">
        <button v-if="can('btn.board.snap')" class="btn" @click="resetSnap(); modal = 'snap'">修改总金额</button>
        <button v-if="can('btn.board.trade')" class="btn btn-primary" @click="resetTrade(); modal = 'trade'">记一笔交易</button>
      </div>
    </header>

    <KeepAlive>
      <StockScreenerPage v-if="view === 'stockScreener'" @open-detail="openScreenDetail" />
    </KeepAlive>
    <KeepAlive>
      <EarningsStocksPage v-if="view === 'earningsStocks'" @open-detail="openEarningsDetail" />
    </KeepAlive>
    <UsersPage v-if="view === 'users'" />
    <PlansPage v-else-if="view === 'dayPlan'" kind="day" />
    <PlansPage v-else-if="view === 'weekPlan'" kind="week" />
    <PlansPage v-else-if="view === 'monthPlan'" kind="month" />
    <PositionsPage v-else-if="view === 'positions'" @changed="load" />
    <NewsPage v-else-if="view === 'news'" :market="market" :loading="marketLoading" />
    <FundamentalsPage v-else-if="view === 'fundamentals'" :initial-symbol="stockToolSymbol" />
    <RiskModelPage v-else-if="view === 'risk'" />
    <RiskPortfoliosPage
      v-else-if="view === 'riskPortfolios'"
      @create="openRiskPortfolioNew"
      @edit="openRiskPortfolioEdit"
    />
    <BudgetModelPage v-else-if="view === 'budgetModels'" />
    <KakeyaModelPage v-else-if="view === 'kakeyaModel'" />
    <PortfolioPage
      v-else-if="view === 'portfolio'"
      :draft="portfolioDraft"
      @saved="onPortfolioSaved"
      @back-list="onPortfolioBackToList"
    />
    <MacroRiskPage v-else-if="view === 'macro' && macroTab === 'fedRisk'" />
    <MacroEventStudyPage v-else-if="view === 'macro' && macroTab === 'eventStudy'" />
    <MacroPage v-else-if="view === 'macro'" :tab="macroTab" />
    <StrategyPage v-else-if="view === 'indicatorList'" />
    <IndicatorVerifyPage v-else-if="view === 'indicatorVerify'" />
    <CryptoAnalysisPage v-else-if="view === 'cryptoMarket'" />
    <CryptoNewsPage v-else-if="view === 'cryptoNews'" />
    <CryptoStrategyPage
      v-else-if="view === 'cryptoStrategy'"
      @open-backtest="openCryptoStrategyBacktest"
    />
    <CryptoStrategyBacktestPage
      v-else-if="view === 'cryptoStrategyBacktest'"
      :strategy-id="cryptoStrategyBtId"
      @back="view = 'cryptoStrategy'"
    />
    <CryptoCustomStrategyPage
      v-else-if="view === 'cryptoCustomStrategy'"
      @open-backtest="openCryptoCustomBacktest"
    />
    <CryptoCustomBacktestPage
      v-else-if="view === 'cryptoCustomBacktest'"
      :strategy-id="cryptoCustomBtId"
      @back="view = 'cryptoCustomStrategy'"
    />
    <IntradayStockPage v-else-if="view === 'intradayStock'" />
    <NewStrategyPage v-else-if="view === 'newStrategy'" />
    <StockWatchPage v-else-if="view === 'stockWatch'" />
    <OptionsPage v-else-if="view === 'options'" :initial-symbol="stockToolSymbol" />
    <RegressionScreenerPage v-else-if="view === 'regression'" :initial-symbol="stockToolSymbol" />
    <DailyWatchPage v-else-if="view === 'dailyWatch' || view === 'live'" @open-analysis="view = 'dailyWatchAnalysis'" @open-detail="openStockDetail" />
    <StockDetailPage v-else-if="view === 'stockDetail' && detailStock" :stock="detailStock" @back="view = detailBackView" @more-financials="symbol => openStockTool('fundamentals', symbol)">
      <template v-if="['earningsStocks', 'stockScreener'].includes(detailBackView) && can('btn.daily_watch.write')" #actions>
        <button
          class="btn icon-btn"
          :class="{ added: detailStock.in_list }"
          type="button"
          :disabled="detailStock.in_list || addingDetail"
          :title="detailStock.in_list ? '已在股票列表' : '加入股票列表'"
          aria-label="加入股票列表"
          @click="addDetailToList"
        >
          <svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
            <path v-if="detailStock.in_list" d="M5 12l5 5L20 7" />
            <path v-else d="M4 6h11M4 12h11M4 18h7M18 15v6M15 18h6" />
          </svg>
        </button>
      </template>
    </StockDetailPage>
    <DailyWatchAnalysisPage v-else-if="view === 'dailyWatchAnalysis'" @back="view = 'dailyWatch'" />
    <StockJournalPage v-else-if="view === 'stockJournal'" />
    <FearVixStudyPage v-else-if="view === 'fearVix'" />
    <ArbStrategyPage v-else-if="view === 'strategy'" />
    <PairScanPage v-else-if="view === 'pairs'" />
    <IntradayScanPage v-else-if="view === 'intraday'" />
    <AlertsPage v-else-if="view === 'alerts' && pushTab === 'list'" />
    <RecipientsPage v-else-if="view === 'alerts' && pushTab === 'people'" />

    <template v-else-if="view === 'board'">
      <div class="range-row">
        <div class="pills">
          <button
            v-for="opt in RANGE_OPTIONS"
            :key="opt.key"
            class="pill"
            :class="{ active: range === opt.key }"
            @click="range = opt.key; load()"
          >
            {{ opt.label }}
          </button>
        </div>
        <div class="sub" v-if="loading">加载中…</div>
        <div class="error" v-else-if="error && !modal">{{ error }}</div>
      </div>

      <template v-if="overview">
        <TapeBar :tape="tape" />
        <StatCards :cards="overview.cards" />
        <BoardCalendar @open="onBoardCalOpen" />
        <GoalPanel v-if="overview.goal" :goal="overview.goal" @save="saveGoal" />

        <div class="grid-2">
          <section class="panel">
            <h2>累计资金曲线</h2>
            <EquityChart :curve="overview.equity_curve" />
          </section>
          <section class="panel">
            <h2>每周盈亏</h2>
            <DailyBarChart :curve="overview.equity_curve" />
          </section>
        </div>

        <section class="panel" style="margin-bottom: 12px">
          <h2>周盈亏热力图（按记账日，建议填周五收盘）</h2>
          <CalendarHeatmap :calendar="overview.calendar" />
        </section>

        <div class="grid-3">
          <section class="panel">
            <h2>复盘统计</h2>
            <ReviewPanel :review="overview.review" />
          </section>
          <section class="panel">
            <h2>月份效应</h2>
            <MonthChart :monthly="overview.monthly" />
          </section>
          <section class="panel">
            <h2>策略标签盈亏</h2>
            <TagChart :tags="overview.tags" />
          </section>
        </div>

        <section class="panel">
          <h2>交易日志</h2>
          <TradeTable :trades="trades" @delete="removeTrade" />
        </section>
      </template>

      <div v-if="modal" class="modal-mask" @click.self="modal = null">
        <div class="modal" v-if="modal === 'trade'">
          <h3>记一笔交易</h3>
          <div class="form-grid">
            <div class="field">
              <label>日期（开仓/平仓日）</label>
              <input v-model="tradeForm.date" type="date" />
            </div>
            <div class="field">
              <label>股票代码</label>
              <input v-model="tradeForm.symbol" placeholder="AAPL" />
            </div>
            <div class="field">
              <label>方向</label>
              <select v-model="tradeForm.side">
                <option value="LONG">LONG</option>
                <option value="SHORT">SHORT</option>
              </select>
            </div>
            <div class="field">
              <label>盈亏金额 ($)</label>
              <input v-model="tradeForm.pnl_amount" type="number" step="0.01" />
            </div>
            <div class="field">
              <label>收益率 %（可选）</label>
              <input v-model="tradeForm.pnl_pct" type="number" step="0.01" placeholder="1.5" />
            </div>
            <div class="field">
              <label>标签（逗号分隔）</label>
              <input v-model="tradeForm.tags" placeholder="周线突破,持仓,按计划止损" />
            </div>
            <div class="field full">
              <label>复盘备注</label>
              <textarea v-model="tradeForm.notes" placeholder="周线结构、为什么进、持有还是止损" />
            </div>
          </div>
          <div class="error" v-if="error">{{ error }}</div>
          <div class="modal-actions">
            <button class="btn btn-ghost" @click="modal = null">取消</button>
            <button class="btn btn-primary" @click="submitTrade">保存</button>
          </div>
        </div>

        <div class="modal" v-if="modal === 'snap'">
          <h3>修改总金额</h3>
          <div class="form-grid">
            <div class="field full">
              <label>总金额 ($)</label>
              <input v-model="snapForm.total_amount" type="number" step="0.01" min="0" />
            </div>
          </div>
          <p class="sub" style="margin-top: 8px">保存后侧栏总金额与看板账户资产会同步更新。</p>
          <div class="error" v-if="error">{{ error }}</div>
          <div class="modal-actions">
            <button class="btn btn-ghost" @click="modal = null">取消</button>
            <button class="btn btn-primary" @click="submitSnap">保存</button>
          </div>
        </div>
      </div>
    </template>
    </div>
  </div>
</template>
