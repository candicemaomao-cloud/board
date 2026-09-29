<script setup>
import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { api } from '../api'
import { money, tone } from '../format'

const data = ref(null)
const loading = ref(false)
const error = ref('')
const ok = ref('')
const filter = ref('all')
const basket = ref('all')
const timeframe = ref('5m')
const search = ref('')
const progress = ref('')
const adding = ref('')
const btKey = ref('')
const btLoading = ref(false)
const bt = ref(null)
const picked = reactive({})
const MAX_HEDGES = 2
let pollTimer

const FILTERS = [
  { key: 'all', label: '全部' },
  { key: 'green', label: '值得研究' },
  { key: 'yellow', label: '观察' },
  { key: 'red', label: '不交易' },
]
const TFS = [
  { key: '5m', label: '5分钟' },
  { key: '30m', label: '30分钟' },
]

const FALLBACK_BASKETS = [
  { key: 'semis', label: '半导体', default: true },
  { key: 'software', label: '软件云', default: true },
  { key: 'ads', label: '广告平台', default: true },
  { key: 'finance', label: '金融', default: true },
  { key: 'energy', label: '能源', default: true },
  { key: 'consumer', label: '消费', default: true },
  { key: 'industrial', label: '工业快递', default: true },
  { key: 'etf', label: '行业ETF对', default: true },
  { key: 'highbeta', label: '对照：vs 大盘', default: false },
]

const baskets = computed(() => (data.value?.baskets?.length ? data.value.baskets : FALLBACK_BASKETS))
const mainBaskets = computed(() => baskets.value.filter((b) => b.default !== false))
const contrastBaskets = computed(() => baskets.value.filter((b) => b.default === false))
const selectedBasket = computed(() => baskets.value.find((b) => b.key === basket.value))
const basketMissing = computed(() => {
  if (basket.value === 'all') return false
  const list = data.value?.rows || []
  return list.length > 0 && !list.some((r) => r.basket === basket.value)
})

const rows = computed(() => {
  const list = data.value?.rows || []
  const q = search.value.trim().toLowerCase()
  return list.filter((r) => {
    if (filter.value !== 'all' && r.status !== filter.value) return false
    if (basket.value !== 'all' && r.basket !== basket.value) return false
    if (!q) return true
    return `${r.target} ${(r.factors || []).join(' ')} ${r.hedge || ''} ${r.relation || ''} ${r.basket_label || ''} ${r.kind_label || ''}`.toLowerCase().includes(q)
  })
})

function light(status) {
  if (status === 'green') return '🟢'
  if (status === 'yellow') return '🟡'
  return '🔴'
}

function zClass(z) {
  if (z == null) return ''
  if (Math.abs(z) >= 2.5) return 'up'
  if (Math.abs(z) >= 2) return 'warn'
  return ''
}

function stopPoll() {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

function ensurePoll() {
  if (!pollTimer) pollTimer = setInterval(tick, 800)
}

function applyResult(payload) {
  if (!payload) return
  data.value = payload
  const okN = (payload.rows || []).filter((r) => r.ok).length
  const miss = (payload.rows || []).filter((r) => r.status_label === '缺行情').length
  if (!okN && miss) {
    error.value = '行情没拉到。不用填代码，过一会儿再扫。'
    ok.value = ''
  } else {
    error.value = ''
    ok.value = `扫完 ${payload.n || 0} 只 · 有数据 ${okN} · ${payload.regime?.label || ''}`
  }
}

async function loadCached() {
  try {
    const uni = await api.intradayUniverse()
    const cached = await api.intradayCached()
    data.value = {
      ...cached,
      baskets: uni.baskets || cached.baskets,
    }
  } catch (e) {
    error.value = e.message
  }
}

async function tick() {
  try {
    const st = await api.intradayStatus()
    if (st.running) {
      loading.value = true
      progress.value = st.stage || `扫描中 ${st.done || 0}/${st.total || ''}`
      ensurePoll()
      return
    }
    stopPoll()
    loading.value = false
    progress.value = ''
    if (st.error) {
      error.value = st.error
      return
    }
    if (st.result?.rows?.length) applyResult(st.result)
    else await loadCached()
  } catch (e) {
    error.value = e.message || '扫描失败'
    if (loading.value) ensurePoll()
    else {
      stopPoll()
      loading.value = false
    }
  }
}

async function scan() {
  if (loading.value) {
    ensurePoll()
    await tick()
    return
  }
  loading.value = true
  error.value = ''
  ok.value = ''
  progress.value = '开始扫描…'
  ensurePoll()
  try {
    const baskets = basket.value === 'all' ? null : [basket.value]
    await api.intradayScan({ force: true, baskets, timeframe: timeframe.value })
    await tick()
  } catch (e) {
    loading.value = false
    progress.value = ''
    error.value = e.message || '扫描失败'
    stopPoll()
  }
}

onMounted(async () => {
  await loadCached()
  await tick()
})
onUnmounted(() => stopPoll())

function rowKey(r) {
  return `${r.basket}-${r.target}-${(r.factors || []).join('+')}`
}

function hedgesOf(row) {
  const k = rowKey(row)
  if (!picked[k]) picked[k] = (row.factors || []).slice(0, MAX_HEDGES)
  return picked[k]
}

function toggleHedge(row, sym) {
  const list = hedgesOf(row)
  const i = list.indexOf(sym)
  if (i >= 0) {
    if (list.length > 1) list.splice(i, 1)
  } else if (list.length < MAX_HEDGES) {
    list.push(sym)
  }
}

async function addToStrategy(row) {
  adding.value = rowKey(row)
  error.value = ''
  ok.value = ''
  try {
    const created = await api.intradayToStrategy({
      target: row.target,
      factors: hedgesOf(row),
      timeframe: timeframe.value,
      lookback: data.value?.lookback || 390,
    })
    ok.value = `已加到套利策略：${created.name}。去「套利策略」点计算，用的是 ${timeframe.value} K 线残差，不是 D 线。`
  } catch (e) {
    error.value = e.message || '加不进去'
  } finally {
    adding.value = ''
  }
}

async function runBacktest(row) {
  btKey.value = rowKey(row)
  btLoading.value = true
  bt.value = null
  error.value = ''
  ok.value = ''
  try {
    bt.value = await api.intradayBacktest({
      target: row.target,
      factors: hedgesOf(row),
      timeframe: timeframe.value,
      entry_z: data.value?.z_entry || 2.5,
      notional: 10000,
    })
    ok.value = `回测 ${bt.value.target}：${bt.value.trades} 笔，成本后 ${bt.value.total_pnl_net}`
  } catch (e) {
    error.value = e.message || '回测失败'
    btKey.value = ''
  } finally {
    btLoading.value = false
  }
}
</script>

<template>
  <p class="goal-lead">
    之前亏，是因为按<b>单根 5 分钟噪声</b>开仓，边往往小于往返成本。现在 Z 看价差水平（log 价 − β·对冲）。
    回测会分开看成本前 / 成本后；成本后仍亏，就是这对没边，不是显示问题。
    同向带动、财报、跳空一律不交易。
  </p>
  <div class="range-row">
    <button class="btn btn-primary" type="button" @click="scan">
      {{ loading ? '扫描中…' : (rows.length ? '重新扫描' : '扫描日内残差') }}
    </button>
    <div class="pills">
      <button
        v-for="t in TFS"
        :key="t.key"
        class="pill"
        :class="{ active: timeframe === t.key }"
        type="button"
        @click="timeframe = t.key"
      >
        {{ t.label }}
      </button>
    </div>
    <div class="pills">
      <button
        class="pill"
        :class="{ active: basket === 'all' }"
        type="button"
        @click="basket = 'all'"
      >
        全部（同业 / 行业ETF）
      </button>
      <button
        v-for="b in mainBaskets"
        :key="b.key"
        class="pill"
        :class="{ active: basket === b.key }"
        type="button"
        @click="basket = b.key"
      >
        {{ b.label }}
      </button>
      <button
        v-for="b in contrastBaskets"
        :key="b.key"
        class="pill"
        :class="{ active: basket === b.key }"
        type="button"
        @click="basket = b.key"
      >
        {{ b.label }}
      </button>
    </div>
  </div>
  <div class="range-row">
    <div class="pills">
      <button
        v-for="f in FILTERS"
        :key="f.key"
        class="pill"
        :class="{ active: filter === f.key }"
        type="button"
        @click="filter = f.key"
      >
        {{ f.label }}
      </button>
    </div>
    <div class="watch-edit" style="flex: 1; margin: 0">
      <input v-model="search" placeholder="筛选代码，空着看全部" />
    </div>
  </div>
  <p class="sub" v-if="progress">{{ progress }}</p>
  <div class="error" v-if="error">{{ error }}</div>
  <p class="ok-msg" v-else-if="ok">{{ ok }}</p>
  <p class="sub" v-if="data?.regime">
    市场：{{ data.regime.label }}
    · SPY {{ data.regime.spy_ret != null ? data.regime.spy_ret + '%' : '—' }}
    <span v-if="data.regime.sector_ret != null"> · 板块 {{ data.regime.sector_ret }}%</span>
    · {{ data.regime.why }}
  </p>
  <p class="sub" v-if="selectedBasket?.note">{{ selectedBasket.note }}</p>
  <p class="sub" v-else-if="basket === 'all'">默认扫同业两腿和股票 vs 行业 ETF。不含 vs SPY。</p>
  <p class="sub" v-if="basketMissing">当前结果里没有这一篮。点「重新扫描」才会扫。</p>
  <p class="sub" v-if="data?.as_of">
    {{ data.as_of }} · {{ data.timeframe }} · {{ data.n_ok || 0 }}/{{ data.n || 0 }} 条
    · 窗口 {{ data.lookback }} 根 · 对冲优先 1 只（两标的），最多 2 只
    （约 {{ data.lookback_days || '?' }} 个交易日）
    · |Z|≥{{ data.z_entry }} · 成本 {{ data.cost_bps }}bp
  </p>
  <p class="sub" v-if="data?.note">{{ data.note }}</p>
  <p class="sub" v-if="data?.earnings_note">{{ data.earnings_note }}</p>

  <section class="panel" style="margin-top: 12px">
    <div class="table-wrap" v-if="rows.length">
      <table>
        <thead>
          <tr>
            <th>标的</th>
            <th>对冲组合</th>
            <th>价差偏离</th>
            <th>Z</th>
            <th>量能</th>
            <th>成本</th>
            <th>制度</th>
            <th>状态</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="r in rows" :key="rowKey(r)">
            <td>
              <b>{{ r.target }}</b>
              <div class="sub">{{ r.basket_label }} · {{ r.kind_label }}{{ r.n ? ' · ' + r.n + '根' : '' }}</div>
              <div class="sub" v-if="r.event_label" style="color: var(--warn, #c9a227)">{{ r.event_label }}</div>
            </td>
            <td>
              <div class="macro-chips">
                <button
                  v-for="f in (r.factors || [])"
                  :key="f"
                  class="chip"
                  type="button"
                  :class="{ now: hedgesOf(r).includes(f) }"
                  @click="toggleHedge(r, f)"
                >
                  {{ f }}
                </button>
              </div>
              <div class="sub">{{ r.relation || '两腿' }} · 点选对冲，优先 1 只</div>
            </td>
            <td class="mono" :class="zClass(r.z)">
              {{ r.spread_pct != null ? r.spread_pct + '%' : '—' }}
              <div class="sub">本根 {{ r.residual_pct != null ? r.residual_pct + '%' : '—' }}</div>
            </td>
            <td class="mono" :class="zClass(r.z)">{{ r.z ?? '—' }}</td>
            <td class="mono">
              {{ r.rvol ?? '—' }}×
              <div class="sub" v-if="r.info_shock">疑似冲击</div>
            </td>
            <td class="mono">
              {{ r.net_pct != null ? r.net_pct + '%' : '—' }}
              <div class="sub">偏离 {{ r.edge_pct }} − 往返 {{ r.cost_pct }}</div>
            </td>
            <td>{{ r.regime_label || '—' }}</td>
            <td>
              {{ light(r.status) }} {{ r.status_label }}
              <div class="sub">{{ r.why }}</div>
            </td>
            <td class="pos-actions">
              <button class="icon-btn" type="button" :disabled="btLoading" @click="runBacktest(r)">
                {{ btLoading && btKey === rowKey(r) ? '回测…' : '回测' }}
              </button>
              <button class="icon-btn" type="button" :disabled="!!adding" @click="addToStrategy(r)">
                {{ adding === rowKey(r) ? '…' : '加入策略' }}
              </button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <div v-else class="empty">
      {{ loading ? (progress || '正在拉 5 分钟K并做滚动回归…') : '还没有结果。点扫描：同业两腿 + 股票 vs 行业 ETF，不是 vs 大盘。旧结果请重新扫。' }}
    </div>
  </section>

  <section class="panel" style="margin-top: 12px" v-if="bt">
    <h2 style="margin: 0 0 4px; font-size: 15px">
      样本回测 · {{ bt.target }} vs {{ bt.hedge }}
    </h2>
    <p class="sub" style="margin: 0 0 10px">{{ bt.note }}</p>
    <div class="range-row" style="gap: 24px; flex-wrap: wrap">
      <div>
        <div class="sub">成本后</div>
        <b :class="tone(bt.total_pnl_net)">{{ money(bt.total_pnl_net) }}</b>
        <div class="sub">{{ bt.trades }} 笔 · 胜率 {{ bt.win_rate ?? '—' }}%</div>
        <div class="sub" v-if="bt.blocked_event">跳过事件 {{ bt.blocked_event }} 根</div>
      </div>
      <div>
        <div class="sub">成本前</div>
        <b :class="tone(bt.total_pnl_gross)">{{ money(bt.total_pnl_gross) }}</b>
        <div class="sub">费用 {{ money(bt.cost_drag) }}</div>
      </div>
      <div>
        <div class="sub">相对投入</div>
        <b :class="tone(bt.roi_pct)">{{ bt.roi_pct != null ? `${bt.roi_pct}%` : '—' }}</b>
      </div>
      <div>
        <div class="sub">最大回撤</div>
        <b :class="tone(bt.mdd)">{{ money(bt.mdd) }}</b>
      </div>
      <div>
        <div class="sub">样本内</div>
        <b :class="tone(bt.holdout?.in_sample?.pnl)">{{ money(bt.holdout?.in_sample?.pnl) }}</b>
        <div class="sub">{{ bt.holdout?.in_sample?.trades ?? 0 }} 笔</div>
      </div>
      <div>
        <div class="sub">样本外</div>
        <b :class="tone(bt.holdout?.out_of_sample?.pnl)">{{ money(bt.holdout?.out_of_sample?.pnl) }}</b>
        <div class="sub">{{ bt.holdout?.out_of_sample?.trades ?? 0 }} 笔 · 切 {{ bt.holdout?.split }}</div>
      </div>
    </div>
    <div class="table-wrap" style="margin-top: 12px" v-if="bt.recent?.length">
      <table>
        <thead>
          <tr>
            <th>方向</th>
            <th>开</th>
            <th>平</th>
            <th>入场 z</th>
            <th>盈亏</th>
            <th>原因</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(t, i) in bt.recent" :key="i">
            <td>{{ t.side === 'long_resid' ? '做多残差' : '做空残差' }}</td>
            <td class="mono">{{ t.entry }}</td>
            <td class="mono">{{ t.exit }}</td>
            <td class="mono">{{ t.entry_z }}</td>
            <td class="mono" :class="tone(t.pnl)">{{ money(t.pnl) }}</td>
            <td>{{ t.why === 'stop' ? '止损' : t.why === 'exit' ? '回归' : '未平' }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>
