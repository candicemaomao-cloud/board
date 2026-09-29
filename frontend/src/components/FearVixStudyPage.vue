<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { api } from '../api'
import { can } from '../auth'
import AppendJournalDialog from './AppendJournalDialog.vue'
import MacroChart from './MacroChart.vue'

const AFTER_OPTS = [
  { key: 20, label: '后 20 日' },
  { key: 40, label: '后 40 日' },
  { key: 60, label: '后 60 日' },
  { key: 90, label: '后 90 日' },
]

const selectedDate = ref('')
const calendarMonth = ref('')
const panel = ref(null)
const panelLoading = ref(false)
const panelError = ref('')

const stocks = ref([])
const symbol = ref('')
const afterDays = ref(60)
const path = ref(null)
const pathLoading = ref(false)
const pathError = ref('')
const journalOpen = ref(false)
const journalSnippet = ref('')

const stockOptions = computed(() => {
  const list = (stocks.value || []).map((r) => (r.symbol || '').toUpperCase()).filter(Boolean)
  return [...new Set(list)].sort()
})

const calendarMap = computed(() => {
  const map = {}
  for (const row of panel.value?.calendar || []) {
    if (row?.date) map[row.date] = row
  }
  return map
})

const monthCells = computed(() => {
  const base = calendarMonth.value || selectedDate.value || new Date().toISOString().slice(0, 10)
  const [y, m] = base.split('-').map(Number)
  if (!y || !m) return []
  const first = new Date(y, m - 1, 1)
  const startPad = (first.getDay() + 6) % 7 // Monday-first
  const daysInMonth = new Date(y, m, 0).getDate()
  const cells = []
  for (let i = 0; i < startPad; i += 1) cells.push({ empty: true, key: `pad-${i}` })
  for (let d = 1; d <= daysInMonth; d += 1) {
    const iso = `${y}-${String(m).padStart(2, '0')}-${String(d).padStart(2, '0')}`
    const info = calendarMap.value[iso]
    cells.push({
      empty: false,
      key: iso,
      date: iso,
      day: d,
      info,
      selected: iso === selectedDate.value,
      today: iso === new Date().toISOString().slice(0, 10),
    })
  }
  return cells
})

const monthLabel = computed(() => {
  const base = calendarMonth.value || selectedDate.value
  if (!base) return ''
  const [y, m] = base.split('-')
  return `${y}年${Number(m)}月`
})

const derivMainCards = computed(() =>
  (panel.value?.derivative_cards || []).filter((c) => c.layout !== 'sector_bars'),
)
const derivSectorCards = computed(() =>
  (panel.value?.derivative_cards || []).filter((c) => c.layout === 'sector_bars'),
)

const pathOption = computed(() => {
  const rows = path.value?.path || []
  if (!rows.length) return null
  return {
    animation: false,
    tooltip: {
      trigger: 'axis',
      formatter(params) {
        const p = params?.[0]
        if (!p) return ''
        const row = rows[p.dataIndex]
        const ret = row?.ret_from_event
        const retText = ret == null ? '—' : `${(ret * 100).toFixed(2)}%`
        return `${row?.date || p.name}<br/>收盘 ${p.value}<br/>相对事件日 ${retText}<br/>偏移 ${row?.offset ?? '—'} 日`
      },
    },
    grid: { left: 52, right: 24, top: 28, bottom: 28 },
    xAxis: {
      type: 'category',
      data: rows.map((r) => r.date),
      boundaryGap: false,
      axisLabel: { hideOverlap: true },
    },
    yAxis: { type: 'value', scale: true, splitLine: { lineStyle: { type: 'dashed', opacity: 0.25 } } },
    series: [
      {
        name: '收盘',
        type: 'line',
        showSymbol: false,
        data: rows.map((r) => r.close),
        markLine: {
          symbol: 'none',
          label: { formatter: '事件日' },
          lineStyle: { color: '#ff6b7a' },
          data: [{ xAxis: path.value?.anchor_date || selectedDate.value }],
        },
      },
    ],
  }
})

function toneClass(tone) {
  if (tone === 'extreme_fear' || tone === 'fear') return 'fear'
  if (tone === 'greed' || tone === 'extreme_greed') return 'greed'
  return 'neutral'
}

function deltaClass(delta) {
  if (delta == null) return ''
  // 对恐惧指标：数值上升通常更恐慌 → 红；下降 → 绿（CNN 相反，但统一用数值涨跌色）
  if (delta > 0) return 'up-bad'
  if (delta < 0) return 'down-good'
  return ''
}

function pct(n) {
  if (n == null || Number.isNaN(Number(n))) return '—'
  const x = Number(n) * 100
  return `${x >= 0 ? '+' : ''}${x.toFixed(2)}%`
}

function toneRet(n) {
  if (n == null) return ''
  if (n > 0.001) return 'up'
  if (n < -0.001) return 'down'
  return ''
}

const canAppendJournal = computed(
  () =>
    can('btn.stock_journal.write') &&
    !!(panel.value?.cards?.length || panel.value?.derivative_cards?.length || panel.value?.composite != null),
)

const journalDefaultDate = computed(
  () => panel.value?.resolved_date || selectedDate.value || '',
)

function buildFearSnippet() {
  const p = panel.value
  if (!p) return ''
  const day = p.resolved_date || selectedDate.value || '—'
  const lines = [
    `【恐慌指标】${day}`,
    `六项等权 ${p.composite ?? '—'}（${p.composite_label || '—'}）`,
  ]
  for (const card of p.cards || []) {
    const delta = card.delta_display ? ` ${card.delta_display}` : ''
    lines.push(`${card.title} ${card.display} ${card.label || ''}${delta}`.replace(/\s+/g, ' ').trim())
  }
  if ((p.derivative_cards || []).length) {
    lines.push('【衍生品三层】')
    for (const card of p.derivative_cards) {
      lines.push(`${card.title} ${card.display} ${card.label || ''}`.replace(/\s+/g, ' ').trim())
      if (card.layout === 'sector_bars' && card.rows?.length) {
        for (const row of card.rows) {
          lines.push(
            `  ${row.symbol}(${row.name}) Put/Call ${Number(row.pcr).toFixed(2)} Call ${row.call_share ?? '—'}% Put ${row.put_share ?? '—'}%`,
          )
        }
      }
    }
  }
  return lines.join('\n')
}

function openFearJournal() {
  journalSnippet.value = buildFearSnippet()
  if (!journalSnippet.value) return
  journalOpen.value = true
}

function shiftMonth(delta) {
  const base = calendarMonth.value || selectedDate.value || new Date().toISOString().slice(0, 10)
  const [y, m] = base.split('-').map(Number)
  const dt = new Date(y, m - 1 + delta, 1)
  calendarMonth.value = `${dt.getFullYear()}-${String(dt.getMonth() + 1).padStart(2, '0')}-01`
}

function pickDay(cell) {
  if (!cell?.date) return
  selectedDate.value = cell.date
  path.value = null
  pathError.value = ''
}

async function loadPanel() {
  panelLoading.value = true
  panelError.value = ''
  try {
    const data = await api.marketFearPanel({
      date: selectedDate.value || undefined,
    })
    panel.value = data
    if (!selectedDate.value && data.resolved_date) {
      selectedDate.value = data.resolved_date
    }
    if (!calendarMonth.value && (data.resolved_date || selectedDate.value)) {
      const d = data.resolved_date || selectedDate.value
      calendarMonth.value = `${d.slice(0, 7)}-01`
    }
  } catch (e) {
    panelError.value = e.message || '恐慌指标加载失败'
  } finally {
    panelLoading.value = false
  }
}

async function refreshPanel() {
  await loadPanel()
}

async function loadStocks() {
  try {
    const list = await api.dailyWatches()
    stocks.value = Array.isArray(list) ? list : []
    if (!symbol.value && stockOptions.value.length) symbol.value = stockOptions.value[0]
  } catch {
    stocks.value = []
  }
}

async function runPath() {
  pathError.value = ''
  path.value = null
  const sym = (symbol.value || '').trim().toUpperCase()
  const ed = (selectedDate.value || '').trim()
  if (!sym) {
    pathError.value = '请从股票列表里选一只'
    return
  }
  if (!ed) {
    pathError.value = '请先在日历里点选一天'
    return
  }
  pathLoading.value = true
  try {
    path.value = await api.marketFearPath({
      symbol: sym,
      event_date: ed,
      before: 30,
      after: afterDays.value,
    })
  } catch (e) {
    pathError.value = e.message || '走势计算失败'
  } finally {
    pathLoading.value = false
  }
}

let dateWatchReady = false
watch(selectedDate, async (d, prev) => {
  if (!dateWatchReady) return
  if (!d || d === prev) return
  calendarMonth.value = `${d.slice(0, 7)}-01`
  await loadPanel()
})

onMounted(async () => {
  selectedDate.value = new Date().toISOString().slice(0, 10)
  calendarMonth.value = `${selectedDate.value.slice(0, 7)}-01`
  await Promise.all([loadPanel(), loadStocks()])
  dateWatchReady = true
})
</script>

<template>
  <p class="goal-lead">
    恐慌指标：顶部日历点选日期，面板刷新到那天的六项情绪；再从股票列表选一只，看恐慌日后的股价走势。
  </p>

  <section class="panel fear-top" style="margin-top: 12px">
    <div class="fear-top-row">
      <div>
        <h2 style="margin: 0 0 6px">选择日期</h2>
        <p class="sub" style="margin: 0">点日历某一天即可更新下方指标；周末会落到最近交易日。</p>
      </div>
      <el-date-picker
        v-model="selectedDate"
        type="date"
        value-format="YYYY-MM-DD"
        placeholder="选日期"
        :clearable="false"
        style="width: 160px"
      />
    </div>

    <div class="fear-cal-head">
      <button class="btn" type="button" @click="shiftMonth(-1)">‹</button>
      <b>{{ monthLabel }}</b>
      <button class="btn" type="button" @click="shiftMonth(1)">›</button>
    </div>
    <div class="fear-cal-week">
      <span v-for="w in ['一', '二', '三', '四', '五', '六', '日']" :key="w">{{ w }}</span>
    </div>
    <div class="fear-cal-grid">
      <button
        v-for="cell in monthCells"
        :key="cell.key"
        type="button"
        class="fear-cal-cell"
        :class="[
          cell.empty ? 'empty' : '',
          cell.selected ? 'selected' : '',
          cell.today ? 'today' : '',
          cell.info ? `tone-${toneClass(cell.info.tone)}` : '',
        ]"
        :disabled="cell.empty"
        @click="pickDay(cell)"
      >
        <template v-if="!cell.empty">
          <span class="d">{{ cell.day }}</span>
          <span class="v" v-if="cell.info">{{ Number(cell.info.value).toFixed(0) }}</span>
        </template>
      </button>
    </div>
    <div class="fear-cal-legend">
      <span class="leg tone-fear">恐惧</span>
      <span class="leg tone-neutral">中性</span>
      <span class="leg tone-greed">贪婪</span>
      <span class="sub">色块按当天 CNN 恐惧贪婪着色，数字是 CNN 分数</span>
    </div>
  </section>

  <section class="panel fear-board" style="margin-top: 12px" v-loading="panelLoading">
    <div class="fear-board-head">
      <div>
        <h2 style="margin: 0">恐慌指标</h2>
        <p class="sub" style="margin: 4px 0 0">
          UPDATED {{ panel?.updated_at || '—' }}
          · 锚定 {{ panel?.resolved_date || selectedDate || '—' }}
        </p>
      </div>
      <div class="fear-composite" :class="toneClass(panel?.composite_tone)">
        <div class="score">{{ panel?.composite ?? '—' }}</div>
        <div class="label">六项等权 · {{ panel?.composite_label || '—' }}</div>
      </div>
    </div>

    <div class="error" v-if="panelError" style="margin: 8px 0">{{ panelError }}</div>

    <div class="fear-cards">
      <div
        v-for="card in panel?.cards || []"
        :key="card.key"
        class="fear-card"
        :class="toneClass(card.tone)"
      >
        <div class="fear-card-top">
          <div class="title">{{ card.title }}</div>
          <span class="tag">{{ card.label }}</span>
        </div>
        <div class="fear-card-mid">
          <div class="val">{{ card.display }}</div>
          <div class="delta" :class="deltaClass(card.delta)" v-if="card.delta_display">
            <span v-if="card.delta > 0">▲</span>
            <span v-else-if="card.delta < 0">▼</span>
            {{ card.delta_display }}
          </div>
        </div>
        <div class="bar-track">
          <div class="bar-fill" :style="{ width: `${card.bar ?? 0}%` }" />
        </div>
        <p class="hint tip">{{ card.hint }}</p>
      </div>
    </div>

    <div v-if="(panel?.derivative_cards || []).length" class="fear-deriv-block">
      <div class="fear-deriv-head">
        <h3>衍生品三层</h3>
        <span class="sub">期货方向 · 股指期权波动 · 板块期权仓位 · 不计入上方等权</span>
      </div>
      <div class="fear-cards fear-cards-deriv">
        <div
          v-for="card in derivMainCards"
          :key="card.key"
          class="fear-card"
          :class="toneClass(card.tone)"
        >
          <div class="fear-card-top">
            <div class="title">{{ card.title }}</div>
            <span class="tag">{{ card.label }}</span>
          </div>
          <div class="fear-card-mid">
            <div class="val">{{ card.display }}</div>
            <div class="delta" :class="deltaClass(card.delta)" v-if="card.delta_display">
              <span v-if="card.delta > 0">▲</span>
              <span v-else-if="card.delta < 0">▼</span>
              {{ card.delta_display }}
            </div>
          </div>
          <div class="bar-track">
            <div class="bar-fill" :style="{ width: `${card.bar ?? 0}%` }" />
          </div>
          <p class="hint tip">{{ card.hint }}</p>
        </div>
      </div>

      <div
        v-for="card in derivSectorCards"
        :key="card.key"
        class="fear-card fear-card-sector"
        :class="toneClass(card.tone)"
      >
        <div class="fear-card-top">
          <div class="title">{{ card.title }}</div>
          <span class="tag">{{ card.label }} · {{ card.display }}</span>
        </div>
        <div class="sector-legend">
          <span class="leg call">Call 看涨</span>
          <span class="leg put">Put 看跌</span>
          <span class="sub">条长=成交量占比；右侧为 Put/Call 比率</span>
        </div>
        <div class="sector-rows">
          <div v-for="row in card.rows || []" :key="row.symbol" class="sector-row">
            <div class="sector-meta">
              <span class="sym">{{ row.symbol }}</span>
              <span class="name">{{ row.name }}</span>
            </div>
            <div class="sector-bar-track" :title="`Call ${row.call_share ?? '—'}% · Put ${row.put_share ?? '—'}%`">
              <div class="sector-bar call" :style="{ width: `${row.call_share ?? 0}%` }" />
              <div class="sector-bar put" :style="{ width: `${row.put_share ?? 0}%` }" />
            </div>
            <div class="sector-stats">
              <span class="share call">C {{ row.call_share != null ? `${row.call_share}%` : '—' }}</span>
              <span class="share put">P {{ row.put_share != null ? `${row.put_share}%` : '—' }}</span>
              <span class="pcr" :class="toneClass(row.tone)">{{ Number(row.pcr).toFixed(2) }}</span>
            </div>
          </div>
        </div>
        <p class="hint tip">{{ card.hint }}</p>
      </div>
    </div>

    <div class="fear-board-foot">
      <span class="sub">{{ panel?.note || '6 项指标等权合成' }}</span>
      <div class="range-row" style="margin: 0; gap: 8px">
        <button
          v-if="canAppendJournal"
          class="btn"
          type="button"
          :disabled="panelLoading"
          @click="openFearJournal"
        >
          加入日志
        </button>
        <button class="btn" type="button" :disabled="panelLoading" @click="refreshPanel">
          {{ panelLoading ? '刷新中…' : '刷新' }}
        </button>
      </div>
    </div>
  </section>

  <AppendJournalDialog
    v-model="journalOpen"
    title="恐慌指标加入日志"
    :snippet="journalSnippet"
    :default-date="journalDefaultDate"
  />

  <section class="panel" style="margin-top: 12px">
    <h2 style="margin: 0 0 8px">恐慌日之后 · 单票走势</h2>
    <div class="range-row" style="gap: 8px; flex-wrap: wrap; margin-bottom: 10px">
      <select v-model="symbol" class="strategy-select" style="min-width: 140px">
        <option disabled value="">选股票列表里的代码</option>
        <option v-for="s in stockOptions" :key="s" :value="s">{{ s }}</option>
      </select>
      <select v-model.number="afterDays" class="strategy-select">
        <option v-for="opt in AFTER_OPTS" :key="opt.key" :value="opt.key">{{ opt.label }}</option>
      </select>
      <button
        class="btn btn-primary"
        type="button"
        :disabled="pathLoading || !symbol || !selectedDate"
        @click="runPath"
      >
        {{ pathLoading ? '计算中…' : `看 ${selectedDate || '…'} 之后走势` }}
      </button>
    </div>
    <p class="sub" v-if="!stockOptions.length">股票列表是空的，先去「股票列表」加几只。</p>
    <div class="error" v-if="pathError">{{ pathError }}</div>

    <div v-if="path" style="margin-top: 10px">
      <p class="sub" style="margin: 0 0 8px">
        {{ path.symbol }} · 事件 {{ path.event_date }}
        <template v-if="path.anchor_date !== path.event_date">（锚点 {{ path.anchor_date }}）</template>
        · 基准价 {{ path.anchor_close }}
      </p>
      <div class="metric-grid" style="margin-bottom: 12px">
        <div>
          <span class="sub">+1 日</span>
          <div class="mono" :class="toneRet(path.returns?.d1)">{{ pct(path.returns?.d1) }}</div>
        </div>
        <div>
          <span class="sub">+5 日</span>
          <div class="mono" :class="toneRet(path.returns?.d5)">{{ pct(path.returns?.d5) }}</div>
        </div>
        <div>
          <span class="sub">+20 日</span>
          <div class="mono" :class="toneRet(path.returns?.d20)">{{ pct(path.returns?.d20) }}</div>
        </div>
        <div>
          <span class="sub">+60 日</span>
          <div class="mono" :class="toneRet(path.returns?.d60)">{{ pct(path.returns?.d60) }}</div>
        </div>
        <div>
          <span class="sub">窗口末</span>
          <div class="mono" :class="toneRet(path.returns?.end)">{{ pct(path.returns?.end) }}</div>
        </div>
      </div>
      <MacroChart v-if="pathOption" :option="pathOption" height="340px" />
    </div>
  </section>

</template>

<style scoped>
.fear-top-row {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
  margin-bottom: 12px;
}
.fear-cal-head {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 16px;
  margin-bottom: 8px;
}
.fear-cal-week {
  display: grid;
  grid-template-columns: repeat(7, minmax(0, 1fr));
  gap: 6px;
  margin-bottom: 6px;
  text-align: center;
  color: #8b9bb0;
  font-size: 12px;
}
.fear-cal-grid {
  display: grid;
  grid-template-columns: repeat(7, minmax(0, 1fr));
  gap: 6px;
}
.fear-cal-cell {
  min-height: 54px;
  border: 1px solid rgba(148, 176, 210, 0.14);
  border-radius: 8px;
  background: rgba(12, 18, 26, 0.55);
  color: #d7e2ef;
  cursor: pointer;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 2px;
  padding: 4px;
}
.fear-cal-cell.empty {
  background: transparent;
  border-color: transparent;
  cursor: default;
}
.fear-cal-cell .d {
  font-size: 13px;
  font-weight: 600;
}
.fear-cal-cell .v {
  font-size: 11px;
  opacity: 0.9;
}
.fear-cal-cell.today {
  box-shadow: inset 0 0 0 1px rgba(122, 162, 255, 0.55);
}
.fear-cal-cell.selected {
  outline: 2px solid #7aa2ff;
  outline-offset: 0;
}
.fear-cal-cell.tone-fear {
  background: rgba(255, 90, 100, 0.18);
  border-color: rgba(255, 90, 100, 0.35);
}
.fear-cal-cell.tone-neutral {
  background: rgba(230, 180, 60, 0.14);
  border-color: rgba(230, 180, 60, 0.3);
}
.fear-cal-cell.tone-greed {
  background: rgba(60, 200, 150, 0.14);
  border-color: rgba(60, 200, 150, 0.3);
}
.fear-cal-legend {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 10px;
  flex-wrap: wrap;
}
.fear-cal-legend .leg {
  font-size: 12px;
  padding: 2px 8px;
  border-radius: 999px;
}
.fear-cal-legend .leg.tone-fear {
  background: rgba(255, 90, 100, 0.2);
  color: #ff8a93;
}
.fear-cal-legend .leg.tone-neutral {
  background: rgba(230, 180, 60, 0.18);
  color: #e7c35a;
}
.fear-cal-legend .leg.tone-greed {
  background: rgba(60, 200, 150, 0.18);
  color: #5fd4ae;
}

.fear-board-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
  margin-bottom: 14px;
}
.fear-composite {
  text-align: right;
}
.fear-composite .score {
  font-size: 56px;
  font-weight: 700;
  line-height: 1;
  letter-spacing: -0.02em;
}
.fear-composite .label {
  margin-top: 6px;
  font-size: 15px;
  font-weight: 600;
}
.fear-composite.fear .score,
.fear-composite.fear .label {
  color: #ff7a84;
}
.fear-composite.neutral .score,
.fear-composite.neutral .label {
  color: #e7c35a;
}
.fear-composite.greed .score,
.fear-composite.greed .label {
  color: #5fd4ae;
}

.fear-cards {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}
.fear-card {
  border: 1px solid rgba(148, 176, 210, 0.16);
  border-radius: 12px;
  padding: 14px 14px 12px;
  background: rgba(14, 20, 28, 0.7);
}
.fear-card-top {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 8px;
}
.fear-card .title {
  font-size: 14px;
  color: #c5d3e4;
}
.fear-card .tag {
  font-size: 12px;
  padding: 2px 8px;
  border-radius: 999px;
}
.fear-card.fear .tag {
  background: rgba(255, 90, 100, 0.18);
  color: #ff8a93;
}
.fear-card.neutral .tag {
  background: rgba(230, 180, 60, 0.16);
  color: #e7c35a;
}
.fear-card.greed .tag {
  background: rgba(60, 200, 150, 0.16);
  color: #5fd4ae;
}
.fear-card-mid {
  display: flex;
  align-items: baseline;
  gap: 10px;
  margin: 8px 0 10px;
}
.fear-card .val {
  font-size: 28px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
}
.fear-card .delta {
  font-size: 13px;
}
.fear-card .delta.up-bad {
  color: #ff7a84;
}
.fear-card .delta.down-good {
  color: #5fd4ae;
}
.bar-track {
  height: 6px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.08);
  overflow: hidden;
}
.bar-fill {
  height: 100%;
  border-radius: 999px;
  background: #e7c35a;
}
.fear-card.fear .bar-fill {
  background: #ff6b7a;
}
.fear-card.greed .bar-fill {
  background: #5fd4ae;
}
.fear-card .hint {
  margin: 8px 0 0;
  font-size: 11px;
  line-height: 1.45;
  color: #8b9bb0;
}
.fear-card .hint.tip {
  border-top: 1px dashed rgba(148, 176, 210, 0.18);
  padding-top: 7px;
}
.fear-board-foot {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-top: 14px;
  gap: 12px;
}
.metric-grid {
  display: grid;
  grid-template-columns: repeat(5, minmax(0, 1fr));
  gap: 10px 14px;
}
@media (max-width: 860px) {
  .fear-cards {
    grid-template-columns: 1fr;
  }
  .metric-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

.fear-deriv-block {
  margin-top: 18px;
  padding-top: 14px;
  border-top: 1px dashed rgba(148, 176, 210, 0.22);
}
.fear-deriv-head {
  display: flex;
  align-items: baseline;
  gap: 12px;
  flex-wrap: wrap;
  margin-bottom: 10px;
}
.fear-deriv-head h3 {
  margin: 0;
  font-size: 15px;
  color: #d7e2ef;
  font-weight: 600;
}
.fear-cards-deriv {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}
@media (max-width: 1100px) {
  .fear-cards-deriv {
    grid-template-columns: 1fr;
  }
}

.fear-card-sector {
  margin-top: 12px;
}
.sector-legend {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  margin: 4px 0 10px;
  font-size: 12px;
}
.sector-legend .leg {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.sector-legend .leg::before {
  content: '';
  width: 10px;
  height: 10px;
  border-radius: 2px;
}
.sector-legend .leg.call::before {
  background: #5fd4ae;
}
.sector-legend .leg.put::before {
  background: #ff6b7a;
}
.sector-legend .leg.call {
  color: #5fd4ae;
}
.sector-legend .leg.put {
  color: #ff8a93;
}
.sector-rows {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.sector-row {
  display: grid;
  grid-template-columns: 88px minmax(0, 1fr) 148px;
  align-items: center;
  gap: 10px;
}
.sector-meta {
  display: flex;
  flex-direction: column;
  line-height: 1.2;
}
.sector-meta .sym {
  font-weight: 700;
  font-size: 13px;
  color: #e2ebf5;
}
.sector-meta .name {
  font-size: 11px;
  color: #8b9bb0;
}
.sector-bar-track {
  display: flex;
  height: 12px;
  border-radius: 999px;
  overflow: hidden;
  background: rgba(255, 255, 255, 0.06);
}
.sector-bar {
  height: 100%;
  min-width: 0;
  transition: width 0.25s ease;
}
.sector-bar.call {
  background: #5fd4ae;
}
.sector-bar.put {
  background: #ff6b7a;
}
.sector-stats {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  font-size: 11px;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
.sector-stats .share.call {
  color: #5fd4ae;
}
.sector-stats .share.put {
  color: #ff8a93;
}
.sector-stats .pcr {
  min-width: 36px;
  text-align: right;
  font-weight: 700;
  font-size: 13px;
  color: #d7e2ef;
}
.sector-stats .pcr.fear,
.sector-stats .pcr.extreme_fear {
  color: #ff8a93;
}
.sector-stats .pcr.greed {
  color: #5fd4ae;
}
@media (max-width: 720px) {
  .sector-row {
    grid-template-columns: 72px minmax(0, 1fr);
    grid-template-rows: auto auto;
  }
  .sector-stats {
    grid-column: 1 / -1;
    justify-content: flex-start;
  }
}

</style>
