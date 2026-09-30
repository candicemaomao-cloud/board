<script setup>
import { computed, reactive, ref, watch } from 'vue'
import { api } from '../api'
import MacroChart from './MacroChart.vue'
import StockOptionsSummary from './StockOptionsSummary.vue'
import StockEntryAnalysis from './StockEntryAnalysis.vue'

const props = defineProps({ stock: { type: Object, required: true } })
const emit = defineEmits(['back', 'more-financials'])
const sections = [
  ['financials', '财务摘要'], ['price', '价格走势'], ['options', '期权'],
  ['regression', '回归线'], ['news', '近期新闻'], ['events', '下一事件'],
]
const data = reactive({}), loading = reactive({}), errors = reactive({})
const notes = ref([]), draft = ref(''), saving = ref(false), noteError = ref(''), saved = ref(false)
const period = ref(126)
const canRecordAnalysis = computed(() => Number.isInteger(Number(props.stock.id)) && Number(props.stock.id) > 0)
let generation = 0
function num(v) { return v == null || !Number.isFinite(Number(v)) ? '—' : Number(v).toLocaleString('zh-CN', { maximumFractionDigits: 2 }) }
function time(v) { return v ? new Date(v).toLocaleString('zh-CN') : '—' }
async function load(section) {
  const version = generation, id = props.stock.id, symbol = props.stock.symbol
  if (section === 'notes' && !id) return
  loading[section] = true; errors[section] = ''
  try {
    const result = section === 'notes'
      ? await api.stockAnalysisNotes(id)
      : (id ? await api.stockDetail(id, section) : await api.stockDetailBySymbol(symbol, section))
    if (version !== generation) return
    if (section === 'notes') notes.value = [...new Map([...notes.value, ...result.items].map(note => [note.id, note])).values()].sort((a, b) => b.id - a.id)
    else data[section] = result
  } catch (e) { if (version === generation) errors[section] = e.message }
  finally { if (version === generation) loading[section] = false }
}
watch(() => [props.stock.id, props.stock.symbol], () => {
  generation++
  Object.keys(data).forEach(k => delete data[k])
  notes.value = []; draft.value = ''; saved.value = false
  sections.forEach(([key]) => load(key))
  if (canRecordAnalysis.value) load('notes')
}, { immediate: true })
async function save() {
  if (!props.stock.id || !draft.value.trim() || saving.value) return
  saving.value = true; noteError.value = ''; saved.value = false
  try {
    const result = await api.addStockAnalysisNote(props.stock.id, draft.value)
    notes.value.unshift(result); draft.value = ''; saved.value = true
  } catch (e) { noteError.value = e.message }
  finally { saving.value = false }
}
function addAnalysisDraft(text) {
  draft.value = [draft.value.trim(), text].filter(Boolean).join('\n\n')
  saved.value = false
  document.getElementById('stock-notes')?.scrollIntoView({ behavior: 'smooth' })
}
function onEntryPriceSaved(updated) {
  Object.assign(props.stock, updated)
}
function back() {
  if (draft.value.trim() && !confirm('分析记录尚未保存，确定返回？')) return
  emit('back')
}
function chart(dates, series) {
  return {
    tooltip: { trigger: 'axis' }, legend: { textStyle: { color: '#a9b8cc' } },
    grid: { left: 65, right: 24, top: 38, bottom: 60 },
    xAxis: { type: 'category', data: dates, axisLabel: { color: '#8b9bb0' } },
    yAxis: { type: 'value', scale: true, axisLabel: { color: '#8b9bb0' }, splitLine: { lineStyle: { color: '#263344' } } },
    dataZoom: [{ type: 'inside' }, { type: 'slider', height: 18, bottom: 8 }],
    series: series.map(([name, values, color]) => ({ name, data: values, type: 'line', showSymbol: false, itemStyle: { color } })),
  }
}
const bars = computed(() => (data.price?.ohlc_bars || []).slice(-period.value))
const priceChart = computed(() => chart(bars.value.map(b => new Date(b.ts * 1000).toISOString().slice(0, 10)), [['收盘价', bars.value.map(b => b.close), '#82a5ff']]))
const regressionChart = computed(() => chart(data.regression?.dates || [], [['收盘价', data.regression?.closes || [], '#82a5ff'], ['稳健回归线', data.regression?.regression || [], '#3ecf8e']]))
const financial = computed(() => data.financials?.quarter)
const company = computed(() => data.financials?.company || {})
const valuation = computed(() => company.value.valuation || {})
const financialNotes = computed(() => data.financials?.notes || [])
function usd(value, compact = false) {
  if (value == null || value === '' || !Number.isFinite(Number(value))) return '—'
  const n = Number(value), abs = Math.abs(n), sign = n < 0 ? '-' : ''
  if (compact && abs >= 1e9) return `${sign}$${(abs / 1e9).toFixed(1)}B`
  if (compact && abs >= 1e6) return `${sign}$${(abs / 1e6).toFixed(1)}M`
  return `${sign}$${abs.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`
}
const labels = { 'Total Revenue': '营业收入', 'Gross Profit': '毛利润', 'Operating Income': '营业利润', 'Net Income': '净利润' }
</script>

<template>
  <div class="stock-detail">
    <div class="detail-heading">
      <button class="btn" @click="back">← 返回</button>
      <div><h1>{{ stock.symbol }} <small>{{ data.financials?.company?.name || stock.sector }}</small></h1><p class="sub">现价 {{ num(data.price?.price ?? stock.current_price) }} · 列表更新 {{ time(stock.updated_at) }}</p></div>
      <div v-if="$slots.actions" class="detail-actions"><slot name="actions" /></div>
    </div>
    <nav class="detail-nav" aria-label="股票详情章节"><a href="#stock-entry">买卖价分析</a><a v-for="[key, title] in sections" :key="key" :href="`#stock-${key}`">{{ title }}</a><a v-if="canRecordAnalysis" href="#stock-notes">我的分析记录</a></nav>
    <StockEntryAnalysis :stock="stock" :allow-record="canRecordAnalysis" @record="addAnalysisDraft" @price-saved="onEntryPriceSaved" />
    <div class="detail-grid">
      <section v-for="[key, title] in sections" :id="`stock-${key}`" :key="key" class="panel" :class="{ wide: ['financials', 'price', 'options', 'regression'].includes(key) }">
        <div class="section-heading"><h2>{{ title }}</h2><button class="btn btn-ghost" :disabled="loading[key]" @click="load(key)">{{ loading[key] ? '加载中…' : '刷新' }}</button></div>
        <p v-if="errors[key]" class="error" role="alert">{{ errors[key] }}，可点击刷新重试。</p>
        <p v-else-if="loading[key] && !data[key]" class="sub">正在加载 {{ stock.symbol }} 数据…</p>
        <template v-if="data[key]">
          <template v-if="key === 'financials'">
            <div class="financial-company">
              <h3>{{ company.symbol || stock.symbol }} · {{ company.name || '公司名称未提供' }}</h3>
              <p class="sub">{{ [company.exchange, company.sector, company.industry, data.financials.source].filter(Boolean).join(' · ') }}</p>
              <div class="price-grid financial-prices">
                <div class="price-card"><span>现价</span><b>{{ usd(company.price) }}</b></div>
                <div class="price-card"><span>市值</span><b>{{ usd(company.market_cap, true) }}</b></div>
                <div class="price-card"><span>一年目标价</span><b>{{ usd(company.target) }}</b></div>
                <div class="price-card"><span>52 周高 / 低</span><b>{{ company.high_52w != null || company.low_52w != null ? `${usd(company.high_52w)} / ${usd(company.low_52w)}` : company.range_52w || '—' }}</b></div>
                <div class="price-card"><span>PE（TTM）</span><b>{{ num(valuation.trailing_pe) }}</b></div>
                <div class="price-card"><span>预期 PE</span><b>{{ num(valuation.forward_pe) }}</b></div>
                <div class="price-card"><span>EPS（TTM）</span><b>{{ usd(valuation.eps_ttm) }}</b></div>
                <div class="price-card"><span>营收（TTM）</span><b>{{ usd(valuation.revenue_ttm, true) }}</b></div>
                <div class="price-card"><span>自由现金流（TTM）</span><b>{{ usd(valuation.free_cash_flow_ttm, true) }}</b></div>
                <div class="price-card"><span>净利率（TTM）</span><b>{{ valuation.net_margin_ttm == null ? '—' : `${num(valuation.net_margin_ttm)}%` }}</b></div>
              </div>
              <p class="sub">{{ valuation.as_of || 'TTM 数据暂不可用' }}</p>
              <p class="company-about">{{ company.about || '暂无公司简介。' }}</p>
            </div>
            <h3>要点</h3>
            <div v-if="financialNotes.length" class="fund-notes financial-notes">
              <div v-for="item in financialNotes" :key="item.title" class="fund-note" :class="item.tone">
                <span>{{ item.title }}</span><p>{{ item.text }}</p>
              </div>
            </div>
            <p v-else class="sub">暂无可用的财务分析要点。</p>
            <h3>最新季度财务</h3>
            <p class="sub">报告期 {{ financial?.periods?.[0] || '未提供' }} · 金额单位：美元（B 为十亿，M 为百万）</p>
            <div v-if="financial?.income?.length" class="price-grid financial-prices">
              <div v-for="row in financial.income" :key="row.name" class="price-card"><span>{{ labels[row.name] || row.name }}</span><b>{{ usd(row.values?.[0], true) }}</b></div>
            </div>
            <p v-else class="sub">暂无季度财务数据。</p>
            <div class="more-row"><button class="btn" type="button" @click="emit('more-financials', stock.symbol)">更多财报分析 →</button></div>
          </template>
          <template v-else-if="key === 'price'">
            <div class="section-heading"><span class="sub">日线收盘价 · {{ data.price.source }} · 截至 {{ bars.at(-1) ? new Date(bars.at(-1).ts * 1000).toISOString().slice(0, 10) : '—' }}</span><select v-model="period" aria-label="价格走势范围"><option :value="21">近一个月</option><option :value="63">近三个月</option><option :value="126">近六个月</option><option :value="252">近一年</option></select></div>
            <MacroChart v-if="bars.length" :option="priceChart" height="320px" /><p v-else class="sub">暂无价格走势。</p>
          </template>
          <template v-else-if="key === 'options'">
            <StockOptionsSummary :data="data.options" />
          </template>
          <template v-else-if="key === 'regression'">
            <p class="sub">{{ data.regression.sample_start }} — {{ data.regression.sample_end }} · 趋势预测价 {{ num(data.regression.predicted_price) }} · Z-score {{ num(data.regression.z_score) }}</p>
            <p v-if="data.regression.latest_is_event" class="error">最近交易日波动异常，回归信号参考价值降低。</p>
            <MacroChart v-if="data.regression.dates?.length" :option="regressionChart" height="300px" /><p class="sub">稳健回归反映历史价格趋势。</p>
          </template>
          <template v-else-if="key === 'news'">
            <p class="sub">{{ data.news.source }} · 最近 {{ data.news.lookback_days || 30 }} 天 · 按发布时间排序</p>
            <p v-if="data.news.unavailable_sources?.length" class="sub">部分来源暂不可用，已展示其他来源的结果。</p>
            <article v-for="item in data.news.items" :key="item.url" class="detail-item"><a :href="item.url" target="_blank" rel="noopener noreferrer">{{ item.title }}</a><p class="sub">{{ item.source }} · {{ time(item.published_at) }}</p></article><p v-if="!data.news.items?.length" class="sub">暂无该股票的近期新闻。</p>
          </template>
          <template v-else-if="key === 'events'">
            <article v-for="(item, i) in data.events.items" :key="i" class="detail-item"><strong>{{ item.date }}</strong><p>{{ item.title }}</p></article><p v-if="!data.events.items?.length" class="sub">暂无已公布的下一事件。</p><p class="sub">{{ data.events.source }}</p>
          </template>
        </template>
        <template v-if="key === 'events' && stock.events"><h3>列表中的事件备注</h3><p class="preserve">{{ stock.events }}</p></template>
      </section>
      <section v-if="canRecordAnalysis" id="stock-notes" class="panel wide">
        <div class="notes-heading">
          <div>
            <h2>我的分析记录</h2>
            <p class="sub">仅自己可见，按股票保存。记录判断依据、关键价位和后续验证结果。</p>
          </div>
          <span class="private-badge">
            <svg viewBox="0 0 20 20" aria-hidden="true"><path d="M6.5 9V6.8a3.5 3.5 0 0 1 7 0V9M5 9h10v8H5z" /></svg>
            私密记录
          </span>
        </div>
        <form class="note-editor" @submit.prevent="save">
          <div class="note-input-shell" :class="{ disabled: saving }">
            <textarea
              v-model="draft"
              aria-label="我的分析记录"
              placeholder="记录你的判断依据、关键价位、风险条件和交易计划…"
              maxlength="20000"
              rows="6"
              :disabled="saving"
            ></textarea>
            <div class="note-editor-footer">
              <span class="note-hint">支持多行文本 · 买卖价分析结果也可追加到这里</span>
              <span class="note-count" :class="{ warning: draft.length > 18000 }">{{ draft.length.toLocaleString() }} / 20,000</span>
            </div>
          </div>
          <div class="note-actions">
            <span class="sub">保存后会添加为一条新的历史记录</span>
            <button class="btn btn-primary note-save" :disabled="saving || !draft.trim()">
              <svg v-if="!saving" viewBox="0 0 20 20" aria-hidden="true"><path d="M4 3h10l2 2v12H4zM7 3v5h6V3M7 13h6" /></svg>
              {{ saving ? '保存中…' : '保存分析' }}
            </button>
          </div>
        </form>
        <p v-if="noteError" class="error" role="alert">{{ noteError }}</p><p v-if="saved" role="status" class="up">分析记录已保存。</p>
        <p v-if="loading.notes" class="sub">正在加载历史记录…</p><p v-if="errors.notes" class="error">{{ errors.notes }} <button class="btn" @click="load('notes')">重试</button></p>
        <div v-if="notes.length" class="notes-history-head"><h3>历史记录</h3><span>{{ notes.length }} 条</span></div>
        <div v-if="notes.length" class="notes-list">
          <article v-for="note in notes" :key="note.id" class="note-card">
            <time>
              <svg viewBox="0 0 20 20" aria-hidden="true"><circle cx="10" cy="10" r="7"/><path d="M10 6v4l3 2"/></svg>
              {{ time(note.created_at) }}
            </time>
            <p class="preserve">{{ note.content }}</p>
          </article>
        </div>
        <div v-if="!loading.notes && !errors.notes && !notes.length" class="notes-empty">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 3h10l4 4v14H5zM15 3v5h4M8 12h8M8 16h5" /></svg>
          <p>还没有分析记录</p><span>写下第一条分析，之后可以回来验证当时的判断。</span>
        </div>
      </section>
    </div>
  </div>
</template>

<style scoped>
.stock-detail { padding-bottom: 32px; }
.financial-company { margin-top: 18px; padding-bottom: 8px; border-bottom: 1px solid var(--line); }
.financial-prices { grid-template-columns: repeat(auto-fit, minmax(min(100%, 180px), 1fr)); }
.financial-prices b { overflow-wrap: anywhere; }
.company-about { line-height: 1.7; overflow-wrap: anywhere; white-space: pre-wrap; }
.financial-notes { grid-template-columns: repeat(auto-fit, minmax(min(100%, 240px), 1fr)); }
.financial-notes p { line-height: 1.65; }
.more-row { display:flex;justify-content:flex-end;margin-top:18px; }

.detail-heading,.section-heading,.note-actions,.notes-heading { display: flex; align-items: center; justify-content: space-between; gap: 16px; }
.detail-heading { justify-content: flex-start; margin-bottom: 20px; }
.detail-actions { margin-left: auto; }
h1 { margin: 0; font-size: 28px; } h1 small { font-size: 15px; color: var(--muted); } h2 { margin: 0; font-size: 18px; }
.detail-nav { display: flex; flex-wrap: wrap; gap: 12px; margin-bottom: 20px; }
.detail-nav a { padding: 9px 14px; background: #1c293d; border-radius: 8px; }
a { color: #91b1ff; text-decoration: none; } a:hover { text-decoration: underline; }
.detail-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 18px; }
.panel { min-width: 0; scroll-margin-top: 20px; }.wide { grid-column: 1 / -1; }
.metrics { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 18px; margin-top: 20px; }.metrics span { display: block; color: var(--muted); font-size: 13px; margin-bottom: 8px; }.metrics strong { font-size: 20px; overflow-wrap: anywhere; }
.detail-item { padding: 16px 0; border-bottom: 1px solid #293344; }.detail-item p { margin: 8px 0 0; }.preserve { white-space: pre-wrap; overflow-wrap: anywhere; line-height: 1.7; }
.notes-heading { align-items: flex-start; margin-bottom: 18px; }.notes-heading .sub { margin: 8px 0 0; }
.private-badge { display: inline-flex; align-items: center; gap: 6px; flex: none; padding: 6px 10px; border-radius: 20px; color: #9fb5d4; background: rgba(130,165,255,.08); font-size: 12px; }.private-badge svg { width: 14px; fill: none; stroke: currentColor; stroke-width: 1.5; stroke-linecap: round; stroke-linejoin: round; }
.note-editor { margin-top: 4px; }.note-input-shell { overflow: hidden; border: 1px solid #2c3a4d; border-radius: 12px; background: #0d1620; transition: border-color .16s ease, box-shadow .16s ease, background .16s ease; }.note-input-shell:hover:not(.disabled) { border-color: #4d6383; background: #101c29; }.note-input-shell:focus-within { border-color: #82a5ff; box-shadow: 0 0 0 3px rgba(130,165,255,.14); }.note-input-shell.disabled { opacity: .6; }
.note-input-shell textarea { display: block; width: 100%; min-height: 150px; box-sizing: border-box; resize: vertical; border: 0; outline: 0; background: transparent; color: #e5edf8; padding: 16px 18px; font: inherit; font-size: 14px; line-height: 1.75; }.note-input-shell textarea::placeholder { color: #65758c; }.note-input-shell textarea:disabled { cursor: not-allowed; }
.note-editor-footer { display: flex; justify-content: space-between; gap: 16px; padding: 10px 16px; border-top: 1px solid rgba(148,176,210,.1); color: #74859c; font-size: 11px; }.note-count { flex: none; font-variant-numeric: tabular-nums; }.note-count.warning { color: #d9b879; }
.note-actions { margin-top: 12px; }.note-save { min-width: 126px; height: 42px; display: inline-flex; align-items: center; justify-content: center; gap: 7px; border-radius: 10px; }.note-save svg { width: 15px; fill: none; stroke: currentColor; stroke-width: 1.5; stroke-linecap: round; stroke-linejoin: round; }
.notes-history-head { display: flex; align-items: center; justify-content: space-between; margin-top: 26px; padding-top: 22px; border-top: 1px solid var(--line); }.notes-history-head h3 { margin: 0; font-size: 15px; }.notes-history-head span { color: var(--muted); font-size: 12px; }
.notes-list { display: grid; gap: 12px; margin-top: 12px; }.note-card { padding: 16px 18px; border: 1px solid rgba(148,176,210,.13); border-radius: 12px; background: rgba(8,15,23,.35); }.note-card time { display: flex; align-items: center; gap: 7px; color: #788aa3; font-size: 11px; }.note-card time svg { width: 14px; fill: none; stroke: currentColor; stroke-width: 1.5; stroke-linecap: round; stroke-linejoin: round; }.note-card p { margin: 12px 0 0; color: #dbe4ef; }
.notes-empty { display: grid; justify-items: center; gap: 6px; margin-top: 22px; padding: 34px 20px; border: 1px dashed rgba(148,176,210,.18); border-radius: 12px; color: var(--muted); text-align: center; }.notes-empty svg { width: 30px; fill: none; stroke: #61748f; stroke-width: 1.4; stroke-linecap: round; stroke-linejoin: round; }.notes-empty p { margin: 5px 0 0; color: #b7c4d6; font-size: 14px; }.notes-empty span { font-size: 12px; }
@media(max-width: 850px) { .detail-grid { grid-template-columns: 1fr; }.detail-heading { align-items: flex-start; flex-direction: column; } }
@media(max-width: 600px) { .notes-heading { flex-direction: column; }.note-editor-footer { align-items: flex-end; }.note-hint { max-width: 70%; }.note-actions { align-items: stretch; flex-direction: column; }.note-save { width: 100%; } }
</style>
