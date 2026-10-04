<script setup>
import { computed, onMounted, ref } from 'vue'
import { Refresh, TrendCharts, List, Bell, Document } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { api } from '../api'

const emit = defineEmits(['navigate'])
const loading = ref(false)
const updatedAt = ref('')
const coins = ref([])
const strategies = ref([])
const studies = ref([])
const trades = ref([])
const account = ref({ total_amount: 0, available_usdt: 0 })
const sentiment = ref(null)
const news = ref(null)
const signalStats = ref({ monthly: [] })
const signals = ref([])
const errors = ref([])

const leaders = computed(() => [...coins.value].filter((x) => Number.isFinite(Number(x.change_24h))).sort((a, b) => Number(b.change_24h) - Number(a.change_24h)))
const watchCoins = computed(() => {
  const priority = new Map(strategies.value.map((row, index) => [String(row.symbol || '').toUpperCase(), index]))
  return [...coins.value].sort((a, b) => {
    const ai = priority.has(a.symbol) ? priority.get(a.symbol) : 999
    const bi = priority.has(b.symbol) ? priority.get(b.symbol) : 999
    return ai - bi || Number(a.sort_order || 0) - Number(b.sort_order || 0)
  })
})
const rising = computed(() => leaders.value.filter((x) => Number(x.change_24h) > 0).length)
const falling = computed(() => leaders.value.filter((x) => Number(x.change_24h) < 0).length)
const breadth = computed(() => {
  const total = rising.value + falling.value
  if (!total) return 50
  return Math.round((rising.value / total) * 100)
})
const activeStrategies = computed(() => strategies.value.filter((x) => x.enabled))
const hitStrategies = computed(() => activeStrategies.value.filter((x) => x.last_hit === true))
const strategyErrors = computed(() => strategies.value.filter((x) => x.last_error))
const openTrades = computed(() => trades.value.filter((x) => x.status === 'open' && x.trade_type !== 'leverage'))
const investedCost = computed(() => openTrades.value.reduce((sum, row) => sum + Number(row.entry_price || 0) * Number(row.remaining_quantity ?? row.quantity ?? 0), 0))
const holdingsValue = computed(() => openTrades.value.reduce((sum, row) => {
  const coin = coins.value.find((item) => item.symbol === row.symbol)
  const price = Number(coin?.price || row.entry_price || 0)
  return sum + price * Number(row.remaining_quantity ?? row.quantity ?? 0)
}, 0))
const unrealizedPnl = computed(() => holdingsValue.value - investedCost.value)
const realizedPnl = computed(() => trades.value.filter((x) => x.trade_type !== 'leverage').reduce((sum, row) => sum + Number(row.realized_pnl || 0), 0))
const newsSummary = computed(() => news.value?.sentiment || null)
const eventNews = computed(() => (news.value?.events || []).slice(0, 4))
const breadthSample = computed(() => rising.value + falling.value)
const latestSignal = computed(() => signals.value[0] || null)
const fearDuration = computed(() => {
  const rows = sentiment.value?.history || []
  if (!rows.length) return '历史不足'
  const bucket = (v) => Number(v) <= 24 ? '极度恐惧' : Number(v) <= 44 ? '恐惧' : Number(v) <= 55 ? '中性' : Number(v) <= 74 ? '贪婪' : '极度贪婪'
  const current = bucket(rows.at(-1)?.value ?? sentiment.value?.value)
  let days = 0
  for (const row of [...rows].reverse()) { if (bucket(row.value) !== current) break; days += 1 }
  return days ? `${current}持续 ${days} 天` : '历史不足'
})
const latestStudies = computed(() => studies.value.slice(0, 4))
const marketState = computed(() => {
  const fear = Number(sentiment.value?.value)
  if (breadth.value >= 65 && fear >= 45) return { label: '风险偏好回升', tone: 'up', note: '上涨币种占优，仍需等待策略信号确认。' }
  if (breadth.value <= 35 || (Number.isFinite(fear) && fear <= 24)) return { label: '风险偏好偏弱', tone: 'down', note: '优先控制仓位，避免仅因价格下跌而抄底。' }
  return { label: '市场分化', tone: 'flat', note: '方向不集中，重点观察策略新命中与历史形态复核。' }
})

function money(value) {
  const n = Number(value)
  if (!Number.isFinite(n)) return '—'
  if (Math.abs(n) >= 1000) return `$${n.toLocaleString(undefined, { maximumFractionDigits: 2 })}`
  if (Math.abs(n) >= 1) return `$${n.toFixed(2)}`
  return `$${n.toPrecision(4)}`
}

function pct(value) {
  const n = Number(value)
  if (!Number.isFinite(n)) return '—'
  return `${n > 0 ? '+' : ''}${n.toFixed(2)}%`
}

function tone(value) {
  const n = Number(value)
  if (!Number.isFinite(n) || n === 0) return 'flat'
  return n > 0 ? 'up' : 'down'
}

function timeText(value) {
  if (!value) return '尚未检查'
  const d = new Date(value)
  return Number.isNaN(d.getTime()) ? String(value) : d.toLocaleString('zh-CN', { hour12: false })
}

async function load() {
  loading.value = true
  errors.value = []
  const jobs = [
    ['币种行情', api.cryptoCoins({ with_quote: true })],
    ['指标策略', api.cryptoStrategies()],
    ['市场情绪', api.cryptoFearGreed({ limit: 14 })],
    ['币圈新闻', api.cryptoNews({ limit: 30 })],
    ['历史复盘', api.cryptoPatternStudies()],
    ['模拟交易', api.cryptoPaperTrades()],
    ['策略验证', api.cryptoStrategySignalSummary()],
    ['最新策略证据', api.cryptoStrategySignals({ limit: 5 })],
  ]
  const results = await Promise.allSettled(jobs.map((x) => x[1]))
  results.forEach((result, index) => {
    const name = jobs[index][0]
    if (result.status === 'rejected') {
      errors.value.push(`${name}：${result.reason?.message || '加载失败'}`)
      return
    }
    const data = result.value || {}
    if (name === '币种行情') coins.value = data.items || []
    if (name === '指标策略') strategies.value = data.items || []
    if (name === '市场情绪') sentiment.value = data
    if (name === '币圈新闻') news.value = data
    if (name === '历史复盘') studies.value = data.items || []
    if (name === '模拟交易') {
      trades.value = data.items || []
      account.value = data
    }
    if (name === '策略验证') signalStats.value = data
    if (name === '最新策略证据') signals.value = data.items || []
  })
  updatedAt.value = new Date().toLocaleTimeString('zh-CN', { hour12: false, hour: '2-digit', minute: '2-digit' })
  loading.value = false
  if (errors.value.length) ElMessage.warning(`部分数据未更新：${errors.value.join('；')}`)
}

onMounted(load)
</script>

<template>
  <div class="crypto-board" v-loading="loading">
    <header class="board-head">
      <div>
        <span class="eyebrow">市场、策略与复盘集中视图</span>
        <h2>虚拟币看板</h2>
        <p>先判断市场环境，再检查策略事件，最后进入历史形态复核；监控结果不直接等同交易建议。</p>
      </div>
      <div class="head-actions">
        <span>更新 {{ updatedAt || '—' }}</span>
        <el-button :icon="Refresh" :loading="loading" @click="load">刷新</el-button>
      </div>
    </header>

    <section class="market-strip">
      <div><span>市场状态</span><b :class="marketState.tone">{{ marketState.label }}</b><small>{{ marketState.note }}</small></div>
      <div><span>状态持续时间</span><b>{{ fearDuration }}</b><small>按全市场恐惧贪婪同区间连续天数</small></div>
      <div><span>关注币涨跌广度</span><b>{{ breadth }}%</b><small>{{ rising }} 涨 / {{ falling }} 跌 · 样本 {{ breadthSample }} 个</small></div>
      <div><span>全市场恐惧贪婪</span><b>{{ sentiment?.value ?? '—' }}</b><small>{{ sentiment?.label || '数据暂不可用' }}</small></div>
      <div><span>策略监控</span><b>{{ hitStrategies.length }} / {{ activeStrategies.length }}</b><small>当前命中 / 正在监听</small></div>
      <div><span>累计策略成功率</span><b>{{ signalStats.success_rate == null ? '待验证' : `${signalStats.success_rate}%` }}</b><small>{{ signalStats.completed || 0 }} 条已到期 / {{ signalStats.total || 0 }} 条命中</small></div>
      <div><span>账户总额</span><b>{{ money(account.total_amount) }}</b><small>模拟现货账户</small></div>
    </section>

    <p v-if="errors.length" class="partial-error">部分数据暂不可用，其余模块已正常显示。刷新后仍失败时请查看对应模块。</p>

    <section class="board-grid">
      <div class="board-section algorithm-section">
        <div class="section-head"><div><h3>核心算法结果与证据状态</h3><p>只展示已冻结的命中证据；没有到期样本时不宣称策略有效</p></div><el-button link type="primary" @click="emit('navigate', 'cryptoStrategySignals')">查看完整证据</el-button></div>
        <div v-if="latestSignal" class="algorithm-grid"><div><span>最新结论</span><b>{{ latestSignal.symbol }} · {{ latestSignal.direction }}</b><small>{{ latestSignal.verdict }}</small></div><div><span>三段形态复核</span><b>{{ latestSignal.evidence?.consensus?.agreement || 0 }}/3 同向</b><small>综合分 {{ latestSignal.evidence?.consensus?.score || 0 }}%</small></div><div><span>证据完整度</span><b>{{ (latestSignal.audit?.dimensions || []).filter((x) => x.status === 'passed').length }}/6</b><small>监控、三段、样本、风险、回测、真实结果</small></div><div><span>真实走势验证</span><b>{{ latestSignal.review_status === 'completed' ? (latestSignal.success ? '成功' : '失败') : '等待三天复核' }}</b><small>证据号 {{ latestSignal.evidence_id || '—' }}</small></div></div>
        <p v-else class="empty">暂无冻结的策略命中证据，不能生成算法有效性结论</p>
        <div class="metric-definitions"><p><b>涨跌广度口径：</b>当前关注币列表中，24 小时涨幅为正的币种数 ÷ 有涨跌数据且非零的币种数，不代表全市场。</p><p><b>恐惧贪婪口径：</b>外部提供的整个加密市场情绪指标，不是单一币种信号，也不直接决定买卖。</p></div>
      </div>
      <div class="board-section market-section">
        <div class="section-head"><div><h3>关注币种</h3><p>实时价格与 24 小时变化</p></div><el-button link type="primary" @click="emit('navigate', 'cryptoMarket')">全部币种</el-button></div>
        <div class="coin-table">
          <div v-for="coin in watchCoins" :key="coin.id" class="coin-row">
            <div><b>{{ coin.symbol }}</b><span>{{ coin.name }}</span></div>
            <strong>{{ money(coin.price) }}</strong>
            <em :class="tone(coin.change_24h)">{{ pct(coin.change_24h) }}</em>
          </div>
          <p v-if="!watchCoins.length" class="empty">暂无币种行情</p>
        </div>
      </div>

      <div class="board-section strategy-section">
        <div class="section-head"><div><h3>策略雷达</h3><p>新命中才进入历史复核</p></div><el-button link type="primary" @click="emit('navigate', 'cryptoStrategy')">策略管理</el-button></div>
        <div class="strategy-summary">
          <div><span>正在监听</span><b>{{ activeStrategies.length }}</b></div>
          <div><span>当前命中</span><b :class="hitStrategies.length ? 'up' : ''">{{ hitStrategies.length }}</b></div>
          <div><span>策略计算异常</span><b :class="strategyErrors.length ? 'down' : ''">{{ strategyErrors.length }}</b><small>行情、K线或指标未完成</small></div>
        </div>
        <div class="strategy-rows">
          <div v-for="row in strategies.slice(0, 5)" :key="row.id">
            <span><b>{{ row.symbol }}</b>{{ row.timeframe }} · {{ (row.indicators || []).length }} 指标</span>
            <em :class="row.last_error ? 'down' : row.last_hit ? 'up' : 'flat'">{{ row.last_error ? '计算失败' : row.last_hit ? '需复核' : '观察' }}</em>
            <small>{{ timeText(row.last_checked_at) }}<template v-if="row.last_error"> · {{ row.last_error }}</template></small>
          </div>
          <p v-if="!strategies.length" class="empty">尚未创建策略</p>
        </div>
      </div>

      <div class="board-section news-section">
        <div class="section-head"><div><h3>事件与新闻</h3><p>系统已完成方向分类与重大事件筛选</p></div><el-button link type="primary" @click="emit('navigate', 'cryptoNews')">新闻分析</el-button></div>
        <div class="news-bias">
          <span>综合情绪</span><b :class="newsSummary?.overall === '偏多' ? 'up' : newsSummary?.overall === '偏空' ? 'down' : 'flat'">{{ newsSummary?.overall || '—' }}</b>
          <small>偏多 {{ newsSummary?.counts?.bullish || 0 }} · 中性 {{ newsSummary?.counts?.neutral || 0 }} · 偏空 {{ newsSummary?.counts?.bearish || 0 }}</small>
        </div>
        <div class="event-list">
          <a v-for="item in eventNews" :key="item.url || item.title" :href="item.url" target="_blank" rel="noreferrer">
            <span>{{ item.title }}</span><small>{{ item.source }} · {{ item.date }}</small>
          </a>
          <p v-if="!eventNews.length" class="empty">暂无重大事件</p>
        </div>
      </div>

      <div class="board-section funds-section">
        <div class="section-head"><div><h3>模拟账户资金</h3><p>现货资金、持仓占用与已实现/浮动盈亏</p></div><el-button link type="primary" @click="emit('navigate', 'cryptoPattern')">交易与订单</el-button></div>
        <div class="funds-grid">
          <div><span>账户总额</span><b>{{ money(account.total_amount) }}</b><small>与左侧账户口径一致</small></div>
          <div><span>可用资金</span><b>{{ money(account.available_usdt) }}</b><small>可用于模拟买入</small></div>
          <div><span>持仓成本</span><b>{{ money(investedCost) }}</b><small>{{ openTrades.length }} 笔现货持仓</small></div>
          <div><span>持仓市值</span><b>{{ money(holdingsValue) }}</b><small>按当前关注币价格估算</small></div>
          <div><span>浮动盈亏</span><b :class="tone(unrealizedPnl)">{{ money(unrealizedPnl) }}</b><small>尚未卖出的估算盈亏</small></div>
          <div><span>已实现盈亏</span><b :class="tone(realizedPnl)">{{ money(realizedPnl) }}</b><small>来自已成交卖出记录</small></div>
        </div>
      </div>

      <div class="board-section review-section">
        <div class="section-head"><div><h3>历史判断复盘</h3><p>保存当时判断，并用真实后续走势验证</p></div><el-button link type="primary" @click="emit('navigate', 'cryptoPattern')">进入历史形态</el-button></div>
        <div class="review-stats">
          <div><span>保存复盘</span><b>{{ studies.length }}</b></div>
          <div><span>已验证</span><b>{{ studies.filter((x) => x.status === 'completed').length }}</b></div>
          <div><span>等待验证</span><b>{{ studies.filter((x) => x.status !== 'completed').length }}</b></div>
          <div><span>验证成功</span><b>{{ studies.filter((x) => x.success === true).length }}</b></div>
        </div>
        <div class="study-list">
          <div v-for="row in latestStudies" :key="row.id">
            <b>{{ row.symbol }} · {{ row.interval }}</b><span>{{ row.direction }} · {{ Number(row.probability || 0).toFixed(1) }}%</span><em :class="row.success === true ? 'up' : row.success === false ? 'down' : 'flat'">{{ row.success === true ? '验证成功' : row.success === false ? '验证失败' : '等待验证' }}</em>
          </div>
          <p v-if="!latestStudies.length" class="empty">暂无复盘记录</p>
        </div>
      </div>

      <div class="board-section validation-section">
        <div class="section-head"><div><h3>指标策略价值验证</h3><p>三天到期复核与月度成功率，只统计方向明确的可验证结论</p></div><el-button link type="primary" @click="emit('navigate', 'cryptoStrategySignals')">查看命中记录</el-button></div>
        <div class="review-stats">
          <div><span>累计命中</span><b>{{ signalStats.total || 0 }}</b></div>
          <div><span>等待三天复核</span><b>{{ signalStats.pending || 0 }}</b></div>
          <div><span>已完成验证</span><b>{{ signalStats.completed || 0 }}</b></div>
          <div><span>累计成功率</span><b>{{ signalStats.success_rate == null ? '—' : `${signalStats.success_rate}%` }}</b></div>
        </div>
        <div class="study-list">
          <div v-for="item in (signalStats.monthly || []).slice(0, 3)" :key="item.month"><b>{{ item.month }}</b><span>{{ item.completed }} 条可验证 · {{ item.successes }} 条成功</span><em>{{ item.success_rate }}%</em></div>
          <p v-if="!(signalStats.monthly || []).length" class="empty">新命中将在三天后形成第一批验证数据</p>
        </div>
      </div>
    </section>

    <section class="quick-actions">
      <button type="button" @click="emit('navigate', 'cryptoMarket')"><el-icon><List /></el-icon><span><b>管理币种</b><small>新增币种与查看行情</small></span></button>
      <button type="button" @click="emit('navigate', 'cryptoPattern')"><el-icon><TrendCharts /></el-icon><span><b>形态分析</b><small>匹配历史并保存复盘</small></span></button>
      <button type="button" @click="emit('navigate', 'cryptoStrategy')"><el-icon><Bell /></el-icon><span><b>策略监控</b><small>检查事件与推送状态</small></span></button>
      <button type="button" @click="emit('navigate', 'cryptoNews')"><el-icon><Document /></el-icon><span><b>新闻研判</b><small>查看系统分析后的事件</small></span></button>
    </section>
  </div>
</template>

<style scoped>
.crypto-board{display:grid;gap:14px}.board-head{display:flex;align-items:flex-end;justify-content:space-between;gap:24px;padding-bottom:16px;border-bottom:1px solid var(--line)}.board-head h2{margin:4px 0;font-size:24px}.board-head p,.section-head p{margin:0;color:var(--muted);line-height:1.5}.eyebrow{font-size:11px;color:var(--primary)}.head-actions{display:flex;align-items:center;gap:12px}.head-actions>span{font-size:11px;color:var(--muted)}.market-strip{display:grid;grid-template-columns:1.3fr repeat(4,1fr);border:1px solid var(--line);background:var(--panel)}.market-strip>div{display:grid;gap:5px;min-height:92px;padding:14px 16px;border-right:1px solid var(--line)}.market-strip>div:last-child{border-right:0}.market-strip span,.market-strip small{font-size:10px;color:var(--muted)}.market-strip b{font-size:22px}.partial-error{margin:0;padding:9px 12px;border-left:3px solid #f4c46b;background:#191811;color:#d7c18c;font-size:11px}.board-grid{display:grid;grid-template-columns:1.2fr .8fr;gap:14px}.board-section{min-width:0;border-top:1px solid var(--line);background:var(--panel)}.section-head{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:14px 16px;border-bottom:1px solid var(--line)}.section-head h3{margin:0 0 3px;font-size:15px}.section-head p{font-size:10px}.coin-table{display:grid}.coin-row{display:grid;grid-template-columns:1fr auto 74px;align-items:center;gap:12px;padding:10px 16px;border-bottom:1px solid var(--line)}.coin-row:last-child{border-bottom:0}.coin-row>div{display:flex;align-items:baseline;gap:8px}.coin-row span{font-size:10px;color:var(--muted)}.coin-row strong{font-family:var(--mono);font-size:12px}.coin-row em{text-align:right;font-style:normal;font-weight:700;font-size:12px}.strategy-summary,.review-stats{display:grid;grid-template-columns:repeat(3,1fr);border-bottom:1px solid var(--line)}.strategy-summary>div,.review-stats>div{display:grid;gap:4px;padding:12px 14px;border-right:1px solid var(--line)}.strategy-summary>div:last-child,.review-stats>div:last-child{border-right:0}.strategy-summary span,.review-stats span{font-size:9px;color:var(--muted)}.strategy-summary b,.review-stats b{font-size:18px}.strategy-rows>div{display:grid;grid-template-columns:1fr auto;gap:4px 10px;padding:10px 14px;border-bottom:1px solid var(--line)}.strategy-rows span{display:flex;gap:8px;color:var(--muted);font-size:10px}.strategy-rows em{font-style:normal;font-size:10px}.strategy-rows small{grid-column:1/-1;color:var(--faint);font-size:9px}.news-bias{display:grid;grid-template-columns:auto auto 1fr;align-items:baseline;gap:10px;padding:12px 16px;border-bottom:1px solid var(--line)}.news-bias span,.news-bias small{font-size:10px;color:var(--muted)}.news-bias small{text-align:right}.event-list{display:grid}.event-list a{display:grid;gap:4px;padding:10px 16px;border-bottom:1px solid var(--line);color:var(--text);text-decoration:none}.event-list a:hover{background:rgba(255,255,255,.03)}.event-list span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:11px}.event-list small{color:var(--muted);font-size:9px}.review-stats{grid-template-columns:repeat(4,1fr)}.study-list>div{display:grid;grid-template-columns:1fr auto auto;align-items:center;gap:10px;padding:10px 16px;border-bottom:1px solid var(--line);font-size:10px}.study-list span{color:var(--muted)}.study-list em{font-style:normal}.quick-actions{display:grid;grid-template-columns:repeat(4,1fr);border:1px solid var(--line)}.quick-actions button{display:flex;align-items:center;gap:10px;padding:13px 15px;border:0;border-right:1px solid var(--line);background:transparent;color:var(--text);text-align:left;cursor:pointer}.quick-actions button:last-child{border-right:0}.quick-actions button:hover{background:rgba(255,255,255,.04)}.quick-actions .el-icon{font-size:18px;color:var(--primary)}.quick-actions span{display:grid;gap:3px}.quick-actions small{font-size:9px;color:var(--muted)}.empty{margin:0;padding:24px;color:var(--muted);text-align:center;font-size:11px}.up{color:#3ddc97!important}.down{color:#ff667a!important}.flat{color:#f4c46b!important}@media(max-width:1100px){.market-strip{grid-template-columns:repeat(3,1fr)}.market-strip>div{border-bottom:1px solid var(--line)}.board-grid{grid-template-columns:1fr}.quick-actions{grid-template-columns:1fr 1fr}}@media(max-width:700px){.board-head{align-items:flex-start;flex-direction:column}.market-strip{grid-template-columns:1fr 1fr}.quick-actions{grid-template-columns:1fr}.quick-actions button{border-right:0;border-bottom:1px solid var(--line)}.review-stats{grid-template-columns:1fr 1fr}.news-bias{grid-template-columns:1fr auto}.news-bias small{grid-column:1/-1;text-align:left}}
.crypto-board{gap:20px}
.board-head{padding:4px 4px 20px}
.market-strip,.board-section,.quick-actions{border:1px solid var(--line);border-radius:8px;overflow:hidden}
.board-grid{gap:20px}
.board-section{border-top:1px solid var(--line)}
.section-head{padding:17px 18px}
.coin-row,.strategy-rows>div,.event-list a,.study-list>div{padding-left:18px;padding-right:18px}
.funds-section{grid-column:1/-1}
.funds-grid{display:grid;grid-template-columns:repeat(6,minmax(0,1fr))}
.funds-grid>div{display:grid;gap:5px;min-height:92px;padding:15px 18px;border-right:1px solid var(--line)}
.funds-grid>div:last-child{border-right:0}
.funds-grid span,.funds-grid small{font-size:9px;color:var(--muted)}
.funds-grid b{font-size:18px}
.market-strip{grid-template-columns:1.3fr repeat(6,1fr)}
.algorithm-section,.funds-section,.news-section{grid-column:1/-1}.algorithm-section{order:1}.funds-section{order:2}.market-section{order:3}.strategy-section{order:4}.validation-section{order:5}.review-section{order:6}.news-section{order:7}
.algorithm-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:1px;background:var(--line)}.algorithm-grid>div{display:grid;gap:6px;min-height:92px;padding:16px 18px;background:var(--panel)}.algorithm-grid span,.algorithm-grid small{font-size:12px;color:var(--muted);line-height:1.45}.algorithm-grid b{font-size:17px}.metric-definitions{display:grid;grid-template-columns:1fr 1fr;gap:12px;padding:14px 18px;background:rgba(84,135,220,.06)}.metric-definitions p{margin:0;color:var(--muted);font-size:12px;line-height:1.6}.metric-definitions b{color:var(--text)}
.market-strip span,.market-strip small,.funds-grid span,.funds-grid small{font-size:12px}.section-head p,.coin-row span,.strategy-rows span,.strategy-rows em,.strategy-rows small,.news-bias span,.news-bias small,.event-list span,.event-list small,.study-list>div,.quick-actions small{font-size:12px;line-height:1.5}.section-head h3{font-size:17px}.coin-row strong,.coin-row em{font-size:13px}.strategy-summary span,.review-stats span{font-size:12px}.empty{font-size:13px}
@media(max-width:1100px){.funds-grid{grid-template-columns:repeat(3,1fr)}.funds-grid>div{border-bottom:1px solid var(--line)}}
@media(max-width:1100px){.algorithm-grid{grid-template-columns:1fr 1fr}.metric-definitions{grid-template-columns:1fr}}
@media(max-width:700px){.funds-grid,.algorithm-grid{grid-template-columns:1fr 1fr}.crypto-board{gap:14px}.board-grid{gap:14px}}
</style>
