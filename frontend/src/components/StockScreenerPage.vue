<script setup>
import { computed, nextTick, onActivated, onDeactivated, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from '../api'
import { can } from '../auth'

const emit = defineEmits(['open-detail'])
const meta = ref(null), presets = ref([]), result = ref(null), loading = ref(false), error = ref('')
const selected = ref([]), presetName = ref(''), savingPreset = ref(false)
const logic = ref('all'), sort = ref('market_cap'), sortDir = ref('desc'), maxCandidates = ref(120)
const sectors = ref([]), profitableOnly = ref(false), fcfPositive = ref(false), ma50 = ref('any')
const turnedProfitable = ref(false), earningsMode = ref('include')
const earningsOnly = ref(false), earningsDays = ref(30)
const ranges = reactive({
  market_cap: { min: 10, max: null }, price: { min: null, max: null },
  avg_dollar_volume_20d: { min: 20, max: null }, revenue_growth_yoy: { min: null, max: null },
  net_margin: { min: null, max: null }, pe: { min: null, max: null },
  weekly_return: { min: null, max: null }, monthly_return: { min: null, max: null },
  regression_deviation: { min: null, max: null },
  revenue_growth_streak: { min: null, max: null }, revenue_acceleration_streak: { min: null, max: null },
  eps_growth_yoy: { min: null, max: null }, gross_margin_change_yoy: { min: null, max: null },
  operating_margin_change_yoy: { min: null, max: null }, fcf_positive_years: { min: null, max: null },
  fcf_margin: { min: null, max: null }, debt_to_assets: { min: null, max: null },
  net_debt_ebitda: { min: null, max: null }, interest_coverage: { min: null, max: null },
  sector_pe_percentile: { min: null, max: null }, relative_spy_1m: { min: null, max: null },
  relative_spy_3m: { min: null, max: null }, relative_spy_6m: { min: null, max: null },
  days_above_ma50: { min: null, max: null }, days_above_ma200: { min: null, max: null },
  days_since_ma50_breakout: { min: null, max: null }, days_since_ma200_breakout: { min: null, max: null },
  distance_52w_high: { min: null, max: null }, distance_52w_low: { min: null, max: null },
  volume_ratio_20d: { min: null, max: null }, volatility_60d: { min: null, max: null },
  max_drawdown_1y: { min: null, max: null }, days_to_earnings: { min: null, max: null },
})

const ADVANCED_KEYS = new Set(Object.keys(ranges).slice(9))

const COLUMNS = [
  ['price', '股价'], ['market_cap', '市值'], ['sector_zh', '行业'],
  ['avg_dollar_volume_20d', '20日日均成交额'], ['revenue_growth_yoy', '营收同比'],
  ['net_margin', '净利率'], ['free_cash_flow_positive', '自由现金流'], ['pe', 'PE（正值）'],
  ['weekly_return', '周涨跌'], ['monthly_return', '月涨跌'], ['above_ma50', '50日均线'],
  ['regression_deviation', '回归线偏离'], ['volatility_60d', '60日波动率'],
  ['max_drawdown_60d', '60日最大回撤'], ['distance_52w_high', '距52周高点'],
  ['revenue_growth_streak', '营收连续增长季度'], ['revenue_acceleration_streak', '营收增速连续提高季度'],
  ['eps_growth_yoy', 'EPS同比'], ['gross_margin_change_yoy', '毛利率同比改善'],
  ['operating_margin_change_yoy', '营业利润率同比改善'], ['fcf_positive_years', 'FCF连续为正年数'],
  ['fcf_margin', '自由现金流率'], ['debt_to_assets', '资产负债率'],
  ['net_debt_ebitda', '净负债/EBITDA'], ['interest_coverage', '利息覆盖倍数'],
  ['sector_pe_percentile', '行业PE百分位'], ['relative_spy_1m', '1月超额SPY'],
  ['relative_spy_3m', '3月超额SPY'], ['relative_spy_6m', '6月超额SPY'],
  ['days_above_ma50', '连续站上MA50'], ['days_above_ma200', '连续站上MA200'],
  ['days_since_ma50_breakout', '距突破MA50天数'], ['days_since_ma200_breakout', '距突破MA200天数'],
  ['distance_52w_low', '距52周低点'], ['volume_ratio_20d', '成交量/20日均量'],
  ['max_drawdown_1y', '一年最大回撤'], ['days_to_earnings', '距财报天数'],
  ['earnings_date', '财报日'],
]
const columns = ref(['price', 'market_cap', 'sector_zh', 'avg_dollar_volume_20d', 'revenue_growth_yoy', 'net_margin', 'free_cash_flow_positive', 'pe', 'weekly_return', 'monthly_return', 'above_ma50', 'regression_deviation'])
const rows = computed(() => result.value?.items || [])
const allSelected = computed(() => rows.value.length > 0 && selected.value.length === rows.value.length)
const columnLabel = Object.fromEntries(COLUMNS)

const builtinPresets = [
  {
    id: 'builtin-growth', name: '盈利成长', purpose: '找增长且已经赚钱的公司', required: '季度财务、现金流、盈利能力',
    config: { logic: 'all', sort: 'revenue_growth_yoy', sort_dir: 'desc', max_candidates: 120, filters: { ranges: { market_cap: { min: 1e10 }, avg_dollar_volume_20d: { min: 2e7 }, revenue_growth_yoy: { min: 15 }, net_margin: { min: 10 }, revenue_growth_streak: { min: 4 }, gross_margin_change_yoy: { min: 0 } }, profitable_only: true, free_cash_flow_positive: true, advanced_enabled: true } },
  },
  {
    id: 'builtin-cash-value', name: '现金流与估值', purpose: '找盈利、现金流为正且 PE 较低的公司', required: 'TTM 估值、年度现金流',
    config: { logic: 'all', sort: 'pe', sort_dir: 'asc', max_candidates: 120, filters: { ranges: { market_cap: { min: 1e10 }, avg_dollar_volume_20d: { min: 2e7 }, revenue_growth_yoy: { min: 0 }, net_margin: { min: 8 }, pe: { min: 5, max: 25 }, fcf_positive_years: { min: 3 }, fcf_margin: { min: 8 } }, profitable_only: true, free_cash_flow_positive: true, advanced_enabled: true } },
  },
  {
    id: 'builtin-pullback', name: '上升趋势中的回落', purpose: '中期趋势向上、近期回落的股票', required: '行情、均线、财报日历', incomplete: '50 日线与 200 日线关系、50 日线变化尚未接入',
    config: { logic: 'all', sort: 'weekly_return', sort_dir: 'asc', max_candidates: 120, filters: { ranges: { market_cap: { min: 1e10 }, avg_dollar_volume_20d: { min: 3e7 }, weekly_return: { min: -8, max: -2 }, distance_52w_high: { min: -15, max: -5 }, days_to_earnings: { min: 7 } }, above_ma50: true, earnings_mode: 'exclude', advanced_enabled: true } },
  },
  {
    id: 'builtin-breakout', name: '放量创近期新高', purpose: '找价格和成交量同时出现变化的股票', required: '行情、成交量、财报日历', incomplete: '此前 20 日最高收盘价、最新交易日涨跌幅尚未接入',
    config: { logic: 'all', sort: 'volume_ratio_20d', sort_dir: 'desc', max_candidates: 120, filters: { ranges: { market_cap: { min: 2e9 }, price: { min: 5 }, avg_dollar_volume_20d: { min: 3e7 }, volume_ratio_20d: { min: 1.5 }, days_to_earnings: { min: 3 } }, above_ma50: true, earnings_mode: 'exclude', advanced_enabled: true } },
  },
  {
    id: 'builtin-regression-below', name: '回归线下方观察', purpose: '寻找低于上升价格趋势的股票', required: '行情、回归偏离、财务与现金流', incomplete: '回归窗口、斜率、偏离持续天数尚未接入筛选器',
    config: { logic: 'all', sort: 'regression_deviation', sort_dir: 'asc', max_candidates: 120, filters: { ranges: { market_cap: { min: 1e10 }, avg_dollar_volume_20d: { min: 2e7 }, regression_deviation: { min: -2.5, max: -1 } }, profitable_only: true, free_cash_flow_positive: true, advanced_enabled: true } },
  },
  {
    id: 'builtin-earnings', name: '财报研究名单', purpose: '提前整理未来两周发布财报的公司（全美股）', required: '行情、财报日历',
    config: { logic: 'all', sort: 'days_to_earnings', sort_dir: 'asc', max_candidates: 120, filters: { ranges: { market_cap: { min: 1e10 }, avg_dollar_volume_20d: { min: 2e7 } }, earnings_within_days: 14 } },
  },
]
const allPresets = computed(() => [...builtinPresets, ...presets.value])
const presetNotice = ref('')
const presetIncomplete = ref(false)

function numberOrNull(value, scale = 1) {
  if (value === '' || value == null || !Number.isFinite(Number(value))) return null
  return Number(value) * scale
}
function config() {
  const payloadRanges = {}
  for (const [key, value] of Object.entries(ranges)) {
    const scale = key === 'market_cap' ? 1e9 : key === 'avg_dollar_volume_20d' ? 1e6 : 1
    payloadRanges[key] = { min: numberOrNull(value.min, scale), max: numberOrNull(value.max, scale) }
  }
  const advancedEnabled = turnedProfitable.value || Object.entries(payloadRanges).some(([key, value]) => ADVANCED_KEYS.has(key) && (value.min != null || value.max != null))
  return {
    logic: logic.value, sort: sort.value, sort_dir: sortDir.value, columns: columns.value,
    max_candidates: maxCandidates.value,
    filters: {
      ranges: payloadRanges, sectors: sectors.value,
      profitable_only: profitableOnly.value,
      free_cash_flow_positive: fcfPositive.value,
      above_ma50: ma50.value === 'above' ? true : ma50.value === 'below' ? false : null,
      advanced_enabled: advancedEnabled, turned_profitable: turnedProfitable.value,
      earnings_mode: earningsMode.value,
      earnings_within_days: earningsOnly.value ? earningsDays.value : null,
    },
  }
}
function openDetail(row) {
  emit('open-detail', row)
}
function loadConfig(value = {}) {
  logic.value = value.logic || 'all'; sort.value = value.sort || 'market_cap'; sortDir.value = value.sort_dir || 'desc'
  maxCandidates.value = value.max_candidates || 120; columns.value = value.columns?.length ? [...value.columns] : columns.value
  const f = value.filters || {}; sectors.value = [...(f.sectors || [])]; profitableOnly.value = !!f.profitable_only
  fcfPositive.value = !!f.free_cash_flow_positive; ma50.value = f.above_ma50 === true ? 'above' : f.above_ma50 === false ? 'below' : 'any'
  turnedProfitable.value = !!f.turned_profitable; earningsMode.value = f.earnings_mode || 'include'
  earningsOnly.value = !!f.earnings_within_days; earningsDays.value = f.earnings_within_days || 30
  for (const key of Object.keys(ranges)) {
    const scale = key === 'market_cap' ? 1e9 : key === 'avg_dollar_volume_20d' ? 1e6 : 1
    ranges[key].min = f.ranges?.[key]?.min == null ? null : f.ranges[key].min / scale
    ranges[key].max = f.ranges?.[key]?.max == null ? null : f.ranges[key].max / scale
  }
}
async function run() {
  loading.value = true; error.value = ''; selected.value = []
  if (earningsOnly.value && !columns.value.includes('earnings_date')) columns.value = ['earnings_date', ...columns.value]
  try { result.value = await api.runStockScreen(config()) }
  catch (e) { result.value = null; error.value = e.message }
  finally { loading.value = false }
}
async function loadPresets() {
  try { presets.value = (await api.stockScreenPresets()).items || [] } catch { presets.value = [] }
}
async function savePreset() {
  if (!presetName.value.trim()) return ElMessage.warning('请填写方案名称')
  savingPreset.value = true
  try { await api.saveStockScreenPreset({ name: presetName.value.trim(), config: config() }); await loadPresets(); ElMessage.success('筛选方案已保存') }
  catch (e) { ElMessage.error(e.message) } finally { savingPreset.value = false }
}
async function removePreset(row) {
  try { await api.deleteStockScreenPreset(row.id); await loadPresets(); ElMessage.success('方案已删除') } catch (e) { ElMessage.error(e.message) }
}
function selectPreset(event) {
  const row = allPresets.value.find(p => String(p.id) === event.target.value)
  if (!row) { presetIncomplete.value = false; presetNotice.value = ''; return }
  loadConfig(row.config)
  presetIncomplete.value = !!row.incomplete
  presetNotice.value = row.incomplete
    ? `${row.name}：${row.incomplete}。当前方案已填入已支持条件，暂不可完整运行。`
    : `${row.name}：${row.purpose}。所需数据：${row.required}。`
}
function toggleAll() { selected.value = allSelected.value ? [] : rows.value.map(r => r.symbol) }
async function bulkAdd() {
  const picked = rows.value.filter(r => selected.value.includes(r.symbol))
  if (!picked.length) return ElMessage.warning('请先选择股票')
  try {
    const response = await api.bulkAddStockScreen(picked.map(r => ({ symbol: r.symbol, sector_zh: r.sector_zh })))
    ElMessage.success(`已加入 ${response.added.length} 只；已存在 ${response.skipped.length} 只；失败 ${response.failed.length} 只`)
  } catch (e) { ElMessage.error(e.message) }
}
function compact(value) {
  if (value == null) return '缺失'
  const abs = Math.abs(value)
  if (abs >= 1e12) return `$${(value / 1e12).toFixed(1)}T`
  if (abs >= 1e9) return `$${(value / 1e9).toFixed(1)}B`
  if (abs >= 1e6) return `$${(value / 1e6).toFixed(1)}M`
  return `$${Number(value).toFixed(2)}`
}
function display(row, key) {
  const value = row[key]
  if (value == null) return '缺失'
  if (['market_cap', 'avg_dollar_volume_20d'].includes(key)) return compact(value)
  if (key === 'price') return `$${Number(value).toFixed(2)}`
  if (['revenue_growth_yoy', 'net_margin', 'weekly_return', 'monthly_return', 'regression_deviation', 'volatility_60d', 'max_drawdown_60d', 'distance_52w_high', 'eps_growth_yoy', 'gross_margin_change_yoy', 'operating_margin_change_yoy', 'fcf_margin', 'debt_to_assets', 'sector_pe_percentile', 'relative_spy_1m', 'relative_spy_3m', 'relative_spy_6m', 'distance_52w_low', 'max_drawdown_1y'].includes(key)) return `${Number(value).toFixed(1)}%`
  if (key === 'earnings_date') {
    const when = row.days_to_earnings === 0 ? '今天' : row.days_to_earnings != null ? `${row.days_to_earnings}天后` : ''
    const session = row.earnings_time && row.earnings_time !== '—' ? row.earnings_time : ''
    return [value, when, session].filter(Boolean).join(' · ')
  }
  if (key === 'free_cash_flow_positive') return value ? '正' : '负'
  if (key === 'above_ma50') return value ? '高于' : '低于'
  if (key === 'pe') return Number(value).toFixed(1)
  if (['volume_ratio_20d', 'net_debt_ebitda', 'interest_coverage'].includes(key)) return `${Number(value).toFixed(2)}x`
  return String(value)
}
function tone(row, key) {
  if (['weekly_return', 'monthly_return', 'revenue_growth_yoy', 'net_margin'].includes(key)) return row[key] > 0 ? 'up' : row[key] < 0 ? 'down' : ''
  if (key === 'free_cash_flow_positive') return row[key] ? 'up' : 'down'
  return ''
}
const activeChips = computed(() => {
  const chips = []
  if (earningsOnly.value) chips.push(`未来 ${earningsDays.value} 天发布财报（全美股）`)
  for (const [key, value] of Object.entries(ranges)) {
    if (value.min != null || value.max != null) chips.push(`${columnLabel[key]} ${value.min ?? '不限'}～${value.max ?? '不限'}${key === 'market_cap' ? 'B' : key === 'avg_dollar_volume_20d' ? 'M' : ''}`)
  }
  if (sectors.value.length) chips.push(`行业：${sectors.value.join('、')}`)
  if (fcfPositive.value) chips.push('自由现金流为正')
  if (profitableOnly.value) chips.push('仅盈利公司')
  if (turnedProfitable.value) chips.push('最近一季同比扭亏')
  if (ma50.value !== 'any') chips.push(`股价${ma50.value === 'above' ? '高于' : '低于'}50日均线`)
  return chips
})
onMounted(async () => {
  try { meta.value = await api.stockScreenOptions() } catch (e) { error.value = e.message }
  await loadPresets()
})
const cachedScrollY = ref(0)
onDeactivated(() => { cachedScrollY.value = window.scrollY })
onActivated(() => { nextTick(() => window.scrollTo({ top: cachedScrollY.value })) })
</script>

<template>
  <div class="stock-screen-page">
    <p class="goal-lead">默认从标普 500 股票池按基础信息、财务表现、估值和价格走势组合筛选；勾选「未来 N 天发布财报」后改为全美股中即将发财报的公司。缺失数据不会按 0 处理。</p>
    <section class="panel screen-filters">
      <div class="screen-head">
        <div><h2>筛选条件</h2><p class="sub">默认同时满足全部条件；较重指标只计算基础条件筛选后的候选股票。</p></div>
        <div class="logic-switch"><button :class="{ active: logic === 'all' }" @click="logic = 'all'">同时满足</button><button :class="{ active: logic === 'any' }" @click="logic = 'any'">满足任一</button></div>
      </div>
      <div class="filter-groups">
        <fieldset><legend>基础信息</legend><div class="filter-grid">
          <div class="earnings-pool span-2">
            <label class="check-label"><input v-model="earningsOnly" type="checkbox" />只看未来</label>
            <select v-model.number="earningsDays" :disabled="!earningsOnly" aria-label="财报天数"><option :value="7">7 天</option><option :value="14">14 天</option><option :value="30">30 天</option></select>
            <span>发布财报的股票（全美股）</span>
          </div>
          <label>市值下限（十亿美元）<input v-model.number="ranges.market_cap.min" type="number" min="0" placeholder="不限" /></label>
          <label>市值上限（十亿美元）<input v-model.number="ranges.market_cap.max" type="number" min="0" placeholder="不限" /></label>
          <label class="span-2">行业（可多选）<el-select v-model="sectors" multiple collapse-tags clearable placeholder="全部行业"><el-option v-for="sector in meta?.sectors || []" :key="sector" :label="sector" :value="sector" /></el-select></label>
          <label>股价下限（美元）<input v-model.number="ranges.price.min" type="number" min="0" placeholder="不限" /></label>
          <label>股价上限（美元）<input v-model.number="ranges.price.max" type="number" min="0" placeholder="不限" /></label>
          <label>20日日均成交额下限（百万美元）<input v-model.number="ranges.avg_dollar_volume_20d.min" type="number" min="0" placeholder="不限" /></label>
        </div></fieldset>
        <fieldset><legend>财务表现与估值</legend><div class="filter-grid">
          <label>营收同比下限（%）<input v-model.number="ranges.revenue_growth_yoy.min" type="number" placeholder="不限" /></label>
          <label>营收同比上限（%）<input v-model.number="ranges.revenue_growth_yoy.max" type="number" placeholder="不限" /></label>
          <label>净利率下限（%）<input v-model.number="ranges.net_margin.min" type="number" placeholder="不限" /></label>
          <label>PE 下限<input v-model.number="ranges.pe.min" type="number" min="0" placeholder="不限" /></label>
          <label>PE 上限<input v-model.number="ranges.pe.max" type="number" min="0" placeholder="不限" /></label>
          <label class="check-label"><input v-model="profitableOnly" type="checkbox" />仅盈利公司</label>
          <label class="check-label"><input v-model="fcfPositive" type="checkbox" />自由现金流为正</label>
        </div><p class="field-note">PE 仅使用正值；亏损公司的负 PE 作为缺失处理，不会进入“低 PE”结果。</p></fieldset>
        <fieldset><legend>价格走势</legend><div class="filter-grid">
          <label>周涨跌下限（%）<input v-model.number="ranges.weekly_return.min" type="number" placeholder="不限" /></label>
          <label>周涨跌上限（%）<input v-model.number="ranges.weekly_return.max" type="number" placeholder="不限" /></label>
          <label>月涨跌下限（%）<input v-model.number="ranges.monthly_return.min" type="number" placeholder="不限" /></label>
          <label>月涨跌上限（%）<input v-model.number="ranges.monthly_return.max" type="number" placeholder="不限" /></label>
          <label>回归线偏离下限（%）<input v-model.number="ranges.regression_deviation.min" type="number" placeholder="不限" /></label>
          <label>回归线偏离上限（%）<input v-model.number="ranges.regression_deviation.max" type="number" placeholder="不限" /></label>
          <label>股价与50日均线<select v-model="ma50"><option value="any">不限</option><option value="above">高于均线</option><option value="below">低于均线</option></select></label>
        </div></fieldset>
      </div>
      <details class="advanced-search">
        <summary><span><b>高级搜索</b><small>变化、比较和连续性条件</small></span><span class="advanced-caret">展开</span></summary>
        <p class="field-note">只有填写的条件才会参与计算。连续性使用最近可得季度或年度；相对强弱以 SPY 为基准。</p>
        <div class="advanced-groups">
          <fieldset><legend>增长持续性</legend><div class="filter-grid">
            <label>营收连续同比增长（季度）<input v-model.number="ranges.revenue_growth_streak.min" type="number" min="1" placeholder="不限" /></label>
            <label>营收增速连续提高（季度）<input v-model.number="ranges.revenue_acceleration_streak.min" type="number" min="1" placeholder="不限" /></label>
            <label>EPS 同比下限（%）<input v-model.number="ranges.eps_growth_yoy.min" type="number" placeholder="不限" /></label>
            <label>EPS 同比上限（%）<input v-model.number="ranges.eps_growth_yoy.max" type="number" placeholder="不限" /></label>
          </div></fieldset>
          <fieldset><legend>盈利改善</legend><div class="filter-grid">
            <label>毛利率同比提升下限（百分点）<input v-model.number="ranges.gross_margin_change_yoy.min" type="number" step="0.1" placeholder="不限" /></label>
            <label>营业利润率同比提升下限（百分点）<input v-model.number="ranges.operating_margin_change_yoy.min" type="number" step="0.1" placeholder="不限" /></label>
            <label class="check-label span-2"><input v-model="turnedProfitable" type="checkbox" />最近一季净利润较去年同期扭亏</label>
          </div></fieldset>
          <fieldset><legend>现金流质量</legend><div class="filter-grid">
            <label>FCF 连续为正（年）<input v-model.number="ranges.fcf_positive_years.min" type="number" min="1" placeholder="不限" /></label>
            <label>自由现金流率下限（%）<input v-model.number="ranges.fcf_margin.min" type="number" step="0.1" placeholder="不限" /></label>
            <label>自由现金流率上限（%）<input v-model.number="ranges.fcf_margin.max" type="number" step="0.1" placeholder="不限" /></label>
          </div></fieldset>
          <fieldset><legend>债务情况</legend><div class="filter-grid">
            <label>资产负债率上限（%）<input v-model.number="ranges.debt_to_assets.max" type="number" min="0" placeholder="不限" /></label>
            <label>净负债 / EBITDA 上限<input v-model.number="ranges.net_debt_ebitda.max" type="number" step="0.1" placeholder="不限" /></label>
            <label>利息覆盖倍数下限<input v-model.number="ranges.interest_coverage.min" type="number" step="0.1" placeholder="不限" /></label>
          </div><p class="field-note">金融、保险及部分房地产公司债务结构特殊，应结合行业口径判断。</p></fieldset>
          <fieldset><legend>相对估值与强弱</legend><div class="filter-grid">
            <label>行业 PE 百分位上限（%）<input v-model.number="ranges.sector_pe_percentile.max" type="number" min="0" max="100" placeholder="不限" /></label>
            <label>近1月超额 SPY 下限（%）<input v-model.number="ranges.relative_spy_1m.min" type="number" placeholder="不限" /></label>
            <label>近3月超额 SPY 下限（%）<input v-model.number="ranges.relative_spy_3m.min" type="number" placeholder="不限" /></label>
            <label>近6月超额 SPY 下限（%）<input v-model.number="ranges.relative_spy_6m.min" type="number" placeholder="不限" /></label>
          </div><p class="field-note">行业 PE 百分位按本次精算候选中的同行计算。</p></fieldset>
          <fieldset><legend>趋势变化</legend><div class="filter-grid">
            <label>连续站上 MA50（天）<input v-model.number="ranges.days_above_ma50.min" type="number" min="1" placeholder="不限" /></label>
            <label>连续站上 MA200（天）<input v-model.number="ranges.days_above_ma200.min" type="number" min="1" placeholder="不限" /></label>
            <label>近几日突破 MA50<input v-model.number="ranges.days_since_ma50_breakout.max" type="number" min="0" max="20" placeholder="不限" /></label>
            <label>近几日突破 MA200<input v-model.number="ranges.days_since_ma200_breakout.max" type="number" min="0" max="20" placeholder="不限" /></label>
          </div></fieldset>
          <fieldset><legend>位置、成交量与风险</legend><div class="filter-grid">
            <label>距52周高点下限（%）<input v-model.number="ranges.distance_52w_high.min" type="number" placeholder="如 -10" /></label>
            <label>距52周低点涨幅上限（%）<input v-model.number="ranges.distance_52w_low.max" type="number" placeholder="不限" /></label>
            <label>成交量 / 20日均量下限<input v-model.number="ranges.volume_ratio_20d.min" type="number" min="0" step="0.1" placeholder="如 1.5" /></label>
            <label>60日波动率上限（%）<input v-model.number="ranges.volatility_60d.max" type="number" min="0" placeholder="不限" /></label>
            <label>一年最大回撤下限（%）<input v-model.number="ranges.max_drawdown_1y.min" type="number" placeholder="如 -30" /></label>
            <label>一年最大回撤上限（%）<input v-model.number="ranges.max_drawdown_1y.max" type="number" placeholder="如 -5" /></label>
          </div></fieldset>
          <fieldset><legend>财报时间</legend><div class="filter-grid">
            <label>处理方式<select v-model="earningsMode"><option value="include">包含该区间</option><option value="exclude">排除该区间</option></select></label>
            <label>最少还有几天<input v-model.number="ranges.days_to_earnings.min" type="number" min="0" placeholder="0" /></label>
            <label>最多还有几天<input v-model.number="ranges.days_to_earnings.max" type="number" min="0" placeholder="如 30" /></label>
          </div></fieldset>
        </div>
      </details>
      <div class="screen-toolbar">
        <label>精算候选上限<select v-model.number="maxCandidates"><option :value="60">60只</option><option :value="120">120只</option><option :value="200">200只</option><option :value="250">250只</option></select></label>
        <label>排序<select v-model="sort"><option v-for="[key, label] in COLUMNS" :key="key" :value="key">{{ label }}</option></select></label>
        <select v-model="sortDir" aria-label="排序方向"><option value="desc">从高到低</option><option value="asc">从低到高</option></select>
        <button class="btn btn-primary" :disabled="loading || presetIncomplete" @click="run">{{ loading ? '筛选中…' : presetIncomplete ? '缺少高级条件' : '运行筛选' }}</button>
      </div>
    </section>

    <section class="screen-summary">
      <div class="chips"><span v-for="chip in activeChips" :key="chip">{{ chip }}</span><span v-if="!activeChips.length">未设置条件</span></div>
      <div class="preset-controls"><select @change="selectPreset"><option value="">加载方案</option><optgroup label="内置方案"><option v-for="p in builtinPresets" :key="p.id" :value="p.id">{{ p.name }}</option></optgroup><optgroup v-if="presets.length" label="我的方案"><option v-for="p in presets" :key="p.id" :value="p.id">{{ p.name }}</option></optgroup></select><input v-model="presetName" placeholder="方案名称" /><button class="btn" :disabled="savingPreset" @click="savePreset">保存方案</button></div>
    </section>
    <div class="builtin-presets"><button v-for="p in builtinPresets" :key="p.id" class="preset-card" type="button" @click="applyPreset(p)"><b>{{ p.name }}</b><small>{{ p.purpose }}</small><em>{{ p.incomplete ? '缺少高级条件' : '可直接运行' }} · {{ p.required }}</em></button></div>
    <div v-if="presets.length" class="preset-list"><span v-for="p in presets" :key="p.id">{{ p.name }} <button title="删除方案" @click="removePreset(p)">×</button></span></div>
    <p v-if="presetNotice" class="preset-notice" :class="{ incomplete: presetIncomplete }">{{ presetNotice }}</p>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <section v-if="result" class="panel result-panel">
      <div class="screen-head"><div><h2>筛选结果 · {{ result.count }} 只</h2><p class="sub">{{ result.source }} · {{ result.price_period }} · 财务：{{ result.financial_period }}</p></div><div class="result-actions"><details><summary class="btn">自定义列</summary><div class="column-picker"><label v-for="[key,label] in COLUMNS" :key="key"><input v-model="columns" type="checkbox" :value="key" />{{ label }}</label></div></details><button v-if="can('btn.daily_watch.write')" class="btn" :disabled="!selected.length" @click="bulkAdd">加入股票列表（{{ selected.length }}）</button></div></div>
      <div v-if="result.truncated" class="warn-msg">基础条件匹配 {{ result.prefilter_count }} 只，本次按市值优先精算前 {{ result.scanned_count }} 只。提高“精算候选上限”可扩大范围。</div>
      <p class="sub">股票池（{{ result.universe || 'S&P 500' }}）{{ result.universe_count }} 只 → 基础通过 {{ result.prefilter_count }} 只 → 高级通过 {{ result.advanced_pass_count ?? result.count }} 只；当前精算 {{ result.scanned_count }} 只，缺失数据 {{ result.missing_count ?? 0 }} 只。缺失值显示为“缺失”，不会按 0 参与筛选。<template v-if="result.advanced_period">高级口径：{{ result.advanced_period }}。</template></p>
      <div class="table-wrap screen-table"><table><thead><tr><th><input type="checkbox" :checked="allSelected" @change="toggleAll" /></th><th>代码 / 公司</th><th v-for="key in columns" :key="key">{{ columnLabel[key] }}</th><th>详情</th></tr></thead><tbody><tr v-for="row in rows" :key="row.symbol"><td><input v-model="selected" type="checkbox" :value="row.symbol" /></td><td><b>{{ row.symbol }}</b><small>{{ row.name }}</small></td><td v-for="key in columns" :key="key" :class="[tone(row,key), { missing: row[key] == null }]">{{ display(row,key) }}</td><td class="tool-actions"><button class="detail-icon" type="button" :title="`${row.symbol} 详情`" :aria-label="`${row.symbol} 详情`" @click="openDetail(row)"><svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M14 3H5v18h14V8zM14 3v5h5M8 12h8M8 16h5" /></svg></button></td></tr></tbody></table></div>
      <div v-if="!rows.length" class="empty">没有股票同时满足当前条件。可以放宽范围或切换“满足任一”。</div>
    </section>
  </div>
</template>

<style scoped>
.stock-screen-page { padding-bottom:32px; }.screen-filters { padding:22px; }.screen-head { display:flex;justify-content:space-between;align-items:flex-start;gap:18px;flex-wrap:wrap; }.screen-head h2 { margin:0; }.screen-head .sub { margin:8px 0 0; }.logic-switch { display:flex;padding:3px;border:1px solid var(--line);border-radius:10px;background:#0d1620; }.logic-switch button { border:0;background:transparent;color:var(--muted);padding:8px 13px;border-radius:7px;cursor:pointer; }.logic-switch button.active { background:#253654;color:#a9c1ff; }
.filter-groups { display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px;margin-top:20px; }.filter-groups fieldset { min-width:0;border:1px solid var(--line);border-radius:12px;padding:16px;background:rgba(7,14,22,.28); }.filter-groups legend { padding:0 8px;color:#dce6f3;font-weight:600;font-size:14px; }.filter-grid { display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px; }.filter-grid label,.screen-toolbar label { display:grid;gap:7px;color:var(--muted);font-size:11px; }.filter-grid input:not([type=checkbox]),.filter-grid select,.screen-toolbar select,.preset-controls input,.preset-controls select { width:100%;height:38px;box-sizing:border-box;border:1px solid #2c3a4d;border-radius:8px;background:#0d1620;color:#e5edf8;padding:0 11px;outline:none; }.filter-grid input:focus,.filter-grid select:focus,.preset-controls input:focus { border-color:#82a5ff;box-shadow:0 0 0 3px rgba(130,165,255,.12); }.span-2 { grid-column:1/-1; }.earnings-pool { display:flex;align-items:center;gap:8px;flex-wrap:wrap;padding:10px 12px;border:1px dashed #33507a;border-radius:10px;background:rgba(130,165,255,.06);color:#dce6f3;font-size:12px; }.earnings-pool .check-label { margin-top:0;color:#dce6f3;font-size:12px; }.earnings-pool select { width:auto!important;height:32px!important;border:1px solid #2c3a4d;border-radius:8px;background:#0d1620;color:#e5edf8;padding:0 8px; }.earnings-pool select:disabled { opacity:.5; }.check-label { display:flex!important;align-items:center;gap:8px;margin-top:16px; }.field-note { color:var(--muted);font-size:11px;line-height:1.6;margin:12px 0 0; }
.advanced-search { margin-top:16px;border:1px solid var(--line);border-radius:12px;background:rgba(7,14,22,.22); }.advanced-search>summary { display:flex;align-items:center;justify-content:space-between;padding:15px 17px;cursor:pointer;list-style:none;color:#dce6f3; }.advanced-search>summary::-webkit-details-marker { display:none; }.advanced-search>summary span:first-child { display:flex;align-items:center;gap:12px; }.advanced-search>summary small { color:var(--muted);font-weight:400; }.advanced-caret { color:#9eb5da;font-size:12px; }.advanced-search[open] .advanced-caret { font-size:0; }.advanced-search[open] .advanced-caret::after { content:'收起';font-size:12px; }.advanced-search>.field-note { padding:0 17px; }.advanced-groups { display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px;padding:4px 16px 16px; }.advanced-groups fieldset { min-width:0;border:1px solid var(--line);border-radius:10px;padding:14px;background:rgba(9,17,26,.5); }.advanced-groups legend { padding:0 7px;font-size:13px;color:#cad7e8; }
.screen-toolbar { display:flex;align-items:flex-end;justify-content:flex-end;gap:12px;margin-top:18px;flex-wrap:wrap; }.screen-toolbar label { min-width:150px; }.screen-toolbar>select { width:130px;height:38px; }.screen-toolbar .btn { height:38px; }.screen-summary { display:flex;justify-content:space-between;align-items:center;gap:14px;margin:18px 0 8px;flex-wrap:wrap; }.chips { display:flex;gap:7px;flex-wrap:wrap; }.chips span,.preset-list span { padding:6px 10px;border-radius:20px;background:#1b2940;color:#9eb5da;font-size:11px; }.preset-controls { display:flex;gap:8px; }.preset-controls input,.preset-controls select { width:150px; }.builtin-presets { display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px;margin:12px 0; }.preset-card { min-height:92px;padding:12px;text-align:left;border:1px solid var(--line);border-radius:10px;background:#101a27;color:#dce6f3;cursor:pointer;display:grid;gap:5px; }.preset-card:hover { border-color:#82a5ff;background:#152238; }.preset-card small,.preset-card em { color:var(--muted);font-size:11px;font-style:normal;line-height:1.4; }.preset-card em { color:#8faedb; }.preset-notice { margin:8px 0;color:#9eb5da;font-size:12px;line-height:1.6; }.preset-notice.incomplete { color:#d9b879; }.preset-list { display:flex;gap:7px;flex-wrap:wrap;margin-bottom:14px; }.preset-list button { border:0;background:transparent;color:#8fa4c1;cursor:pointer;margin-left:4px; }.result-panel { margin-top:16px; }.result-actions { display:flex;gap:9px;align-items:center; }.result-actions details { position:relative; }.result-actions summary { list-style:none;cursor:pointer; }.column-picker { position:absolute;right:0;top:44px;z-index:5;width:220px;padding:12px;display:grid;grid-template-columns:1fr 1fr;gap:9px;background:#101a27;border:1px solid var(--line);border-radius:10px;box-shadow:0 16px 40px rgba(0,0,0,.35); }.column-picker label { font-size:11px;color:#b9c6d8;display:flex;gap:6px;align-items:center; }.warn-msg { margin:14px 0;padding:10px 12px;border-left:3px solid #d9b879;background:rgba(217,184,121,.07);color:#d9b879;font-size:12px;line-height:1.6; }.screen-table { max-height:650px; }.screen-table th { position:sticky;top:0;z-index:2;background:#151f2c; }.screen-table td small { display:block;color:var(--muted);max-width:170px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-top:4px; }.missing { color:#6f7f94; }.tool-actions { white-space:nowrap; }.tool-actions button { width:30px;height:30px;margin-right:4px;border:1px solid #324158;border-radius:7px;background:#152238;color:#9cb7ea;cursor:pointer; }.tool-actions button:hover { border-color:#82a5ff;color:#dce7ff; }
.tool-actions .detail-icon { display:inline-flex;align-items:center;justify-content:center; }.detail-icon svg { width:16px;height:16px; }
@media(max-width:1200px){.filter-groups,.advanced-groups,.builtin-presets{grid-template-columns:1fr 1fr}.filter-groups fieldset:last-child{grid-column:1/-1}}@media(max-width:760px){.filter-groups,.advanced-groups,.builtin-presets{grid-template-columns:1fr}.filter-groups fieldset:last-child{grid-column:auto}.filter-grid{grid-template-columns:1fr}.span-2{grid-column:auto}.screen-summary,.preset-controls{align-items:stretch;flex-direction:column}.preset-controls input,.preset-controls select{width:100%}.advanced-search>summary span:first-child{align-items:flex-start;flex-direction:column;gap:3px}}
</style>
