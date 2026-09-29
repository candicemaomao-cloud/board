<script setup>
import { computed, ref, watch } from 'vue'
import { api } from '../api'
import MacroChart from './MacroChart.vue'

const props = defineProps({
  tab: { type: String, default: 'cpi' },
})

const COLORS = ['#7aa2ff', '#3ee0a0', '#f5c16c', '#ff6b7a', '#c084fc', '#22d3ee', '#fb923c', '#a3e635']
const INFLATION_TABS = new Set(['cpi', 'ppi', 'pce'])
const INFLATION_HEAD = {
  cpi: { total: 'CUSR0000SA0', core: 'CUSR0000SA0L1E', label: 'CPI' },
  ppi: { total: 'WPSFD4', core: 'WPSFD49104', label: 'PPI' },
  pce: { total: 'DPCERG-M', core: 'DPCCRG-M', label: 'PCE' },
}
const CPI_PARENTS = [
  { name: '总览', items: ['SA0', 'SA0L1E', 'SA0LE', 'SA0L1'] },
  { name: '食品', items: ['SAF', 'SAF1', 'SAF11', 'SEFV'] },
  { name: '能源', items: ['SA0E', 'SACE', 'SEHF', 'SEHF01', 'SEHF02', 'SETB', 'SETB01'] },
  { name: '住房', items: ['SAH', 'SAH1', 'SEHA', 'SEHC', 'SAH2', 'SAH3'] },
  { name: '交通', items: ['SAT', 'SAT1', 'SETA', 'SETA01', 'SETA02', 'SETG', 'SETG01'] },
  { name: '医疗', items: ['SAM', 'SAM1', 'SAM2'] },
  { name: '商品服务', items: ['SAC', 'SACL1E', 'SAS', 'SASLE'] },
  { name: '其他', items: ['SAA', 'SAR', 'SAE', 'SAE1', 'SAE2', 'SAG'] },
]
const PPI_PARENTS = [
  { name: '总览', ids: ['WPSFD4', 'WPSFD49104', 'WPSFD49116'] },
  { name: '需求结构', ids: ['WPSFD411', 'WPSFD42', 'WPSFD43'] },
  { name: '食品能源', ids: ['WPSFD412', 'WPSFD413'] },
]
const PCE_PARENTS = [
  { name: '总览', ids: ['DPCERG-M', 'DPCCRG-M', 'IA001176-M', 'IA001260-M'] },
  { name: '商品服务', ids: ['DGDSRG-M', 'DSERRG-M', 'DDURRG-M', 'DNDGRG-M'] },
  { name: '分项', ids: ['DFXARG-M', 'DNRGRG-M', 'DHUTRG-M', 'DHLCRG-M'] },
  { name: '市场法', ids: ['DPCMRG-M', 'DPCXRG-M'] },
]
function ym(d = new Date()) {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`
}

function yearStart(year = new Date().getFullYear()) {
  return `${year}-01`
}

function monthLabel(ymStr) {
  if (!ymStr) return '—'
  const [y, m] = ymStr.slice(0, 7).split('-')
  return `${y}年${Number(m)}月`
}

function rangeSpanText(pts) {
  if (!pts?.length) return ''
  const a = pts[0].date.slice(0, 7)
  const b = pts.at(-1).date.slice(0, 7)
  if (a === b) return monthLabel(a)
  const [y1, m1] = a.split('-')
  const [y2, m2] = b.split('-')
  if (y1 === y2) return `${y1}年${Number(m1)}–${Number(m2)}月`
  return `${monthLabel(a)} – ${monthLabel(b)}`
}

const NFP_TOTAL = 'CES0000000001'
const NFP_PRIVATE = 'CES0500000001'
const NFP_GOVT = 'CES9000000001'

function nfpWan(thousands, signed = true) {
  if (thousands == null || Number.isNaN(Number(thousands))) return '—'
  const wan = Number(thousands) / 10
  const body = `${Math.abs(wan).toFixed(1)}万`
  if (wan > 0) return signed ? `+${body}` : body
  if (wan < 0) return `-${body}`
  return '0.0万'
}

function nfpJobs(thousands) {
  if (thousands == null || Number.isNaN(Number(thousands))) return '—'
  const n = Number(thousands)
  if (Math.abs(n) >= 10000) return `${(n / 10000).toFixed(2)}亿`
  return `${(n / 10).toFixed(1)}万`
}

const RANGES = [
  { key: 'latest', label: '最近一期' },
  { key: 'ytd', label: '今年' },
  { key: '5y', label: '近 5 年' },
  { key: '10y', label: '近 10 年' },
  { key: '20y', label: '近 20 年' },
  { key: 'all', label: '1960 以来' },
]
const METRICS = {
  cpi: [
    { key: 'yoy', label: '同比 %' },
    { key: 'mom', label: '环比 %' },
    { key: 'value', label: '指数' },
  ],
  ppi: [
    { key: 'yoy', label: '同比 %' },
    { key: 'mom', label: '环比 %' },
    { key: 'value', label: '指数' },
  ],
  pce: [
    { key: 'yoy', label: '同比 %' },
    { key: 'mom', label: '环比 %' },
    { key: 'value', label: '指数' },
  ],
  nfp: [
    { key: 'mom', label: '月增（万人）' },
    { key: 'value', label: '就业人数' },
    { key: 'yoy', label: '同比（万人）' },
  ],
  pmi: [{ key: 'value', label: '指数' }],
}

const tab = computed(() => props.tab || 'cpi')
const range = ref('ytd')
const start = ref(yearStart())
const end = ref(ym())
const metric = ref('yoy')
const loading = ref(false)
const error = ref('')
const payload = ref(null)
const selected = ref([])
const query = ref('')
const extra = ref({})
const openParents = ref({})

watch(tab, (key) => {
  metric.value = (METRICS[key] || METRICS.cpi)[0].key
  selected.value = []
  extra.value = {}
  query.value = ''
  openParents.value = {}
  load()
  if (key === 'nfp') loadNfpAll()
}, { immediate: true })

async function load() {
  loading.value = true
  error.value = ''
  try {
    const data = await api.macro(tab.value)
    payload.value = data
    selected.value = data.default_ids?.length ? [...data.default_ids] : (data.series?.[0] ? [data.series[0].id] : [])
    openSelectedParents(data.series || [], selected.value)
  } catch (e) {
    payload.value = null
    error.value = e.message
  } finally {
    loading.value = false
  }
}

async function loadNfpAll() {
  try {
    const data = await api.macro('nfp', { detail: true })
    if (tab.value !== 'nfp' || !payload.value) return
    const core = new Map((payload.value.series || []).map((row) => [row.id, row]))
    payload.value = {
      ...data,
      series: (data.series || []).map((row) => core.get(row.id) || row),
    }
    openSelectedParents(payload.value.series, selected.value)
  } catch {
    /* 行业全表失败时，核心行业图表仍可用 */
  }
}

async function ensurePoints(ids) {
  const missing = ids.filter((id) => {
    const row = (payload.value?.series || []).find((item) => item.id === id)
    return row && !(row.points && row.points.length)
  })
  if (!missing.length) return
  const pack = await api.macroHistory(tab.value, missing)
  const got = new Map((pack.series || []).map((row) => [row.id, row]))
  extra.value = { ...extra.value, ...Object.fromEntries(got) }
}

watch(selected, (ids) => {
  if (ids.length) ensurePoints(ids).catch(() => {})
}, { deep: true })

const seriesMap = computed(() => {
  const map = new Map()
  for (const row of payload.value?.series || []) map.set(row.id, row)
  Object.values(extra.value).forEach((row) => map.set(row.id, row))
  return map
})

const picked = computed(() => selected.value.map((id) => seriesMap.value.get(id)).filter(Boolean))

const latestDataMonth = computed(() => {
  let max = ''
  const scan = (row) => {
    if (!row) return
    const hit = (row.latest?.date || row.end || '').slice(0, 7)
    if (hit > max) max = hit
    for (const p of row.points || []) {
      const d = (p.date || '').slice(0, 7)
      if (d > max) max = d
    }
  }
  for (const row of payload.value?.series || []) scan(row)
  Object.values(extra.value).forEach(scan)
  return max
})

function applyRange(key) {
  range.value = key
  if (key === 'latest') {
    const m = latestDataMonth.value || ym()
    start.value = m
    end.value = m
    return
  }
  const now = new Date()
  end.value = ym(now)
  if (key === 'ytd') start.value = yearStart(now.getFullYear())
  else if (key === 'all') start.value = '1960-01'
  else if (key === '5y') start.value = ym(new Date(now.getFullYear() - 5, now.getMonth(), 1))
  else if (key === '10y') start.value = ym(new Date(now.getFullYear() - 10, now.getMonth(), 1))
  else if (key === '20y') start.value = ym(new Date(now.getFullYear() - 20, now.getMonth(), 1))
}

function onStartEnd() {
  range.value = 'custom'
  if (start.value && end.value && start.value > end.value) {
    const swap = start.value
    start.value = end.value
    end.value = swap
  }
}

watch(latestDataMonth, (m) => {
  if (range.value === 'latest' && m) {
    start.value = m
    end.value = m
  }
})

const windowStart = computed(() => start.value || '1960-01')
const windowEnd = computed(() => end.value || ym())
const singleMonth = computed(() => windowStart.value === windowEnd.value)

function metricValue(point) {
  if (!point) return null
  const key = metric.value
  if (key === 'value') return point.value
  return point[key]
}

function pointInWindow(row) {
  return (row.points || []).filter((p) => {
    const m = p.date.slice(0, 7)
    return m >= windowStart.value && m <= windowEnd.value
  })
}

function pointAt(row, month) {
  return (row?.points || []).find((p) => p.date.slice(0, 7) === month) || null
}

function seriesPeriod(row) {
  const live = liveRow(row)
  if (!live) return null
  const pts = [...(live.points || [])].sort((a, b) => a.date.localeCompare(b.date))
  const inWin = pts.filter((p) => {
    const m = p.date.slice(0, 7)
    return m >= windowStart.value && m <= windowEnd.value
  })
  const last = inWin.at(-1) || (live.latest && live.latest.date?.slice(0, 7) >= windowStart.value && live.latest.date?.slice(0, 7) <= windowEnd.value ? live.latest : null)
  if (!last) return null
  if (tab.value === 'nfp') {
    const prev = [...pts].reverse().find((p) => p.date.slice(0, 7) < windowStart.value)
    const base = prev?.value
    const delta = base != null ? last.value - base : (inWin.length >= 2 ? last.value - inWin[0].value : last.mom)
    if (delta == null || Number.isNaN(Number(delta))) return null
    return { n: delta, text: nfpWan(delta), unit: '区间净增' }
  }
  if (tab.value === 'pmi') {
    if (last.value == null) return null
    return { n: last.value - 50, text: Number(last.value).toFixed(1), unit: '区间末' }
  }
  const n = metric.value === 'mom' ? last.mom : metric.value === 'value' ? last.value : last.yoy
  if (n == null || Number.isNaN(Number(n))) return null
  if (metric.value === 'value') return { n, text: Number(n).toFixed(2), unit: '区间末' }
  return { n, text: fmtPct(n), unit: metric.value === 'mom' ? '区间末环比' : '区间末同比' }
}

function seriesLatest(row) {
  const live = liveRow(row)
  if (!live) return null
  if (live.latest) return live.latest
  const pts = [...(live.points || [])].sort((a, b) => a.date.localeCompare(b.date))
  return pts.at(-1) || null
}

function nodeLatestRow(node) {
  if (node.row) return node.row
  const head = node.children.find((c) => /^(总\s|总P|ISM)/.test(c.name) || c.name.includes('总计'))
  return head || node.children[0] || null
}

function latestTagText(row) {
  const p = seriesLatest(row)
  if (!p) return ''
  const when = monthLabel(p.date)
  if (tab.value === 'nfp') return `最近一期 ${when} ${nfpWan(p.mom)}`
  if (tab.value === 'pmi') return `最近一期 ${when} ${p.value?.toFixed(1) ?? '—'}`
  if (metric.value === 'mom') return `最近一期 ${when} ${fmtPct(p.mom)}`
  if (metric.value === 'value') return `最近一期 ${when} ${p.value == null ? '—' : Number(p.value).toFixed(2)}`
  return `最近一期 ${when} ${fmtPct(p.yoy)}`
}

function nodePeriod(node) {
  if (node.row) return seriesPeriod(node.row)
  if (tab.value === 'nfp' && node.children.length) {
    const maxLv = Math.max(...node.children.map((c) => c.level || 0))
    const tops = node.children.filter((c) => (c.level || 0) === maxLv)
    let sum = 0
    let ok = 0
    for (const child of tops) {
      const stat = seriesPeriod(child)
      if (stat?.n == null) continue
      sum += stat.n
      ok += 1
    }
    if (!ok) return null
    return { n: sum, text: nfpWan(sum), unit: '区间净增' }
  }
  const head = node.children.find((c) => /^(总\s|总P|ISM)/.test(c.name) || c.name.includes('总计'))
  if (head) return seriesPeriod(head)
  return null
}

function statTone(stat) {
  if (!stat || stat.n == null) return ''
  if (stat.n > 0) return 'up'
  if (stat.n < 0) return 'down'
  return ''
}

function fmtNfpTip(value) {
  if (value == null) return '—'
  if (metric.value === 'value') return nfpJobs(value)
  return `${nfpWan(value)}人`
}

function nfpAxisFmt(value) {
  if (metric.value === 'value') {
    if (Math.abs(value) >= 10000) return `${(value / 10000).toFixed(1)}亿`
    return `${(value / 10).toFixed(0)}万`
  }
  return (value / 10).toFixed(1)
}

const nfpStory = computed(() => {
  if (tab.value !== 'nfp') return null
  const totalRow = seriesMap.value.get(NFP_TOTAL)
  const pts = pointInWindow(totalRow || { points: [] })
  const total = pts.at(-1)
  if (!total) return null
  const month = total.date.slice(0, 7)
  const priv = pointAt(seriesMap.value.get(NFP_PRIVATE), month)
  const gov = pointAt(seriesMap.value.get(NFP_GOVT), month)
  const impliedGov = total.mom != null && priv?.mom != null ? total.mom - priv.mom : null
  return {
    month,
    monthText: monthLabel(month),
    span: rangeSpanText(pts),
    period: seriesPeriod(totalRow),
    total,
    priv,
    gov,
    govMom: gov?.mom ?? impliedGov,
    govImplied: !gov && impliedGov != null,
  }
})

const inflationStory = computed(() => {
  const spec = INFLATION_HEAD[tab.value]
  if (!spec) return null
  const total = pointInWindow(seriesMap.value.get(spec.total) || { points: [] }).at(-1)
  if (!total) return null
  const month = total.date.slice(0, 7)
  const core = pointAt(seriesMap.value.get(spec.core), month)
  return {
    label: spec.label,
    monthText: monthLabel(month),
    total,
    core,
    hint: tab.value === 'ppi'
      ? 'PPI 是 CPI 的上游。环比意外偏高时，市场会重新定价降息，空头容易被数据打脸。'
      : tab.value === 'pce'
        ? 'PCE 是联储真正盯的通胀。核心 PCE 目标约 2%。比 CPI 晚约两周，但口径更贴近消费结构。'
        : 'CPI 是市场盘中交易最多的通胀。核心看除食品能源。',
  }
})

const chartOption = computed(() => {
  const rows = picked.value
  if (!rows.length) return null
  const dates = [...new Set(rows.flatMap((row) => (row.points || []).map((p) => p.date)))]
    .filter((d) => d.slice(0, 7) >= windowStart.value && d.slice(0, 7) <= windowEnd.value)
    .sort()
  const snapshot = tab.value === 'nfp' && (singleMonth.value || dates.length <= 1)
  const nfpScale = tab.value === 'nfp'
  const nfpChange = nfpScale && metric.value !== 'value'
  const useBar = snapshot || (tab.value === 'nfp' && metric.value === 'mom' && dates.length <= 24)
  const axisStyle = {
    backgroundColor: 'transparent',
    color: COLORS,
    tooltip: {
      trigger: snapshot ? 'item' : 'axis',
      backgroundColor: '#101820',
      borderColor: 'rgba(148,176,210,0.2)',
      textStyle: { color: '#e9f0f7' },
      formatter: (params) => {
        const items = Array.isArray(params) ? params : [params]
        const head = snapshot
          ? monthLabel(windowEnd.value)
          : (items[0]?.axisValue || items[0]?.name || '')
        const lines = items.map((item) => {
          const name = snapshot ? (item.name || '') : item.seriesName
          const text = nfpScale ? fmtNfpTip(item.value) : (item.value ?? '—')
          return `${item.marker || ''}${name}　${text}`
        })
        return [head, ...lines].join('<br/>')
      },
    },
    grid: { left: nfpScale ? 64 : 58, right: 18, top: 42, bottom: snapshot ? 72 : 36 },
    yAxis: {
      type: 'value',
      name: nfpChange ? '万人' : undefined,
      nameTextStyle: { color: '#8b9bb0' },
      splitLine: { lineStyle: { color: 'rgba(148,176,210,0.08)' } },
      axisLabel: { color: '#8b9bb0', formatter: nfpScale ? nfpAxisFmt : undefined },
    },
  }
  if (snapshot) {
    const month = windowEnd.value
    return {
      ...axisStyle,
      xAxis: {
        type: 'category',
        data: rows.map((row) => row.name),
        axisLine: { lineStyle: { color: 'rgba(148,176,210,0.2)' } },
        axisLabel: {
          color: '#8b9bb0',
          interval: 0,
          rotate: rows.length > 3 ? 28 : 0,
        },
      },
      series: [{
        type: 'bar',
        barMaxWidth: 52,
        data: rows.map((row) => {
          const value = metricValue(pointAt(row, month))
          return {
            value,
            itemStyle: { color: value == null ? '#627084' : value >= 0 ? '#3ee0a0' : '#ff6b7a' },
          }
        }),
      }],
    }
  }
  return {
    ...axisStyle,
    legend: { type: 'scroll', top: 0, textStyle: { color: '#8b9bb0' } },
    xAxis: {
      type: 'category',
      data: dates,
      axisLine: { lineStyle: { color: 'rgba(148,176,210,0.2)' } },
      axisLabel: { color: '#8b9bb0', formatter: (v) => v.slice(0, 7) },
    },
    series: rows.map((row, idx) => {
      const byDate = new Map((row.points || []).map((p) => [p.date, metricValue(p)]))
      const data = dates.map((d) => byDate.get(d) ?? null)
      if (useBar) {
        return {
          name: row.name,
          type: 'bar',
          barMaxWidth: rows.length === 1 ? 28 : 18,
          data: data.map((v) => ({
            value: v,
            itemStyle: rows.length === 1
              ? { color: v == null ? '#627084' : v >= 0 ? '#3ee0a0' : '#ff6b7a' }
              : undefined,
          })),
        }
      }
      return {
        name: row.name,
        type: 'line',
        showSymbol: dates.length <= 24,
        symbolSize: dates.length <= 3 ? 12 : 6,
        data,
        lineStyle: { width: 2, color: COLORS[idx % COLORS.length] },
        itemStyle: { color: COLORS[idx % COLORS.length] },
        markLine: tab.value === 'pmi' && idx === 0 ? {
          silent: true,
          symbol: 'none',
          lineStyle: { color: '#8b9bb0', type: 'dashed' },
          data: [{ yAxis: 50, label: { formatter: '荣枯线 50', color: '#8b9bb0' } }],
        } : tab.value === 'pce' && metric.value === 'yoy' && idx === 0 ? {
          silent: true,
          symbol: 'none',
          lineStyle: { color: '#8b9bb0', type: 'dashed' },
          data: [{ yAxis: 2, label: { formatter: '联储目标 2%', color: '#8b9bb0' } }],
        } : undefined,
      }
    }),
  }
})

const cards = computed(() => picked.value.slice(0, 4).map((row) => {
  const pts = pointInWindow(row)
  return {
    ...row,
    pts,
    last: pts.at(-1) || null,
    period: seriesPeriod(row),
    span: rangeSpanText(pts),
  }
}))

const CPI_TOP = new Set([
  'SA0', 'SA0L1E', 'SAF1', 'SA0E', 'SAH', 'SAH1', 'SEHA', 'SEHC', 'SAA', 'SAT',
  'SAM', 'SAR', 'SAE', 'SAG', 'SETB01', 'SETA01', 'SETA02',
])

function rowText(row) {
  return `${row.name || ''} ${row.id || ''} ${row.supersector_name || ''} ${row.item || ''}`
}

function parentOf(row) {
  if (tab.value === 'nfp') return row.supersector_name || '其他'
  if (tab.value === 'pmi') {
    if (row.kind === 'headline' || (row.name || '').includes('PMI')) return '总览'
    if ((row.name || '').includes('制造') || (row.id || '').includes('man')) return '制造业'
    return '服务业'
  }
  if (tab.value === 'cpi') {
    const hit = CPI_PARENTS.find((g) => g.items.includes(row.item))
    return hit?.name || '其他'
  }
  if (tab.value === 'ppi') {
    const hit = PPI_PARENTS.find((g) => g.ids.includes(row.id))
    return hit?.name || '其他'
  }
  if (tab.value === 'pce') {
    const hit = PCE_PARENTS.find((g) => g.ids.includes(row.id))
    return hit?.name || '其他'
  }
  return row.featured || CPI_TOP.has(row.item) ? '常用分类' : '全部分类'
}

function buildTree(rows) {
  const buckets = new Map()
  for (const row of rows) {
    const key = parentOf(row)
    if (!buckets.has(key)) buckets.set(key, [])
    buckets.get(key).push(row)
  }
  return [...buckets.entries()].map(([name, items]) => {
    const sorted = [...items].sort((a, b) => (b.level || 0) - (a.level || 0) || a.name.localeCompare(b.name, 'zh'))
    const parentRow = tab.value === 'nfp'
      ? sorted.find((row) => row.name === name) || (sorted[0]?.level > 0 ? sorted[0] : null)
      : null
    const children = parentRow ? sorted.filter((row) => row.id !== parentRow.id) : sorted
    return {
      key: name,
      name,
      id: parentRow?.id || null,
      row: parentRow,
      children,
    }
  })
}

const industryTree = computed(() => {
  const q = query.value.trim().toLowerCase()
  const rows = (payload.value?.series || []).filter((row) => !q || rowText(row).toLowerCase().includes(q))
  const tree = buildTree(rows)
  if (!q) return tree
  return tree.filter((node) => (
    node.name.toLowerCase().includes(q)
    || node.children.length
    || (node.row && rowText(node.row).toLowerCase().includes(q))
  ))
})

watch([industryTree, start, end, openParents], () => {
  const ids = []
  for (const node of industryTree.value) {
    if (node.id) ids.push(node.id)
    if (query.value.trim() || openParents.value[node.key]) {
      node.children.forEach((row) => ids.push(row.id))
    }
  }
  if (ids.length) ensurePoints(ids).catch(() => {})
})

function openSelectedParents(series, ids) {
  const wanted = new Set(ids || selected.value)
  const next = {}
  for (const node of buildTree(series || payload.value?.series || [])) {
    if (wanted.has(node.id) || node.children.some((row) => wanted.has(row.id))) next[node.key] = true
  }
  openParents.value = next
}

function isOpen(key) {
  if (query.value.trim()) return true
  return !!openParents.value[key]
}

function toggleOpen(key) {
  openParents.value = { ...openParents.value, [key]: !openParents.value[key] }
}

function liveRow(row) {
  if (!row) return null
  return extra.value[row.id] || row
}

function onParentClick(node) {
  if (node.children.length && !isOpen(node.key)) toggleOpen(node.key)
  if (node.id) toggle(node.id)
  else toggleOpen(node.key)
}

function toggle(id) {
  if (selected.value.includes(id)) {
    if (selected.value.length === 1) return
    selected.value = selected.value.filter((x) => x !== id)
    return
  }
  selected.value = [...selected.value, id]
}

function fmtPct(n) {
  if (n == null || Number.isNaN(Number(n))) return '—'
  const value = Number(n)
  const sign = value > 0 ? '+' : ''
  return `${sign}${value.toFixed(2)}%`
}

function fmt(row, last) {
  if (!last) return tab.value === 'nfp' && singleMonth.value ? '该月未公布' : '—'
  if (tab.value === 'pmi') return last.value?.toFixed(1)
  if (tab.value === 'nfp') {
    if (metric.value === 'value') return nfpJobs(last.value)
    if (metric.value === 'yoy') return nfpWan(last.yoy)
    return nfpWan(last.mom)
  }
  if (INFLATION_TABS.has(tab.value)) {
    if (metric.value === 'value') return last.value == null ? '—' : Number(last.value).toFixed(2)
    if (metric.value === 'mom') return fmtPct(last.mom)
    return fmtPct(last.yoy)
  }
  return fmtPct(last.yoy)
}

function cardMain(card) {
  if (tab.value === 'nfp') {
    if (metric.value === 'value') return card.last ? nfpJobs(card.last.value) : '—'
    return card.period?.text || '—'
  }
  return fmt(card, card.last)
}

function latestText(card) {
  if (!card.last) return '—'
  if (tab.value === 'pmi') return card.last.value?.toFixed(1) ?? '—'
  if (tab.value === 'nfp') return nfpWan(card.last.mom)
  if (metric.value === 'mom') return fmtPct(card.last.mom)
  if (metric.value === 'value') return card.last.value == null ? '—' : Number(card.last.value).toFixed(2)
  return fmtPct(card.last.yoy)
}

function cardSub(card) {
  if (!card.last) return singleMonth.value ? `${monthLabel(windowEnd.value)} · 该月未公布` : '—'
  if (tab.value === 'nfp') {
    const jobs = nfpJobs(card.last.value)
    const span = card.span || rangeSpanText(card.pts || [])
    if (metric.value === 'value') return `${span}末就业 · ${jobs}`
    return `${span}合计 · 就业 ${jobs}`
  }
  if (INFLATION_TABS.has(tab.value)) {
    return `${monthLabel(card.last.date)} · 同比 ${fmtPct(card.last.yoy)} · 环比 ${fmtPct(card.last.mom)}`
  }
  return card.last.date || '—'
}

function cardTone(card) {
  if (tab.value === 'nfp' && metric.value !== 'value') return statTone(card.period)
  return tone(card.last)
}

function tone(last) {
  if (!last) return ''
  const n = tab.value === 'nfp'
    ? last.mom
    : tab.value === 'pmi'
      ? last.value - 50
      : (metric.value === 'mom' ? last.mom : last.yoy)
  if (n > 0) return 'up'
  if (n < 0) return 'down'
  return ''
}
</script>

<template>
  <div class="macro-layout">
    <aside class="macro-tree">
      <div class="macro-filters">
        <div class="macro-dates">
          <label>
            Start
            <input v-model="start" type="month" min="1960-01" :max="end || ym()" @change="onStartEnd" />
          </label>
          <label>
            End
            <input v-model="end" type="month" :min="start || '1960-01'" :max="ym()" @change="onStartEnd" />
          </label>
        </div>
        <div class="pills">
          <button
            v-for="item in RANGES"
            :key="item.key"
            class="pill"
            :class="{ active: range === item.key }"
            @click="applyRange(item.key)"
          >{{ item.label }}</button>
        </div>
      </div>
      <h2>行业</h2>
      <p class="sub tree-hint">
        {{ tab === 'nfp' ? '名称上方是所选区间净增人数' : '名称上方是所选区间末的读数' }}
        <span v-if="latestDataMonth" class="latest-tag">最近一期 {{ monthLabel(latestDataMonth) }}</span>
      </p>
      <input
        v-model="query"
        class="macro-search"
        :placeholder="tab === 'nfp' ? '搜索，例如 制造 / retail' : '搜索分类'"
      />
      <div v-if="loading" class="sub">正在拉取…</div>
      <div v-else-if="!industryTree.length" class="sub">没有匹配的行业</div>
      <div v-for="node in industryTree" :key="node.key" class="tree-node">
        <div class="tree-row parent" :class="{ now: node.id && selected.includes(node.id), open: isOpen(node.key) }">
          <button
            v-if="node.children.length"
            class="tree-caret"
            type="button"
            @click="toggleOpen(node.key)"
          >{{ isOpen(node.key) ? '▾' : '▸' }}</button>
          <span v-else class="tree-caret"></span>
          <button class="tree-name" type="button" @click="onParentClick(node)">
            <b v-if="nodePeriod(node)" class="tree-stat" :class="statTone(nodePeriod(node))">{{ nodePeriod(node).text }}</b>
            <span>{{ node.name }}</span>
            <em v-if="latestTagText(nodeLatestRow(node))" class="latest-tag">{{ latestTagText(nodeLatestRow(node)) }}</em>
          </button>
        </div>
        <div v-if="node.children.length && isOpen(node.key)" class="tree-children">
          <button
            v-for="child in node.children"
            :key="child.id"
            class="tree-row child"
            :class="{ now: selected.includes(child.id) }"
            type="button"
            @click="toggle(child.id)"
          >
            <b v-if="seriesPeriod(child)" class="tree-stat" :class="statTone(seriesPeriod(child))">{{ seriesPeriod(child).text }}</b>
            <span>{{ child.name }}</span>
            <em v-if="latestTagText(child)" class="latest-tag">{{ latestTagText(child) }}</em>
          </button>
        </div>
      </div>
    </aside>

    <div class="macro-main">
      <p class="goal-lead">
        PPI 是 CPI 上游，PCE 是联储口径。左侧点父行业展开子行业，点名称叠加到图上。
      </p>
      <div class="range-row">
        <div class="sub" v-if="loading">正在拉取历史序列…</div>
        <div class="error" v-else-if="error">{{ error }}</div>
        <div class="sub" v-else-if="payload">{{ payload.source }}</div>
      </div>

      <template v-if="payload">
        <div class="macro-cards">
          <div class="stat" v-for="card in cards" :key="card.id">
            <span>{{ card.name }}</span>
            <b :class="cardTone(card)">{{ cardMain(card) }}</b>
            <em>{{ cardSub(card) }}</em>
            <span v-if="card.last" class="latest-tag">最近一期 {{ monthLabel(card.last.date) }} {{ latestText(card) }}</span>
          </div>
        </div>

        <div v-if="inflationStory" class="nfp-story">
          <p>
            <strong>{{ inflationStory.monthText }}</strong>
            {{ inflationStory.label }} 同比
            <b :class="tone(inflationStory.total)">{{ fmtPct(inflationStory.total.yoy) }}</b>
            ，环比
            <b :class="inflationStory.total.mom > 0 ? 'up' : inflationStory.total.mom < 0 ? 'down' : ''">{{ fmtPct(inflationStory.total.mom) }}</b>。
            <template v-if="inflationStory.core">
              核心同比
              <b :class="tone(inflationStory.core)">{{ fmtPct(inflationStory.core.yoy) }}</b>
              ，环比 {{ fmtPct(inflationStory.core.mom) }}。
            </template>
          </p>
          <p class="sub">{{ inflationStory.hint }}</p>
        </div>

        <div v-if="nfpStory" class="nfp-story">
          <p>
            <strong>{{ nfpStory.span }}合计</strong>
            非农总计
            <b :class="statTone(nfpStory.period)">{{ nfpStory.period?.text || '—' }}人</b>
            。最近一期 {{ nfpStory.monthText }}
            <b :class="tone(nfpStory.total)">{{ nfpWan(nfpStory.total.mom) }}</b>
            ：私营
            <b :class="tone(nfpStory.priv)">{{ nfpWan(nfpStory.priv?.mom) }}</b>，
            政府
            <b :class="nfpStory.govMom > 0 ? 'up' : nfpStory.govMom < 0 ? 'down' : ''">{{ nfpWan(nfpStory.govMom) }}</b>
            <template v-if="nfpStory.govImplied">（用总计 − 私营推算，政府序列该月尚未公布）</template>。
          </p>
          <p class="sub">
            读法：总计 = 私营 + 政府。卡片大数是左侧区间合计，标签是最近一期。
          </p>
          <p v-if="singleMonth" class="sub nfp-story-actions">
            Start 和 End 都是 {{ nfpStory.monthText }}，下面是该月拆分，不是走势。
            <button class="pill" type="button" @click="applyRange('ytd')">看今年走势</button>
          </p>
        </div>

        <section class="panel">
          <div class="range-row">
            <div class="pills">
              <button
                v-for="item in METRICS[tab]"
                :key="item.key"
                class="pill"
                :class="{ active: metric === item.key }"
                @click="metric = item.key"
              >{{ item.label }}</button>
            </div>
          </div>
          <MacroChart v-if="chartOption" :option="chartOption" />
        </section>
      </template>
    </div>
  </div>
</template>
