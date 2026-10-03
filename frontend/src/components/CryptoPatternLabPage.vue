<script setup>
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import * as echarts from 'echarts'
import { ElMessage } from 'element-plus'
import { RefreshRight, ZoomIn, ZoomOut } from '@element-plus/icons-vue'
import { api } from '../api'

const symbol = ref('OPUSDT')
const interval = ref('1d')
const mode = ref('flexible')
const selection = ref([0, 0])
const horizon = ref(3)
const includeVolume = ref(true)
const includeIndicators = ref(true)
const loading = ref(false)
const analyzed = ref(true)
const runStatus = ref('')
const priceEl = ref(null)
const probabilityEl = ref(null)
const bars = ref([])
const coinRows = ref([])
const matches = ref([])
const rawCandidates = ref(0)
const chartZoom = ref([76, 100])
const samplePools = ref([])
const sampleLoading = ref(false)
const analysisDirty = ref(true)
const studies = ref([])
const paperTrades = ref([])
const savedStudyId = ref(null)
const paperNotional = ref(1000)
const paperLeverage = ref(2)
const paperSide = ref('long')
const spotSellPrice = ref(0)
const leverageTargetPrice = ref(0)
const spotAction = ref('buy')
const spotInputMode = ref('amount')
const spotQuantityInput = ref(0)
const spotSellQuantity = ref(0)
const autoSellEnabled = ref(false)
const paperTotalAmount = ref(0)
const serverAvailableUsdt = ref(0)
const matchProgress = ref(0)
const matchPhase = ref('')
const studySaving = ref(false)
const orderSubmitting = ref(false)
const closingTradeId = ref(null)
const marketTransport = ref('NAS 后端')
const newsAnalysis = ref(null)

const SAMPLE_SYMBOLS = ['OPUSDT', 'BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'XRPUSDT']
const BINANCE_SPOT_URLS = ['https://api.binance.com', 'https://data-api.binance.vision']

let priceChart = null
let probabilityChart = null

const symbols = computed(() => coinRows.value.map((row) => row.binance_symbol || `${row.symbol}USDT`))
const intervals = [
  { value: '1m', label: '1分钟' },
  { value: '15m', label: '15分钟' },
  { value: '1h', label: '1小时' },
  { value: '4h', label: '4小时' },
  { value: '1d', label: '1天' },
  { value: '1w', label: '1周' },
  { value: '1M', label: '1个月' },
]
const intervalLabel = computed(() => intervals.find((x) => x.value === interval.value)?.label || interval.value)
const horizonLabel = computed(() => `${horizon.value} 根 = ${horizon.value} × ${intervalLabel.value}`)
const paperEntryPrice = computed(() => Number(bars.value.at(-1)?.close || 0))
const spotQuantity = computed(() => spotInputMode.value === 'quantity'
  ? Number(spotQuantityInput.value || 0)
  : paperEntryPrice.value > 0 ? Number(paperNotional.value) / paperEntryPrice.value : 0)
const spotNotional = computed(() => spotInputMode.value === 'quantity'
  ? spotQuantity.value * paperEntryPrice.value
  : Number(paperNotional.value || 0))
const openSpotTrades = computed(() => paperTrades.value.filter((item) => item.trade_type !== 'leverage' && item.status === 'open' && Number(item.remaining_quantity ?? item.quantity) > 0))
const spotHoldingQty = computed(() => openSpotTrades.value.reduce((sum, item) => sum + Number(item.remaining_quantity ?? item.quantity), 0))
const spotAverageCost = computed(() => {
  const cost = openSpotTrades.value.reduce((sum, item) => sum + Number(item.entry_price) * Number(item.remaining_quantity ?? item.quantity), 0)
  return spotHoldingQty.value ? cost / spotHoldingQty.value : 0
})
const paperAvailableUsdt = computed(() => {
  return Math.max(0, Number(serverAvailableUsdt.value || 0))
})
const spotFee = computed(() => (spotAction.value === 'buy' ? spotNotional.value : Number(spotSellQuantity.value) * paperEntryPrice.value) * 0.001)
const spotPotentialProfit = computed(() => Math.max(0, (Number(spotSellPrice.value) - paperEntryPrice.value) * spotQuantity.value))

const selectedCount = computed(() => selection.value[1] - selection.value[0] + 1)
const selectedStart = computed(() => bars.value[selection.value[0]]?.date || '—')
const selectedEnd = computed(() => bars.value[selection.value[1]]?.date || '—')
const sampleCount = computed(() => matches.value.length)
const upProbability = computed(() => sampleCount.value ? (matches.value.filter((x) => x.outcome > 1).length / sampleCount.value * 100).toFixed(1) : '—')
const flatProbability = computed(() => sampleCount.value ? (matches.value.filter((x) => Math.abs(x.outcome) <= 1).length / sampleCount.value * 100).toFixed(1) : '—')
const downProbability = computed(() => sampleCount.value ? (matches.value.filter((x) => x.outcome < -1).length / sampleCount.value * 100).toFixed(1) : '—')
const weightedNews = computed(() => {
  const items = newsAnalysis.value?.items || []
  const now = Date.now() / 1000
  let weightSum = 0
  let scoreSum = 0
  for (const item of items) {
    const ageHours = Math.max(0, (now - Number(item.ts || now)) / 3600)
    const recency = Math.exp(-ageHours / 72)
    const eventWeight = (item.tags || []).some((tag) => tag !== '一般新闻') ? 1.5 : 1
    const weight = recency * eventWeight
    scoreSum += Number(item.sentiment?.score || 0) * weight
    weightSum += weight
  }
  const average = weightSum ? scoreSum / weightSum : 0
  return {
    score: clampScore(50 + average * 38),
    average,
    sampleSize: items.length,
    bullish: items.filter((item) => item.sentiment?.level === 'bullish').length,
    bearish: items.filter((item) => item.sentiment?.level === 'bearish').length,
    events: items.filter((item) => (item.tags || []).some((tag) => tag !== '一般新闻')).length,
    evidence: [...items]
      .sort((a, b) => {
        const impactA = Math.abs(Number(a.sentiment?.score || 0)) + ((a.tags || []).some((tag) => tag !== '一般新闻') ? 1 : 0)
        const impactB = Math.abs(Number(b.sentiment?.score || 0)) + ((b.tags || []).some((tag) => tag !== '一般新闻') ? 1 : 0)
        return impactB - impactA || Number(b.ts || 0) - Number(a.ts || 0)
      })
      .slice(0, 3),
  }
})
const factorBreakdown = computed(() => {
  if (!sampleCount.value) return []
  const selected = bars.value.slice(selection.value[0], selection.value[1] + 1)
  const closes = selected.map((row) => Number(row.close)).filter(Number.isFinite)
  const currentReturn = seriesReturn(selected)
  const up = Number(upProbability.value)
  const down = Number(downProbability.value)
  const median = quantile(matches.value.map((item) => item.outcome), 0.5)
  const historyScore = clampScore(50 + (up - down) * 0.42 + Math.tanh(median / 5) * 12)

  const averageClose = mean(closes.slice(-Math.min(20, closes.length))) || closes.at(-1) || 0
  const priceVsAverage = averageClose ? (closes.at(-1) / averageClose - 1) * 100 : 0
  const greenRatio = selected.length
    ? selected.filter((row) => Number(row.close) >= Number(row.open)).length / selected.length
    : 0.5
  const structureScore = clampScore(
    50 + Math.tanh(currentReturn / 8) * 24 + Math.tanh(priceVsAverage / 3) * 16 + (greenRatio - 0.5) * 20,
  )

  const marketReturns = samplePools.value.map((pool) => seriesReturn(pool.bars.slice(-Math.max(2, selected.length))))
  const marketScore = clampScore(mean(marketReturns.map((value) => 50 + Math.tanh(value / 8) * 38)) || 50)

  const split = Math.max(1, Math.floor(selected.length / 2))
  const earlyVolume = mean(selected.slice(0, split).map((row) => Number(row.volume || 0)))
  const recentVolume = mean(selected.slice(split).map((row) => Number(row.volume || 0)))
  const volumeChange = earlyVolume > 0 ? Math.log(Math.max(recentVolume, 1) / earlyVolume) : 0
  const priceDirection = currentReturn === 0 ? 0 : Math.sign(currentReturn)
  const volumeScore = clampScore(50 + priceDirection * Math.tanh(Math.abs(volumeChange)) * 28 + Math.tanh(currentReturn / 10) * 8)

  const outcomes = matches.value.map((item) => Number(item.outcome)).filter(Number.isFinite)
  const lowerTail = quantile(outcomes, 0.1)
  const upperTail = quantile(outcomes, 0.9)
  const riskScore = clampScore(50 + Math.tanh((lowerTail + upperTail) / 10) * 20 + (up - down) * 0.18)

  return [
    { key: 'history', label: '历史概率', weight: 40, score: historyScore, detail: `${sampleCount.value} 个独立案例：涨 ${up.toFixed(1)}% / 跌 ${down.toFixed(1)}%，后续中位收益 ${signed(median)}%` },
    { key: 'structure', label: '价格结构', weight: 20, score: structureScore, detail: `当前切片涨跌 ${signed(currentReturn)}%，收盘相对近期均值 ${signed(priceVsAverage)}%` },
    { key: 'market', label: '市场共振', weight: 15, score: marketScore, detail: `${samplePools.value.map((pool) => pool.symbol).join('、')} 同周期方向共振，承接政策、政治与风险偏好冲击` },
    { key: 'volume', label: '量价心理', weight: 10, score: volumeScore, detail: `成交量前后半段变化 ${signed((Math.exp(volumeChange) - 1) * 100)}%，反映追涨、恐慌与控盘行为` },
    { key: 'news', label: '事件新闻', weight: 10, score: weightedNews.value.score, detail: `系统分析 ${weightedNews.value.sampleSize} 条，其中重大事件 ${weightedNews.value.events} 条、偏多 ${weightedNews.value.bullish} 条、偏空 ${weightedNews.value.bearish} 条；按时效和事件等级加权` },
    { key: 'risk', label: '波动风险', weight: 5, score: riskScore, detail: `历史后续 10% / 90% 分位为 ${signed(lowerTail)}% / ${signed(upperTail)}%，衡量尾部损失与上行空间` },
  ]
})
const compositeScore = computed(() => {
  if (!factorBreakdown.value.length) return '—'
  const totalWeight = factorBreakdown.value.reduce((sum, item) => sum + item.weight, 0)
  const weighted = factorBreakdown.value.reduce((sum, item) => sum + item.score * item.weight, 0)
  return Math.round(weighted / totalWeight)
})
const compositeDirection = computed(() => compositeScore.value === '—' ? '暂无结论' : compositeScore.value >= 55 ? '看涨' : compositeScore.value <= 45 ? '看跌' : '震荡')
const medianOutcome = computed(() => quantile(matches.value.map((x) => x.outcome), 0.5))
const outcomeRanges = computed(() => {
  const build = (values) => {
    if (!values.length) return null
    const low = quantile(values, 0.25)
    const high = quantile(values, 0.75)
    const median = quantile(values, 0.5)
    const price = paperEntryPrice.value
    return {
      count: values.length,
      low,
      high,
      median,
      priceLow: price * (1 + low / 100),
      priceHigh: price * (1 + high / 100),
      priceMedian: price * (1 + median / 100),
    }
  }
  return {
    up: build(matches.value.map((item) => Number(item.outcome)).filter((value) => value > 1)),
    down: build(matches.value.map((item) => Number(item.outcome)).filter((value) => value < -1)),
  }
})
const visibleBarCount = computed(() => {
  if (!bars.value.length) return 0
  return Math.max(1, Math.round(bars.value.length * (chartZoom.value[1] - chartZoom.value[0]) / 100))
})
const sampleBarCount = computed(() => samplePools.value.reduce((sum, row) => sum + row.bars.length, 0))
const sampleCoverage = computed(() => {
  const all = samplePools.value.flatMap((row) => row.bars)
  if (!all.length) return '—'
  const values = all.map((row) => Number(row.ts)).filter(Number.isFinite)
  return `${formatDate(Math.min(...values))} 至 ${formatDate(Math.max(...values))}`
})

function formatDate(ts) {
  const d = new Date(Number(ts) * 1000)
  const intraday = ['1m', '5m', '15m', '30m', '1h', '4h'].includes(interval.value)
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Manila',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    ...(intraday ? { hour: '2-digit', minute: '2-digit', hour12: false } : {}),
  }).formatToParts(d)
  const value = (type) => parts.find((part) => part.type === type)?.value || ''
  const date = `${value('year')}-${value('month')}-${value('day')}`
  return intraday ? `${date} ${value('hour')}:${value('minute')}` : date
}

function roundedPrice(value) {
  if (!Number.isFinite(value)) return 0
  return Number(value.toFixed(value < 1 ? 6 : value < 100 ? 4 : 2))
}

function displayPrice(value) {
  const number = Number(value)
  if (!Number.isFinite(number)) return '—'
  return number.toFixed(number < 1 ? 6 : number < 100 ? 4 : 2)
}

function resetPaperTargets() {
  const price = paperEntryPrice.value
  if (!price) return
  spotSellPrice.value = roundedPrice(price * 1.05)
  leverageTargetPrice.value = roundedPrice(price * (paperSide.value === 'short' ? 0.95 : 1.05))
}

async function fetchSpotBars(targetSymbol, targetInterval, limit) {
  let lastError = null
  for (const base of BINANCE_SPOT_URLS) {
    try {
      const response = await fetch(`${base}/api/v3/klines?symbol=${encodeURIComponent(targetSymbol)}&interval=${encodeURIComponent(targetInterval)}&limit=${Math.min(1000, limit)}`)
      if (!response.ok) throw new Error(`HTTP ${response.status}`)
      const rows = await response.json()
      const now = Date.now()
      return rows.map((row) => ({
        ts: Math.floor(Number(row[0]) / 1000),
        close_ts: Math.floor(Number(row[6]) / 1000),
        closed: now > Number(row[6]),
        open: Number(row[1]), high: Number(row[2]), low: Number(row[3]), close: Number(row[4]),
        volume: Number(row[5] || 0), market: 'spot',
      }))
    } catch (error) {
      lastError = error
    }
  }
  throw lastError || new Error(`拉不到 ${targetSymbol} 的 Binance 现货 K 线`)
}

async function loadSpotData(coinId) {
  try {
    const [data, samples] = await Promise.all([
      api.cryptoKlines(coinId, { interval: interval.value, limit: 500 }),
      api.cryptoPatternSamples({ symbols: SAMPLE_SYMBOLS.join(','), interval: interval.value, limit: 1500 }),
    ])
    if (!(data.bars || []).length || (samples.items || []).some((row) => !(row.bars || []).length || row.market !== 'spot')) {
      throw new Error('NAS 现货接口不可用')
    }
    marketTransport.value = 'NAS 后端'
    return [data, samples]
  } catch {
    const [currentBars, ...sampleBars] = await Promise.all([
      fetchSpotBars(symbol.value, interval.value, 500),
      ...SAMPLE_SYMBOLS.map((item) => fetchSpotBars(item, interval.value, 1000)),
    ])
    marketTransport.value = '浏览器直连'
    return [
      { bars: currentBars, market: 'spot' },
      { items: SAMPLE_SYMBOLS.map((item, index) => ({ symbol: item, market: 'spot', bars: sampleBars[index] })) },
    ]
  }
}

function normalize(source) {
  const base = Number(source[0]?.close || 1)
  return source.map((b) => [b.open, b.high, b.low, b.close].map((v) => Number(v) / base - 1))
}

function shapeSignature(source) {
  const closes = source.map((b) => Number(b.close))
  const returns = closes.slice(1).map((v, i) => v / closes[i] - 1)
  const ranges = source.map((b) => (Number(b.high) - Number(b.low)) / Number(b.open))
  const peakDrawdowns = closes.map((v, i) => v / Math.max(...closes.slice(0, i + 1)) - 1)
  const volumes = source.map((b) => Number(b.volume || 0))
  const net = closes.at(-1) / closes[0] - 1
  const volatility = Math.sqrt(returns.reduce((sum, v) => sum + v * v, 0) / Math.max(1, returns.length))
  const upRatio = source.filter((b) => Number(b.close) >= Number(b.open)).length / source.length
  const avgRange = ranges.reduce((sum, v) => sum + v, 0) / source.length
  const maxDrawdown = Math.min(...peakDrawdowns)
  const volumeTrend = volumes.at(-1) / Math.max(volumes[0], 1) - 1
  const bucket = (value, step) => Math.round(value / step)
  return [
    source.length,
    bucket(net, 0.02),
    bucket(volatility, 0.005),
    bucket(maxDrawdown, 0.02),
    bucket(upRatio, 0.1),
    bucket(avgRange, 0.005),
    includeVolume.value ? bucket(volumeTrend, 0.25) : 'volume-off',
  ].join('|')
}

function exactEqual(a, b) {
  if (a.length !== b.length) return false
  const left = normalize(a)
  const right = normalize(b)
  return left.every((candle, index) => candle.every((value, field) => (
    Number(value.toFixed(8)) === Number(right[index][field].toFixed(8))
  )))
}

function similarityScore(a, b) {
  const left = normalize(a)
  const right = normalize(b)
  let error = 0
  let points = 0
  for (let i = 0; i < left.length; i += 1) {
    for (let field = 0; field < 4; field += 1) {
      error += (left[i][field] - right[i][field]) ** 2
      points += 1
    }
  }
  const rmse = Math.sqrt(error / Math.max(1, points))
  return Math.max(0, 100 - rmse * 900)
}

function quantile(values, q) {
  const sorted = values.filter(Number.isFinite).sort((a, b) => a - b)
  if (!sorted.length) return 0
  const pos = (sorted.length - 1) * q
  const base = Math.floor(pos)
  const rest = pos - base
  return sorted[base + 1] == null ? sorted[base] : sorted[base] + rest * (sorted[base + 1] - sorted[base])
}

function clampScore(value) {
  return Math.max(0, Math.min(100, Math.round(Number(value) || 0)))
}

function mean(values) {
  const valid = values.filter(Number.isFinite)
  return valid.length ? valid.reduce((sum, value) => sum + value, 0) / valid.length : 0
}

function seriesReturn(source) {
  const first = Number(source[0]?.close)
  const last = Number(source.at(-1)?.close)
  return first > 0 && Number.isFinite(last) ? (last / first - 1) * 100 : 0
}

function signed(value) {
  const number = Number(value || 0)
  return `${number > 0 ? '+' : ''}${number.toFixed(2)}`
}

function factorDirection(score) {
  return score >= 55 ? '偏多' : score <= 45 ? '偏空' : '中性'
}

function candleGeometry(source, width = 156, height = 48) {
  if (!source?.length) return []
  const high = Math.max(...source.map((b) => Number(b.high)))
  const low = Math.min(...source.map((b) => Number(b.low)))
  const span = high - low || 1
  const step = width / source.length
  const y = (v) => height - ((Number(v) - low) / span) * (height - 4)
  return source.map((b, i) => ({
    x: 2 + i * step + step / 2,
    width: Math.max(1.5, Math.min(6, step * 0.55)),
    high: y(b.high), low: y(b.low), open: y(b.open), close: y(b.close), up: Number(b.close) >= Number(b.open),
  }))
}

function yieldToBrowser() {
  return new Promise((resolve) => requestAnimationFrame(() => setTimeout(resolve, 0)))
}

async function analyzeMatches() {
  const source = bars.value
  const [start, end] = selection.value
  const target = source.slice(start, end + 1)
  const length = target.length
  const candidates = []
  const totalWindows = samplePools.value.reduce(
    (sum, pool) => sum + Math.max(0, pool.bars.length - length - horizon.value + 1), 0,
  )
  let processed = 0
  matchPhase.value = '扫描历史样本'
  for (const pool of samplePools.value) {
    const poolBars = pool.bars
    for (let i = 0; i + length + horizon.value <= poolBars.length; i += 1) {
      processed += 1
      const segmentEnd = i + length - 1
      const overlapsCurrent = pool.symbol === symbol.value
        && Number(poolBars[i]?.ts) <= Number(target.at(-1)?.ts)
        && Number(poolBars[segmentEnd]?.ts) >= Number(target[0]?.ts)
      if (overlapsCurrent) continue
      const segment = poolBars.slice(i, i + length)
      const exact = exactEqual(target, segment)
      const score = exact ? 100 : similarityScore(target, segment)
      if ((mode.value === 'strict' && exact) || mode.value === 'flexible') {
        candidates.push({ i, score, segment, sourceSymbol: pool.symbol, poolBars })
      }
      if (processed % 80 === 0) {
        matchProgress.value = Math.min(85, Math.round(processed / Math.max(1, totalWindows) * 85))
        await yieldToBrowser()
      }
    }
  }
  rawCandidates.value = candidates.length
  matchPhase.value = '排序并去除重叠事件'
  matchProgress.value = 90
  await yieldToBrowser()
  candidates.sort((a, b) => b.score - a.score)
  const kept = []
  for (const row of candidates) {
    if (kept.some((x) => x.sourceSymbol === row.sourceSymbol && Math.abs(x.i - row.i) < Math.max(1, Math.floor(length / 2)))) continue
    const futureBars = row.poolBars.slice(row.i + length, row.i + length + horizon.value)
    const base = Number(row.segment.at(-1).close)
    const outcome = (Number(futureBars.at(-1)?.close || base) / base - 1) * 100
    kept.push({
      i: row.i,
      sourceSymbol: row.sourceSymbol,
      date: `${row.segment[0].date} → ${row.segment.at(-1).date}`,
      similarity: row.score,
      outcome,
      segmentBars: row.segment,
      futureBars,
      label: outcome > 1 ? '上涨' : outcome < -1 ? '下跌' : '横盘',
    })
    if (kept.length >= 12) break
  }
  matches.value = kept
  analysisDirty.value = false
  matchPhase.value = '生成统计结果'
  matchProgress.value = 97
  await yieldToBrowser()
}

async function loadMarket() {
  loading.value = true
  sampleLoading.value = true
  try {
    const coins = await api.cryptoCoins({ with_quote: false })
    coinRows.value = coins.items || []
    const coin = coinRows.value.find((row) => (row.binance_symbol || `${row.symbol}USDT`) === symbol.value) || coinRows.value[0]
    if (!coin) throw new Error('币列表为空')
    symbol.value = coin.binance_symbol || `${coin.symbol}USDT`
    const [data, samples] = await loadSpotData(coin.id)
    bars.value = (data.bars || []).map((b) => ({ ...b, date: formatDate(b.ts) }))
    resetPaperTargets()
    samplePools.value = (samples.items || []).map((row) => ({
      symbol: row.symbol,
      bars: (row.bars || []).filter((b) => b.closed !== false).map((b) => ({ ...b, date: formatDate(b.ts) })),
    })).filter((row) => row.bars.length)
    const end = Math.max(0, bars.value.length - 1)
    selection.value = [Math.max(0, end - 23), end]
    const viewStart = Math.max(0, selection.value[0] - 36)
    const viewEnd = Math.min(bars.value.length - 1, selection.value[1] + 36)
    chartZoom.value = [viewStart / Math.max(1, bars.value.length - 1) * 100, viewEnd / Math.max(1, bars.value.length - 1) * 100]
    matches.value = []
    analyzed.value = false
    analysisDirty.value = true
    runStatus.value = '切片已更新，请点击“开始匹配”'
    await paintCharts()
  } catch (error) {
    ElMessage.error(error.message || '真实 K 线加载失败')
  } finally {
    loading.value = false
    sampleLoading.value = false
  }
}

async function loadResearchRecords() {
  try {
    const [studyData, tradeData] = await Promise.all([api.cryptoPatternStudies(), api.cryptoPaperTrades()])
    studies.value = studyData.items || []
    paperTrades.value = tradeData.items || []
    paperTotalAmount.value = Number(tradeData.total_amount || 0)
    serverAvailableUsdt.value = Number(tradeData.available_usdt || 0)
  } catch (error) {
    ElMessage.error(error.message || '复盘记录加载失败')
  }
}

async function loadNewsAnalysis() {
  try {
    newsAnalysis.value = await api.cryptoNews({ limit: 50 })
  } catch {
    newsAnalysis.value = { items: [] }
  }
}

function buildPriceOption() {
  const areaStart = bars.value[selection.value[0]]?.date
  const areaEnd = bars.value[selection.value[1]]?.date
  return {
    animation: false,
    backgroundColor: 'transparent',
    tooltip: { trigger: 'axis', backgroundColor: '#101820', borderColor: '#263446', textStyle: { color: '#e9f0f7' } },
    grid: [{ left: 64, right: 24, top: 22, height: '58%' }, { left: 64, right: 24, top: '67%', height: '12%' }],
    dataZoom: [
      { type: 'inside', xAxisIndex: [0, 1], start: chartZoom.value[0], end: chartZoom.value[1], zoomOnMouseWheel: true, moveOnMouseMove: true, moveOnMouseWheel: false, preventDefaultMouseMove: true },
      { type: 'slider', xAxisIndex: [0, 1], start: chartZoom.value[0], end: chartZoom.value[1], bottom: 8, height: 24, brushSelect: true, borderColor: '#263446', fillerColor: 'rgba(122,162,255,.18)', handleStyle: { color: '#7aa2ff' }, textStyle: { color: '#a8b5c5' } },
    ],
    xAxis: [
      { type: 'category', data: bars.value.map((b) => b.date), axisLabel: { color: '#78889d', interval: 60 }, axisLine: { lineStyle: { color: '#263446' } } },
      { type: 'category', gridIndex: 1, data: bars.value.map((b) => b.date), axisLabel: { show: false }, axisLine: { show: false } },
    ],
    yAxis: [
      { scale: true, axisLabel: { color: '#78889d' }, splitLine: { lineStyle: { color: 'rgba(148,176,210,.08)' } } },
      { gridIndex: 1, axisLabel: { show: false }, splitLine: { show: false } },
    ],
    series: [
      {
        type: 'candlestick',
        data: bars.value.map((b) => [b.open, b.close, b.low, b.high]),
        itemStyle: { color: '#3ee0a0', color0: '#ff6b7a', borderColor: '#3ee0a0', borderColor0: '#ff6b7a' },
        markArea: { silent: true, itemStyle: { color: 'rgba(122,162,255,.13)', borderColor: '#7aa2ff', borderWidth: 1 }, data: [[{ xAxis: areaStart }, { xAxis: areaEnd }]] },
      },
      { type: 'bar', xAxisIndex: 1, yAxisIndex: 1, data: bars.value.map((b) => b.volume), itemStyle: { color: 'rgba(122,162,255,.35)' } },
    ],
  }
}

function buildProbabilityOption() {
  const values = [Number(upProbability.value), Number(flatProbability.value), Number(downProbability.value)]
  return {
    animation: false,
    tooltip: { trigger: 'axis', formatter: (rows) => `${rows[0].name}<br/><b>${rows[0].value.toFixed(1)}%</b>`, backgroundColor: '#101820', borderColor: '#263446', textStyle: { color: '#e9f0f7' } },
    grid: { left: 54, right: 22, top: 28, bottom: 40 },
    xAxis: { type: 'category', data: ['看涨（> +1%）', '震荡（±1%）', '看跌（< -1%）'], axisLabel: { color: '#a8b5c5', fontSize: 12 }, axisLine: { lineStyle: { color: '#263446' } } },
    yAxis: { type: 'value', min: 0, max: 100, axisLabel: { color: '#78889d', formatter: '{value}%' }, splitLine: { lineStyle: { color: 'rgba(148,176,210,.08)' } } },
    series: [
      { type: 'bar', barMaxWidth: 88, data: values.map((value, i) => ({ value, itemStyle: { color: ['#3ee0a0', '#f5c16c', '#ff6b7a'][i] } })), label: { show: true, position: 'top', formatter: ({ value }) => `${value.toFixed(1)}%`, color: '#e9f0f7', fontSize: 15, fontWeight: 700 } },
    ],
  }
}

async function paintCharts() {
  await nextTick()
  if (priceEl.value) {
    if (priceChart && (priceChart.isDisposed() || priceChart.getDom() !== priceEl.value)) {
      if (!priceChart.isDisposed()) priceChart.dispose()
      priceChart = null
    }
    if (!priceChart) {
      priceChart = echarts.init(priceEl.value, null, { renderer: 'canvas' })
      priceChart.on('datazoom', (event) => {
        const zoom = event.batch?.[0] || event
        if (Number.isFinite(zoom.start) && Number.isFinite(zoom.end)) chartZoom.value = [zoom.start, zoom.end]
      })
    }
    priceChart.setOption(buildPriceOption(), true)
  }
  if (probabilityEl.value) {
    if (probabilityChart && (probabilityChart.isDisposed() || probabilityChart.getDom() !== probabilityEl.value)) {
      if (!probabilityChart.isDisposed()) probabilityChart.dispose()
      probabilityChart = null
    }
    if (!probabilityChart) probabilityChart = echarts.init(probabilityEl.value, null, { renderer: 'canvas' })
    probabilityChart.setOption(buildProbabilityOption(), true)
    probabilityChart.resize()
  }
}

function zoomChart(scale) {
  const [start, end] = chartZoom.value
  const center = (start + end) / 2
  const span = Math.max(2, Math.min(100, (end - start) * scale))
  let nextStart = center - span / 2
  let nextEnd = center + span / 2
  if (nextStart < 0) { nextEnd -= nextStart; nextStart = 0 }
  if (nextEnd > 100) { nextStart -= nextEnd - 100; nextEnd = 100 }
  chartZoom.value = [Math.max(0, nextStart), Math.min(100, nextEnd)]
  priceChart?.dispatchAction({ type: 'dataZoom', start: chartZoom.value[0], end: chartZoom.value[1] })
}

function resetChartZoom() {
  chartZoom.value = [0, 100]
  priceChart?.dispatchAction({ type: 'dataZoom', start: 0, end: 100 })
}

async function runAnalysis() {
  if (!samplePools.value.length) {
    ElMessage.warning('历史样本尚未加载完成')
    return
  }
  const startedAt = Date.now()
  loading.value = true
  matchProgress.value = 2
  matchPhase.value = '准备当前切片'
  analyzed.value = false
  runStatus.value = '正在准备匹配…'
  await nextTick()
  await yieldToBrowser()
  try {
    await analyzeMatches()
    savedStudyId.value = null
    analyzed.value = true
    await paintCharts()
    matchProgress.value = 100
    matchPhase.value = '完成'
    const remaining = 450 - (Date.now() - startedAt)
    if (remaining > 0) await new Promise((resolve) => setTimeout(resolve, remaining))
    const now = new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
    runStatus.value = `匹配完成 · ${now} · 找到 ${sampleCount.value} 个历史案例`
    if (!sampleCount.value) ElMessage.info(mode.value === 'strict' ? '匹配完成：没有找到完全一致的历史切片' : '匹配完成：当前条件下没有相似案例')
    else ElMessage.success(`匹配完成：找到 ${sampleCount.value} 个历史案例`)
  } catch (error) {
    runStatus.value = '匹配失败，请重试'
    ElMessage.error(error.message || '历史匹配失败')
  } finally {
    loading.value = false
  }
}

async function saveCurrentStudy() {
  if (!sampleCount.value) {
    ElMessage.warning('当前没有可保存的匹配结果')
    return
  }
  studySaving.value = true
  try {
    const startBar = bars.value[selection.value[0]]
    const endBar = bars.value[selection.value[1]]
    const row = await api.createCryptoPatternStudy({
    symbol: symbol.value,
    interval: interval.value,
    slice_start_ts: Number(startBar.ts),
    slice_end_ts: Number(endBar.ts),
    horizon: horizon.value,
    direction: compositeDirection.value,
    probability: compositeDirection.value === '看跌' ? Number(downProbability.value) : compositeDirection.value === '震荡' ? Number(flatProbability.value) : Number(upProbability.value),
    composite_score: Number(compositeScore.value),
    sample_symbols: samplePools.value.map((x) => x.symbol),
    sample_count: sampleCount.value,
    entry_price: Number(bars.value.at(-1)?.close || 0),
    payload: {
      selected_count: selectedCount.value,
      raw_candidates: rawCandidates.value,
      sample_bars: sampleBarCount.value,
      coverage: sampleCoverage.value,
      selected_bars: bars.value.slice(selection.value[0], selection.value[1] + 1).map(({ ts, open, high, low, close, volume }) => ({ ts, open, high, low, close, volume })),
      predicted_direction: compositeDirection.value,
      predicted_probability: compositeDirection.value === '看跌' ? Number(downProbability.value) : compositeDirection.value === '震荡' ? Number(flatProbability.value) : Number(upProbability.value),
      factor_breakdown: factorBreakdown.value,
      outcome_ranges: outcomeRanges.value,
      matched_cases: matches.value.slice(0, 12).map((item) => ({ symbol: item.sourceSymbol, date: item.date, outcome: item.outcome })),
    },
    })
    savedStudyId.value = row.id
    await loadResearchRecords()
    ElMessage.success('切片研究已保存，可在下方持续复盘')
    return row
  } catch (error) {
    ElMessage.error(error.message || '保存切片失败')
    return null
  } finally {
    studySaving.value = false
  }
}

async function resetResearchData() {
  if (!window.confirm('确认清空当前账户的全部模拟持仓、买卖记录和切片复盘记录？此操作不可撤销。')) return
  try {
    await api.resetCryptoResearchData()
    savedStudyId.value = null
    await loadResearchRecords()
    ElMessage.success('模拟持仓、买卖记录和复盘记录已清空')
  } catch (error) {
    ElMessage.error(error.message || '清空失败')
  }
}

async function openPaperTrade(tradeType = 'spot') {
  if (orderSubmitting.value) return
  orderSubmitting.value = true
  try {
    const entryPrice = paperEntryPrice.value
    const side = tradeType === 'leverage' ? paperSide.value : 'long'
    const targetPrice = tradeType === 'spot' ? (autoSellEnabled.value ? Number(spotSellPrice.value) : null) : Number(leverageTargetPrice.value || 0) || null
    const notional = tradeType === 'spot' ? spotNotional.value : Number(paperNotional.value)
    if (tradeType === 'spot' && (notional <= 0 || notional > paperAvailableUsdt.value)) throw new Error('买入金额必须大于 0 且不能超过可用 USDT')
    await api.createCryptoPaperTrade({
    study_id: savedStudyId.value || null,
    symbol: symbol.value,
    interval: interval.value,
    trade_type: tradeType,
    leverage: tradeType === 'leverage' ? Number(paperLeverage.value) : 1,
    side,
      notional,
      entry_price: entryPrice,
      target_price: targetPrice,
    exit_after_bars: horizon.value,
    })
    await loadResearchRecords()
    const label = tradeType === 'spot' ? '现货买入' : `${paperLeverage.value}x 杠杆${side === 'long' ? '做多' : '做空'}`
    ElMessage.success(`${label}模拟已建立${targetPrice ? '，达到目标价自动卖出' : ''}`)
  } catch (error) {
    ElMessage.error(error.message || '模拟订单创建失败')
  } finally {
    orderSubmitting.value = false
  }
}

function setSpotPercent(percent) {
  if (spotAction.value === 'buy') {
    const amount = paperAvailableUsdt.value * percent / 100
    if (spotInputMode.value === 'amount') paperNotional.value = Number(amount.toFixed(2))
    else spotQuantityInput.value = Number((amount / Math.max(paperEntryPrice.value, 0.00000001)).toFixed(6))
  } else {
    spotSellQuantity.value = percent === 100
      ? spotHoldingQty.value
      : Number((spotHoldingQty.value * percent / 100).toFixed(6))
  }
}

async function sellSpotQuantity() {
  const requested = Number(spotSellQuantity.value || 0)
  if (orderSubmitting.value || requested <= 0 || requested > spotHoldingQty.value) {
    ElMessage.error('卖出数量必须大于 0 且不能超过可用持仓')
    return
  }
  orderSubmitting.value = true
  try {
    let pending = requested
    for (const item of [...openSpotTrades.value].sort((a, b) => a.id - b.id)) {
      if (pending <= 0) break
      const available = Number(item.remaining_quantity ?? item.quantity)
      const quantity = Math.min(pending, available)
      await api.sellCryptoPaperTrade(item.id, { quantity, price: paperEntryPrice.value })
      pending -= quantity
    }
    await loadResearchRecords()
    spotSellQuantity.value = 0
    ElMessage.success(`已按当前模拟市价卖出 ${requested.toFixed(6)} ${symbol.value.replace('USDT', '')}`)
  } catch (error) {
    await loadResearchRecords()
    ElMessage.error(error.message || '现货卖出失败')
  } finally {
    orderSubmitting.value = false
  }
}

function submitSpotOrder() {
  if (spotAction.value === 'buy') return openPaperTrade('spot')
  return sellSpotQuantity()
}

async function sellPaperTrade(item) {
  if (closingTradeId.value) return
  closingTradeId.value = item.id
  try {
    await api.closeCryptoPaperTrade(item.id)
    await loadResearchRecords()
    ElMessage.success(`#${item.id} 已按当前 Binance 现货价卖出`)
  } catch (error) {
    ElMessage.error(error.message || '手动卖出失败')
  } finally {
    closingTradeId.value = null
  }
}

function formatTime(ts) {
  return new Date(Number(ts) * 1000).toLocaleString('zh-CN', { hour12: false })
}

function resizeCharts() {
  priceChart?.resize()
  probabilityChart?.resize()
}

watch(selection, () => {
  analysisDirty.value = true
  runStatus.value = '切片已调整，点击“开始匹配”后才会重新计算'
  paintCharts()
}, { deep: true })
watch(horizon, () => {
  analysisDirty.value = true
  runStatus.value = `结果窗口已改为 ${horizonLabel.value}，请重新匹配`
})
watch(paperSide, resetPaperTargets)
watch([symbol, interval], loadMarket)
watch(mode, () => {
  analysisDirty.value = true
  runStatus.value = '匹配模式已改变，请重新匹配'
})
onMounted(() => {
  loadMarket()
  loadResearchRecords()
  loadNewsAnalysis()
  window.addEventListener('resize', resizeCharts)
})
onUnmounted(() => {
  window.removeEventListener('resize', resizeCharts)
  priceChart?.dispose()
  probabilityChart?.dispose()
  priceChart = null
  probabilityChart = null
})
</script>

<template>
  <div class="pattern-page">
    <section class="analysis-toolbar">
      <div class="field-group">
        <label>交易对</label>
        <el-select v-model="symbol" style="width: 150px">
          <el-option v-for="item in symbols" :key="item" :label="item" :value="item" />
        </el-select>
      </div>
      <div class="field-group">
        <label>周期</label>
        <div class="segmented">
          <button v-for="item in intervals" :key="item.value" :class="{ active: interval === item.value }" @click="interval = item.value">{{ item.label }}</button>
        </div>
      </div>
      <div class="field-group">
        <label>匹配模式</label>
        <div class="segmented">
          <button :class="{ active: mode === 'strict' }" @click="mode = 'strict'">精确匹配</button>
          <button :class="{ active: mode === 'flexible' }" @click="mode = 'flexible'">相似匹配</button>
        </div>
      </div>
      <div class="toolbar-spacer"></div>
      <div class="run-action">
        <el-button type="primary" :loading="loading || sampleLoading" :disabled="loading || sampleLoading" @click="runAnalysis">{{ sampleLoading ? '样本加载中' : loading ? `匹配中 ${matchProgress}%` : analysisDirty ? '开始匹配' : '重新匹配' }}</el-button>
        <small v-if="runStatus">{{ runStatus }}</small>
      </div>
    </section>

    <section v-if="loading && !sampleLoading" class="match-progress" aria-live="polite">
      <div><b>{{ matchPhase }}</b><span>{{ matchProgress }}%</span></div>
      <el-progress :percentage="matchProgress" :stroke-width="8" :show-text="false" />
      <small>正在计算历史 K 线，请勿重复点击；页面仍可正常滚动。</small>
    </section>

    <div class="demo-notice"><b>数据状态：Binance 现货真实行情</b> · {{ marketTransport }} · 时间为菲律宾时间；当前图表为 {{ symbol }} 最近 {{ bars.length }} 根；历史匹配仅使用已收盘 K 线，样本来自 {{ samplePools.map(x => x.symbol).join('、') || '加载中' }}，共 {{ sampleBarCount.toLocaleString() }} 根，覆盖 {{ sampleCoverage }}。匹配与综合指数仍为测试算法，不代表交易信号。</div>
    <div v-if="mode === 'strict'" class="criteria-note">
      <b>精确匹配口径</b>
      <span>先按各切片第一根收盘价归一化，再逐根比较开、高、低、收四项；全部数值精确到小数点后 8 位完全一致才命中。结果不计算相似度，也不会用近似案例补位。</span>
    </div>
    <div v-else class="criteria-note">
      <b>相似匹配口径</b>
      <span>按各切片第一根收盘价归一化，逐根计算开、高、低、收四项的均方根误差，再换算为相似度；从全部样本中按分数排序，展示最接近且互不重叠的 12 个历史事件。</span>
    </div>

    <section class="selection-section">
      <div class="section-heading">
        <div>
          <h2>{{ symbol }} 当前形态</h2>
          <p>{{ intervalLabel }} · 已选择 {{ selectedCount }} 根 K 线 · {{ selectedStart }} 至 {{ selectedEnd }}</p>
        </div>
        <div class="chart-tools">
          <div class="selection-legend"><span></span>蓝色区域为分析切片</div>
          <div class="zoom-status">当前约 {{ visibleBarCount }} 根</div>
          <el-tooltip content="放大 K 线" placement="top"><button class="icon-button" aria-label="放大 K 线" @click="zoomChart(0.65)"><el-icon><ZoomIn /></el-icon></button></el-tooltip>
          <el-tooltip content="缩小 K 线" placement="top"><button class="icon-button" aria-label="缩小 K 线" @click="zoomChart(1.55)"><el-icon><ZoomOut /></el-icon></button></el-tooltip>
          <el-tooltip content="显示全部 K 线" placement="top"><button class="icon-button" aria-label="显示全部 K 线" @click="resetChartZoom"><el-icon><RefreshRight /></el-icon></button></el-tooltip>
        </div>
      </div>
      <div class="zoom-help">滚轮或触控板上下滑动缩放，按住并拖动图表平移，也可拖动底部时间轴两端调整范围</div>
      <div ref="priceEl" class="price-chart"></div>
      <div class="range-control">
        <span>调整切片</span>
        <el-slider v-model="selection" range :min="0" :max="Math.max(0, bars.length - 1)" :min-range="12" :show-tooltip="false" />
        <span class="mono">{{ selectedCount }} 根</span>
      </div>
    </section>

    <section class="settings-band">
      <div>
        <span class="setting-label">特征</span>
        <el-checkbox v-model="includeVolume">成交量</el-checkbox>
        <el-checkbox v-model="includeIndicators">现有技术指标</el-checkbox>
      </div>
      <div>
        <span class="setting-label">结果窗口</span>
        <el-radio-group v-model="horizon" size="small">
          <el-radio-button :value="3">3 根</el-radio-button>
          <el-radio-button :value="7">7 根</el-radio-button>
          <el-radio-button :value="14">14 根</el-radio-button>
        </el-radio-group>
      </div>
        <div class="sample-note">当前口径：{{ horizonLabel }}。先匹配当前切片，再读取每个历史事件之后 X 根真实 K 线作为结果。</div>
    </section>

      <div v-if="analyzed && !sampleCount" class="empty-result">
        <b>{{ mode === 'strict' ? '没有找到完全一致的历史切片' : '当前阈值下没有相似事件' }}</b>
        <span v-if="mode === 'strict'">所有精确形态条件必须同时命中，不会用相似案例替代。可调整切片，或切换“相似匹配”查看近似历史事件。</span>
      </div>

      <section v-if="sampleCount" class="score-strip">
        <div class="score-main">
          <span class="eyebrow">综合指数</span>
          <strong>{{ compositeScore }}%</strong>
          <span class="bias" :class="compositeDirection === '看涨' ? 'positive' : compositeDirection === '看跌' ? 'negative' : 'neutral'">{{ compositeDirection }}</span>
        </div>
        <div class="metric"><span>{{ mode === 'strict' ? '完全一致事件' : '独立相似事件' }}</span><b>{{ sampleCount }}</b></div>
        <div class="metric"><span>后续上涨概率</span><b class="positive">{{ upProbability }}%</b></div>
        <div class="metric"><span>中位收益</span><b :class="medianOutcome >= 0 ? 'positive' : 'negative'">{{ medianOutcome >= 0 ? '+' : '' }}{{ medianOutcome.toFixed(2) }}%</b></div>
        <div class="metric"><span>原始候选</span><b>{{ rawCandidates }}</b></div>
        <div class="confidence"><span>数据来源</span><b>Binance</b><small>真实 OHLCV · 重叠事件已去重</small></div>
      </section>

      <div v-if="sampleCount" class="result-grid">
        <section class="probability-section">
          <div class="section-heading compact">
            <div><h2>后续 {{ horizon }} 根结果分布</h2><p>每个历史案例从匹配区间收盘价开始，统计后续真实行情最终涨跌</p></div>
          </div>
          <div ref="probabilityEl" class="probability-chart"></div>
          <div class="outcome-ranges">
            <article class="range-card up-range">
              <div><span>看涨后续 {{ horizon }} 根</span><b>{{ upProbability }}%</b></div>
              <template v-if="outcomeRanges.up">
                <strong>{{ signed(outcomeRanges.up.low) }}% ～ {{ signed(outcomeRanges.up.high) }}%</strong>
                <p>常见涨幅区间（25%–75% 分位）</p>
                <small>按当前价 {{ displayPrice(paperEntryPrice) }} 换算：约 {{ displayPrice(outcomeRanges.up.priceLow) }} ～ {{ displayPrice(outcomeRanges.up.priceHigh) }}；中位 {{ displayPrice(outcomeRanges.up.priceMedian) }}</small>
                <em>{{ outcomeRanges.up.count }} 个看涨案例</em>
              </template>
              <p v-else>当前没有足够的看涨案例可计算区间</p>
            </article>
            <article class="range-card down-range">
              <div><span>看跌后续 {{ horizon }} 根</span><b>{{ downProbability }}%</b></div>
              <template v-if="outcomeRanges.down">
                <strong>{{ signed(outcomeRanges.down.low) }}% ～ {{ signed(outcomeRanges.down.high) }}%</strong>
                <p>常见跌幅区间（25%–75% 分位）</p>
                <small>按当前价 {{ displayPrice(paperEntryPrice) }} 换算：约 {{ displayPrice(outcomeRanges.down.priceLow) }} ～ {{ displayPrice(outcomeRanges.down.priceHigh) }}；中位 {{ displayPrice(outcomeRanges.down.priceMedian) }}</small>
                <em>{{ outcomeRanges.down.count }} 个看跌案例</em>
              </template>
              <p v-else>当前没有足够的看跌案例可计算区间</p>
            </article>
          </div>
          <p class="range-method">区间只统计同一批匹配案例在后续 {{ horizonLabel }} 的真实收盘结果；排除最高和最低的极端 25%，不是止盈或止损保证。</p>
        </section>

        <section class="breakdown-section">
          <div class="section-heading compact"><div><h2>市场行为指数</h2><p>外部事件、资金行为与用户心理最终都回到价格和成交量</p></div></div>
          <div class="index-verdict">
            <div><span>综合指数</span><strong>{{ compositeScore }}%</strong></div>
            <b :class="compositeDirection === '看涨' ? 'positive' : compositeDirection === '看跌' ? 'negative' : 'neutral'">{{ compositeDirection }}</b>
          </div>
          <div class="factor-list">
            <article v-for="factor in factorBreakdown" :key="factor.key" class="factor-row">
              <div class="factor-title"><b>{{ factor.label }}</b><span>权重 {{ factor.weight }}%</span></div>
              <div class="factor-meter"><i :style="{ width: `${factor.score}%` }" :class="factor.score >= 55 ? 'positive-fill' : factor.score <= 45 ? 'negative-fill' : 'neutral-fill'"></i></div>
              <div class="factor-score"><strong>{{ factor.score }}</strong><span>{{ factorDirection(factor.score) }}</span></div>
              <p>{{ factor.detail }}</p>
            </article>
          </div>
          <div class="summary-box">
            <b>口径说明</b>
            <p>历史概率是主因子；价格结构、头部币共振、量价心理、事件新闻和尾部风险用于修正。系统会直接分析新闻的方向、事件类型、时效和影响等级，不把分析工作交给用户。政治、政策、市场操盘与群体心理还会通过 K 线、成交量和跨币种共振二次验证。</p>
            <div v-if="weightedNews.evidence.length" class="news-evidence">
              <span>本次新闻依据</span>
              <div v-for="item in weightedNews.evidence" :key="`${item.source}-${item.title}`">
                <b :class="item.sentiment?.level === 'bullish' ? 'positive' : item.sentiment?.level === 'bearish' ? 'negative' : 'neutral'">{{ item.sentiment?.label || '中性' }}</b>
                <p>{{ item.title }}</p>
              </div>
            </div>
          </div>
        </section>
      </div>

      <section class="research-actions">
        <div class="paper-heading">
          <h2>模拟买卖</h2>
          <p>模拟本金与当前账户总金额一致。无需先做历史匹配；做过匹配后下单可关联切片复盘。</p>
          <el-button v-if="sampleCount" :loading="studySaving" :disabled="orderSubmitting" @click="saveCurrentStudy">保存当前匹配切片</el-button>
          <el-button type="danger" plain @click="resetResearchData">清空模拟数据</el-button>
        </div>
        <div class="paper-order-lines">
          <div class="spot-ticket">
            <div class="ticket-head"><b>现货模拟</b><div class="segmented side-choice"><button :class="{ active: spotAction === 'buy' }" @click="spotAction = 'buy'">买入</button><button :class="{ active: spotAction === 'sell' }" @click="spotAction = 'sell'">卖出</button></div><span>市价 <strong>{{ paperEntryPrice }}</strong></span></div>
            <div class="ticket-body">
              <div v-if="spotAction === 'buy'" class="ticket-fields">
                <div class="segmented input-mode"><button :class="{ active: spotInputMode === 'amount' }" @click="spotInputMode = 'amount'">按金额</button><button :class="{ active: spotInputMode === 'quantity' }" @click="spotInputMode = 'quantity'">按数量</button></div>
                <label v-if="spotInputMode === 'amount'">成交金额<el-input-number v-model="paperNotional" :min="0" :max="paperAvailableUsdt" :step="100" controls-position="right" /><span>USDT</span></label>
                <label v-else>买入数量<el-input-number v-model="spotQuantityInput" :min="0" :step="100" :precision="6" controls-position="right" /><span>{{ symbol.replace('USDT', '') }}</span></label>
              </div>
              <div v-else class="ticket-fields"><label>卖出数量<el-input-number v-model="spotSellQuantity" :min="0" :max="spotHoldingQty" :step="100" :precision="6" controls-position="right" /><span>{{ symbol.replace('USDT', '') }}</span></label></div>
              <div class="percent-row"><button v-for="pct in [25, 50, 75, 100]" :key="pct" @click="setSpotPercent(pct)">{{ pct }}%</button></div>
              <div class="account-lines"><span>账户总金额 <strong>{{ paperTotalAmount.toFixed(2) }} USDT</strong></span><span>可用 <strong>{{ paperAvailableUsdt.toFixed(2) }} USDT</strong></span><span>持仓 <strong>{{ spotHoldingQty.toFixed(6) }} {{ symbol.replace('USDT', '') }}</strong></span><span>平均成本 <strong>{{ spotAverageCost ? spotAverageCost.toFixed(6) : '—' }}</strong></span><span>预计手续费 <strong>{{ spotFee.toFixed(4) }} USDT</strong></span></div>
              <div v-if="spotAction === 'buy'" class="auto-sell-row"><el-checkbox v-model="autoSellEnabled">设置自动卖出价</el-checkbox><label v-if="autoSellEnabled"><el-input-number v-model="spotSellPrice" :min="paperEntryPrice" :step="paperEntryPrice < 1 ? 0.001 : 1" :precision="paperEntryPrice < 1 ? 6 : 2" controls-position="right" /></label><span v-if="autoSellEnabled">目标毛利润 <strong class="positive">+{{ spotPotentialProfit.toFixed(2) }} USDT</strong></span></div>
              <el-button class="spot-submit" :type="spotAction === 'buy' ? 'primary' : 'danger'" :loading="orderSubmitting" :disabled="studySaving || (spotAction === 'buy' ? spotNotional <= 0 || spotNotional > paperAvailableUsdt || (autoSellEnabled && spotSellPrice <= paperEntryPrice) : spotSellQuantity <= 0 || spotSellQuantity > spotHoldingQty)" @click="submitSpotOrder">{{ orderSubmitting ? '正在提交' : spotAction === 'buy' ? `买入 ${symbol.replace('USDT', '')}` : `卖出 ${symbol.replace('USDT', '')}` }}</el-button>
            </div>
          </div>
          <div class="paper-order-line leverage-line">
            <b>杠杆模拟</b>
            <div class="segmented side-choice"><button :class="{ active: paperSide === 'long' }" @click="paperSide = 'long'">做多</button><button :class="{ active: paperSide === 'short' }" @click="paperSide = 'short'">做空</button></div>
            <label>倍数<el-input-number v-model="paperLeverage" :min="1" :max="125" :step="1" controls-position="right" class="leverage-input" /></label>
            <label>目标平仓价<el-input-number v-model="leverageTargetPrice" :min="0" :step="paperEntryPrice < 1 ? 0.001 : 1" :precision="paperEntryPrice < 1 ? 6 : 2" controls-position="right" /></label>
            <el-button :loading="orderSubmitting" :disabled="studySaving" @click="openPaperTrade('leverage')">提交{{ paperSide === 'long' ? '做多' : '做空' }}</el-button>
          </div>
        </div>
      </section>

      <section class="history-section trade-records">
        <div class="section-heading compact"><div><h2>购买与卖出记录</h2><p>记录每笔现货买入、剩余数量、卖出状态、手续费和已实现利润</p></div></div>
        <div v-if="!paperTrades.length" class="history-empty">暂无模拟交易记录</div>
        <div v-else class="history-table paper-table">
          <div class="history-head"><span>订单</span><span>买入/开仓</span><span>数量</span><span>目标/到期</span><span>状态</span><span>利润</span></div>
          <div v-for="item in paperTrades" :key="item.id" class="history-row">
            <span><b>#{{ item.id }} · {{ item.symbol }}</b><small>{{ item.trade_type === 'leverage' ? `${item.leverage || 1}x ${item.side === 'long' ? '做多' : '做空'}` : '现货买入' }}</small></span>
            <span><b>{{ item.entry_price }}</b><small>投入 {{ item.notional.toFixed(2) }} USDT</small></span>
            <span><b>{{ Number(item.remaining_quantity ?? item.quantity).toFixed(4) }} / {{ item.quantity.toFixed(4) }}</b><small>剩余 / 原始 {{ item.symbol.replace('USDT', '') }}</small></span>
            <span><b>{{ item.target_price || '未设置' }}</b><small>最晚 {{ formatTime(item.due_ts) }}</small></span>
            <span><b>{{ item.status === 'open' ? (Number(item.remaining_quantity ?? item.quantity) < item.quantity ? '部分卖出' : '持仓中') : '已卖出' }}</b><small v-if="item.exit_price">最近卖出价 {{ item.exit_price }} · {{ item.exit_reason }}</small><el-button v-if="item.status === 'open'" size="small" :loading="closingTradeId === item.id" @click="sellPaperTrade(item)">全部卖出</el-button></span>
            <span :class="item.pnl_amount >= 0 ? 'positive' : 'negative'"><b>{{ item.pnl_amount == null ? '—' : `${item.pnl_amount > 0 ? '+' : ''}${item.pnl_amount.toFixed(2)} USDT` }}</b><small>{{ item.pnl_pct == null ? '等待卖出' : `${item.pnl_pct > 0 ? '+' : ''}${item.pnl_pct.toFixed(2)}%` }} · 手续费 {{ Number(item.fee_paid || 0).toFixed(4) }}</small></span>
          </div>
        </div>
      </section>

      <section v-if="sampleCount" class="matches-section">
        <div class="section-heading compact">
          <div><h2>真实历史案例</h2><p>每行是一段连续历史行情：蓝色是命中的相同区间，黄色是之后真实发生的 {{ horizon }} 根 K 线</p></div>
          <button class="text-button">查看全部 {{ sampleCount }} 个案例</button>
        </div>
        <div class="match-table">
          <div class="match-head"><span>历史区间</span><span>{{ mode === 'strict' ? '匹配结果' : '相似度' }}</span><span>命中区间 + 后续真实走势</span><span>结果</span></div>
          <div v-for="item in matches" :key="item.date" class="match-row">
            <span class="case-date"><b class="mono">{{ item.sourceSymbol }} · {{ item.date }}</b><small>命中 {{ item.segmentBars.length }} 根，随后观察 {{ item.futureBars.length }} 根</small></span>
            <span><b>{{ mode === 'strict' ? '完全一致' : `${item.similarity.toFixed(2)}%` }}</b></span>
            <div class="case-chart">
              <div class="case-labels"><span>相同区间</span><span>后续 {{ horizon }} 根</span></div>
              <svg viewBox="0 0 360 72" aria-label="命中区间及后续真实蜡烛图">
                <rect x="0" y="0" :width="360 * item.segmentBars.length / (item.segmentBars.length + item.futureBars.length)" height="72" fill="rgba(122,162,255,.08)" />
                <rect :x="360 * item.segmentBars.length / (item.segmentBars.length + item.futureBars.length)" y="0" :width="360 * item.futureBars.length / (item.segmentBars.length + item.futureBars.length)" height="72" fill="rgba(245,193,108,.08)" />
                <line :x1="360 * item.segmentBars.length / (item.segmentBars.length + item.futureBars.length)" :x2="360 * item.segmentBars.length / (item.segmentBars.length + item.futureBars.length)" y1="0" y2="72" stroke="#f5c16c" stroke-dasharray="4 3" />
                <g v-for="(c, index) in candleGeometry([...item.segmentBars, ...item.futureBars], 356, 68)" :key="`c-${index}`" :stroke="c.up ? '#3ee0a0' : '#ff6b7a'" :fill="c.up ? '#3ee0a0' : '#ff6b7a'">
                <line :x1="c.x" :x2="c.x" :y1="c.high" :y2="c.low"/><rect :x="c.x-c.width/2" :y="Math.min(c.open,c.close)" :width="c.width" :height="Math.max(1,Math.abs(c.close-c.open))"/>
                </g>
              </svg>
              <div class="case-axis"><span>{{ item.segmentBars[0].date }}</span><span>分界</span><span>{{ item.futureBars.at(-1)?.date }}</span></div>
            </div>
            <span :class="item.outcome >= 0 ? 'positive' : 'negative'"><b>{{ item.outcome > 0 ? '+' : '' }}{{ item.outcome.toFixed(2) }}%</b> · {{ item.label }}</span>
          </div>
        </div>
      </section>

      <section class="history-section">
        <div class="section-heading compact"><div><h2>切片复盘记录</h2><p>保留当时结论，并以真实后续行情验证是否成功</p></div></div>
        <div v-if="!studies.length" class="history-empty">尚未保存切片研究</div>
        <div v-else class="history-table study-table">
          <div class="history-head"><span>保存时间</span><span>切片</span><span>当时结论</span><span>走势对比</span><span>验证结果</span></div>
          <div v-for="item in studies" :key="item.id" class="history-row">
            <span>{{ new Date(item.created_at).toLocaleString('zh-CN', { hour12: false }) }}</span>
            <span><b>{{ item.symbol }} · {{ item.interval }}</b><small>{{ formatTime(item.slice_start_ts) }} → {{ formatTime(item.slice_end_ts) }} · 后续 {{ item.horizon }} 根</small></span>
            <span><b>{{ item.direction }} {{ item.probability.toFixed(1) }}%</b><small>综合指数 {{ item.composite_score.toFixed(0) }}%</small></span>
            <span class="study-comparison"><small>保存时选择的走势</small><svg viewBox="0 0 180 48"><g v-for="(c, index) in candleGeometry(item.payload?.selected_bars || [], 176, 44)" :key="`saved-${index}`" :stroke="c.up ? '#3ee0a0' : '#ff6b7a'" :fill="c.up ? '#3ee0a0' : '#ff6b7a'"><line :x1="c.x" :x2="c.x" :y1="c.high" :y2="c.low"/><rect :x="c.x-c.width/2" :y="Math.min(c.open,c.close)" :width="c.width" :height="Math.max(1,Math.abs(c.close-c.open))"/></g></svg><small>后续真实走势</small><svg v-if="item.payload?.actual_bars?.length" viewBox="0 0 180 48"><g v-for="(c, index) in candleGeometry(item.payload.actual_bars, 176, 44)" :key="`actual-${index}`" :stroke="c.up ? '#3ee0a0' : '#ff6b7a'" :fill="c.up ? '#3ee0a0' : '#ff6b7a'"><line :x1="c.x" :x2="c.x" :y1="c.high" :y2="c.low"/><rect :x="c.x-c.width/2" :y="Math.min(c.open,c.close)" :width="c.width" :height="Math.max(1,Math.abs(c.close-c.open))"/></g></svg><small v-else>等待后续 {{ item.horizon }} 根 K 线完成</small></span>
            <span :class="item.success === true ? 'positive' : item.success === false ? 'negative' : ''"><b>{{ item.status }}</b><small v-if="item.actual_return_pct != null">真实收益 {{ item.actual_return_pct > 0 ? '+' : '' }}{{ item.actual_return_pct.toFixed(2) }}% · {{ item.success ? '判断成功' : '判断失败' }}</small></span>
          </div>
        </div>
      </section>

      <p class="risk-note">历史相似性不代表未来必然重复。结果用于研究和风险评估，不构成投资建议。</p>
  </div>
</template>

<style scoped>
.pattern-page { display: flex; flex-direction: column; gap: 14px; }
.analysis-toolbar, .settings-band, .score-strip { display: flex; align-items: center; gap: 18px; border: 1px solid var(--line); background: var(--bg-elev); padding: 14px 16px; border-radius: 8px; }
.field-group { display: flex; align-items: center; gap: 9px; }
.field-group label, .setting-label { color: var(--muted); font-size: 12px; }
.toolbar-spacer { flex: 1; }
.run-action { display: flex; flex-direction: column; align-items: flex-end; gap: 5px; }
.run-action small { color: var(--muted); font-size: 10px; white-space: nowrap; }
.segmented { display: flex; flex-wrap: wrap; padding: 3px; background: #0a0f16; border: 1px solid var(--line); border-radius: 6px; }
.segmented button { min-width: 44px; padding: 7px 10px; border: 0; border-radius: 4px; color: var(--muted); background: transparent; }
.segmented button.active { color: var(--text); background: #243147; }
.demo-notice { padding: 9px 13px; border-left: 3px solid var(--warn); background: rgba(245,193,108,.08); color: #d9c28e; font-size: 12px; }
.criteria-note { display: flex; gap: 10px; align-items: baseline; padding: 10px 13px; border: 1px solid rgba(122,162,255,.24); background: rgba(122,162,255,.06); font-size: 12px; }
.criteria-note b { flex: 0 0 auto; color: var(--accent); }
.criteria-note span { color: var(--muted); line-height: 1.6; }
.empty-result { display: flex; flex-direction: column; gap: 7px; padding: 20px; border: 1px solid var(--line); background: var(--bg-elev); border-radius: 8px; }
.empty-result span { color: var(--muted); font-size: 12px; line-height: 1.6; }
.selection-section, .probability-section, .breakdown-section, .matches-section, .history-section { border-top: 1px solid var(--line); border-bottom: 1px solid var(--line); background: rgba(14,19,27,.56); padding: 18px; }
.section-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: 20px; }
.section-heading h2 { margin: 0; font-size: 17px; letter-spacing: 0; }
.section-heading p { margin: 5px 0 0; color: var(--muted); font-size: 12px; }
.section-heading.compact { margin-bottom: 12px; }
.selection-legend { color: var(--muted); font-size: 12px; display: flex; align-items: center; gap: 7px; }
.selection-legend span { width: 14px; height: 10px; background: rgba(122,162,255,.2); border: 1px solid var(--accent); }
.chart-tools { display: flex; align-items: center; justify-content: flex-end; gap: 8px; flex-wrap: wrap; }
.zoom-status { color: var(--text); font: 600 11px/1 var(--mono); padding: 0 4px; white-space: nowrap; }
.icon-button { width: 30px; height: 30px; display: inline-grid; place-items: center; border: 1px solid var(--line); border-radius: 5px; color: var(--text); background: #111923; cursor: pointer; }
.icon-button:hover { border-color: var(--accent); color: var(--accent); background: rgba(122,162,255,.08); }
.zoom-help { margin-top: 8px; color: var(--faint); font-size: 11px; text-align: right; }
.price-chart { height: 500px; width: 100%; }
.range-control { display: grid; grid-template-columns: 72px minmax(180px, 1fr) 60px; align-items: center; gap: 16px; color: var(--muted); font-size: 12px; }
.settings-band { justify-content: space-between; }
.settings-band > div { display: flex; align-items: center; gap: 12px; }
.sample-note { color: var(--faint); font-size: 12px; }
.score-strip { display: grid; grid-template-columns: 1.2fr repeat(4, 1fr) 1.5fr; align-items: stretch; padding: 0; overflow: hidden; }
.score-strip > div { padding: 15px 16px; border-right: 1px solid var(--line); }
.score-strip > div:last-child { border-right: 0; }
.score-main { display: grid; grid-template-columns: auto 1fr; gap: 2px 10px; align-items: center; }
.score-main .eyebrow { color: var(--muted); font-size: 11px; }
.score-main strong { grid-row: 1 / 3; grid-column: 2; font: 700 34px/1 var(--mono); color: var(--accent); white-space: nowrap; }
.score-main .bias { font-size: 12px; color: var(--text); }
.neutral { color: var(--warn) !important; }
.metric, .confidence { display: flex; flex-direction: column; justify-content: center; gap: 7px; }
.metric span, .confidence span { color: var(--muted); font-size: 11px; }
.metric b, .confidence b { font: 600 20px/1 var(--mono); }
.confidence small { color: var(--faint); font-size: 10px; line-height: 1.4; }
.positive { color: var(--up); }
.negative { color: var(--down); }
.result-grid { display: grid; grid-template-columns: minmax(0, 1.75fr) minmax(280px, .75fr); gap: 14px; }
.probability-chart { height: 310px; }
.outcome-ranges { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
.range-card { position: relative; display: flex; flex-direction: column; gap: 6px; min-height: 150px; padding: 14px; border: 1px solid var(--line); background: #0a0f16; }
.range-card::before { content: ''; position: absolute; inset: 0 auto 0 0; width: 3px; }
.up-range::before { background: var(--up); }
.down-range::before { background: var(--down); }
.range-card > div { display: flex; justify-content: space-between; align-items: center; color: var(--muted); font-size: 11px; }
.range-card > div b { font: 700 18px/1 var(--mono); }
.up-range > div b, .up-range > strong { color: var(--up); }
.down-range > div b, .down-range > strong { color: var(--down); }
.range-card > strong { font: 700 22px/1.2 var(--mono); }
.range-card p { margin: 0; color: var(--muted); font-size: 10px; }
.range-card small { color: var(--text); font: 11px/1.55 var(--mono); }
.range-card em { margin-top: auto; color: var(--faint); font-size: 10px; font-style: normal; }
.range-method { margin: 10px 0 0; color: var(--faint); font-size: 10px; line-height: 1.55; }
.index-verdict { display: flex; align-items: end; justify-content: space-between; padding: 12px 0 16px; border-bottom: 1px solid var(--line); }
.index-verdict > div { display: flex; align-items: baseline; gap: 10px; }
.index-verdict span { color: var(--muted); font-size: 11px; }
.index-verdict strong { color: var(--accent); font: 700 30px/1 var(--mono); }
.index-verdict > b { font-size: 14px; }
.factor-list { display: flex; flex-direction: column; gap: 0; }
.factor-row { display: grid; grid-template-columns: minmax(90px, .7fr) minmax(100px, 1.3fr) 58px; gap: 8px 12px; align-items: center; padding: 13px 0; border-bottom: 1px solid rgba(148,176,210,.08); }
.factor-title { display: flex; flex-direction: column; gap: 3px; }
.factor-title b { font-size: 12px; }
.factor-title span, .factor-score span { color: var(--faint); font-size: 10px; }
.factor-meter { height: 7px; background: #0a0f16; overflow: hidden; }
.factor-meter i { display: block; height: 100%; }
.positive-fill { background: var(--up); }
.negative-fill { background: var(--down); }
.neutral-fill { background: var(--warn); }
.factor-score { display: flex; flex-direction: column; align-items: flex-end; gap: 2px; }
.factor-score strong { font: 700 16px/1 var(--mono); }
.factor-row p { grid-column: 1 / -1; margin: 0; color: var(--muted); font-size: 10px; line-height: 1.55; }
.summary-box { margin-top: 20px; padding: 13px 0 0; border-top: 1px solid var(--line); }
.summary-box b { font-size: 12px; }
.summary-box p { margin: 7px 0 0; color: var(--muted); font-size: 12px; line-height: 1.75; }
.news-evidence { display: grid; gap: 7px; margin-top: 12px; padding-top: 11px; border-top: 1px solid rgba(148,176,210,.08); }
.news-evidence > span { color: var(--faint); font-size: 10px; }
.news-evidence > div { display: grid; grid-template-columns: 34px 1fr; gap: 8px; align-items: baseline; }
.news-evidence b { font-size: 10px; }
.news-evidence p { margin: 0; font-size: 10px; line-height: 1.45; }
@media (max-width: 760px) {
  .outcome-ranges { grid-template-columns: 1fr; }
}
.research-actions { display: grid; grid-template-columns: minmax(180px,.55fr) minmax(0,2.45fr); align-items: start; gap: 20px; padding: 16px 18px; border: 1px solid rgba(122,162,255,.3); background: rgba(122,162,255,.06); }
.research-actions h2 { margin: 0; font-size: 15px; }
.research-actions p { margin: 6px 0 0; color: var(--muted); font-size: 12px; }
.paper-heading .el-button { margin-top: 12px; }
.paper-order-lines { display: grid; gap: 10px; min-width: 0; }
.paper-order-line { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; padding-bottom: 10px; border-bottom: 1px solid var(--line); font-size: 11px; }
.paper-order-line:last-child { padding-bottom: 0; border-bottom: 0; }
.paper-order-line > b { width: 70px; color: var(--text); }
.paper-order-line label { display: flex; align-items: center; gap: 6px; color: var(--muted); }
.paper-order-line label .el-input-number { width: 128px; }
.paper-order-line > span { color: var(--muted); white-space: nowrap; }
.paper-order-line strong { color: var(--text); font-family: var(--mono); }
.spot-ticket { display: grid; gap: 12px; padding-bottom: 14px; border-bottom: 1px solid var(--line); }
.ticket-head { display: flex; align-items: center; gap: 14px; }
.ticket-head > b { min-width: 70px; }
.ticket-head > span { margin-left: auto; color: var(--muted); font-size: 12px; }
.ticket-head strong, .account-lines strong, .auto-sell-row strong { color: var(--text); font-family: var(--mono); }
.ticket-body { display: grid; gap: 10px; padding-left: 84px; }
.ticket-fields { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.ticket-fields label, .auto-sell-row label { display: flex; align-items: center; gap: 7px; color: var(--muted); font-size: 12px; }
.ticket-fields .el-input-number { width: 180px; }
.input-mode button { min-width: 68px; }
.percent-row { display: grid; grid-template-columns: repeat(4, minmax(44px, 1fr)); gap: 6px; max-width: 430px; }
.percent-row button { padding: 7px 10px; border: 1px solid var(--line); color: var(--muted); background: #0a0f16; border-radius: 4px; }
.percent-row button:hover { color: var(--accent); border-color: var(--accent); }
.account-lines { display: grid; grid-template-columns: repeat(2, minmax(180px, 1fr)); gap: 7px 18px; color: var(--muted); font-size: 11px; }
.auto-sell-row { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; color: var(--muted); font-size: 11px; }
.auto-sell-row .el-input-number { width: 150px; }
.spot-submit { width: min(430px, 100%); }
.side-choice button { min-width: 50px; }
.leverage-input { width: 96px; }
.match-progress { display: grid; gap: 8px; padding: 12px 16px; border: 1px solid rgba(122,162,255,.3); background: rgba(122,162,255,.07); border-radius: 8px; }
.match-progress > div { display: flex; justify-content: space-between; gap: 16px; font-size: 12px; }
.match-progress span, .match-progress small { color: var(--muted); }
.history-empty { padding: 20px 0; color: var(--faint); font-size: 12px; }
.history-head, .history-row { display: grid; grid-template-columns: 1.1fr 1.2fr .85fr 1.35fr 1.2fr 1.15fr; gap: 14px; align-items: center; }
.history-head { padding: 8px 10px; color: var(--faint); font-size: 11px; border-bottom: 1px solid var(--line); }
.history-row { padding: 12px 10px; border-bottom: 1px solid rgba(148,176,210,.07); font-size: 12px; }
.history-row > span { display: flex; flex-direction: column; gap: 5px; min-width: 0; }
.history-row small { color: var(--faint); line-height: 1.45; overflow-wrap: anywhere; }
.study-comparison svg { width: 180px; height: 48px; background: #090e14; border: 1px solid var(--line); }
.text-button { border: 0; background: transparent; color: var(--accent); padding: 5px; }
.match-table { min-width: 980px; }
.match-head, .match-row { display: grid; grid-template-columns: 1.25fr .65fr minmax(360px, 2.4fr) .8fr; gap: 18px; align-items: center; padding: 12px; }
.match-head { color: var(--faint); font-size: 11px; border-bottom: 1px solid var(--line); }
.match-row { min-height: 126px; border-bottom: 1px solid rgba(148,176,210,.07); font-size: 12px; }
.match-row:last-child { border-bottom: 0; }
.case-date { display: flex; flex-direction: column; gap: 7px; }
.case-date small { color: var(--faint); line-height: 1.5; }
.case-chart { min-width: 360px; }
.case-chart svg { display: block; width: 100%; height: 76px; background: #090e14; border: 1px solid rgba(148,176,210,.12); }
.case-labels, .case-axis { display: grid; grid-template-columns: 1fr auto; color: var(--faint); font-size: 10px; }
.case-labels { margin-bottom: 5px; }
.case-labels span:first-child { color: var(--accent); }
.case-labels span:last-child { color: var(--warn); }
.case-axis { grid-template-columns: 1fr auto 1fr; margin-top: 4px; }
.case-axis span:nth-child(2) { color: var(--warn); }
.case-axis span:last-child { text-align: right; }
.mono { font-family: var(--mono); }
.risk-note { margin: 0; color: var(--faint); font-size: 11px; text-align: right; }
@media (max-width: 1100px) {
  .analysis-toolbar { flex-wrap: wrap; }
  .toolbar-spacer { display: none; }
  .score-strip { grid-template-columns: repeat(3, 1fr); }
  .score-strip > div { border-bottom: 1px solid var(--line); }
  .result-grid { grid-template-columns: 1fr; }
  .research-actions { grid-template-columns: 1fr; }
}
@media (max-width: 760px) {
  .field-group { width: 100%; justify-content: space-between; }
  .settings-band { align-items: flex-start; flex-direction: column; }
  .score-strip { grid-template-columns: repeat(2, 1fr); }
  .match-table { min-width: 0; }
  .match-head { display: none; }
  .match-row { grid-template-columns: 1fr auto; gap: 10px 12px; padding: 14px 0; }
  .case-chart { grid-column: 1 / 3; min-width: 0; }
  .match-row > span:last-child { grid-column: 1 / 3; text-align: right; }
  .price-chart { height: 420px; }
  .section-heading { flex-direction: column; }
  .chart-tools { width: 100%; justify-content: flex-start; }
  .zoom-help { text-align: left; line-height: 1.5; }
  .paper-order-lines { width: 100%; }
  .ticket-body { padding-left: 0; }
  .account-lines { grid-template-columns: 1fr; }
  .history-head { display: none; }
  .history-row { grid-template-columns: 1fr 1fr; }
}
</style>
