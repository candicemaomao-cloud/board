<script setup>
import { computed, onMounted, ref } from 'vue'
import { Refresh } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { api } from '../api'

const loading = ref(false)
const rows = ref([])
const stats = ref({ monthly: [] })
const status = ref('')
const symbol = ref('')
const expanded = ref(null)

const symbols = computed(() => [...new Set(rows.value.map((x) => x.symbol).filter(Boolean))])
const statusText = { pending: '等待三天复核', completed: '已复核', not_evaluable: '仅监控，不计成功率' }
const outcomeText = { target_hit: '达到目标且未先触发止损', stop_hit: '先触发止损', direction_right: '方向正确', direction_wrong: '方向错误', avoided_decline: '成功避开下跌', invalidated: '看跌判断失效' }

function timeText(value) {
  if (!value) return '—'
  const d = new Date(value)
  return Number.isNaN(d.getTime()) ? value : d.toLocaleString('zh-CN', { hour12: false })
}
function pct(value) {
  return value == null ? '—' : `${Number(value) > 0 ? '+' : ''}${Number(value).toFixed(2)}%`
}
function price(value) {
  if (value == null) return '—'
  const n = Number(value)
  return n >= 1000 ? n.toLocaleString(undefined, { maximumFractionDigits: 2 }) : n.toFixed(n >= 1 ? 4 : 6)
}
function range(values, formatter) {
  if (!values || values.low == null || values.high == null) return '—'
  return `${formatter(values.low)} ～ ${formatter(values.high)}`
}
function duration(row) {
  const values = row.forecast?.duration_bars
  if (!values || values.low == null || values.high == null) return '—'
  const unit = row.forecast?.interval === '1d' ? '天' : '根 K 线'
  return `${Math.max(1, Math.round(values.low))}～${Math.max(1, Math.round(values.high))} ${unit}`
}

async function load() {
  loading.value = true
  try {
    const params = {}
    if (status.value) params.status = status.value
    if (symbol.value) params.symbol = symbol.value
    const [list, summary] = await Promise.all([api.cryptoStrategySignals(params), api.cryptoStrategySignalSummary()])
    rows.value = list.items || []
    stats.value = summary || { monthly: [] }
  } catch (e) {
    ElMessage.error(e.message || '命中记录加载失败')
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="signal-page" v-loading="loading">
    <header class="signal-head">
      <div><span class="eyebrow">策略证据、到期复核与月度价值验证</span><h2>指标策略命中记录</h2><p>命中时冻结原始结论；三天后追加真实走势，原记录不会被回写。</p></div>
      <el-button :icon="Refresh" :loading="loading" @click="load">刷新</el-button>
    </header>

    <section class="summary-strip">
      <div><span>全部命中</span><b>{{ stats.total || 0 }}</b></div>
      <div><span>等待复核</span><b>{{ stats.pending || 0 }}</b></div>
      <div><span>已完成验证</span><b>{{ stats.completed || 0 }}</b></div>
      <div><span>累计成功率</span><b>{{ stats.success_rate == null ? '—' : `${stats.success_rate}%` }}</b></div>
      <div><span>本月成功率</span><b>{{ stats.monthly_success_rate == null ? '—' : `${stats.monthly_success_rate}%` }}</b><small>{{ stats.monthly_completed || 0 }} 条可验证记录</small></div>
    </section>

    <section class="method-note">
      <b>复核口径</b><span>看涨：三天收益为正且未先触发止损；看跌：三天收益为负且未触发失效位。震荡、方向冲突、复核未通过和行情失败不进入成功率分母。</span>
    </section>
    <section class="proof-note"><b>如何自证</b><div><span>1. 命中时冻结价格、指标、三段样本与证据编号</span><span>2. 监控、历史形态、样本量、价格风险分别过门槛，不把相关概率相乘</span><span>3. 三天后读取真实 K 线复核，月度只统计已到期且方向明确的记录</span><span>4. 新闻目前仅作背景，不在完成时间对齐回测前伪装成交易因子</span></div></section>

    <div class="filters">
      <el-select v-model="symbol" clearable placeholder="全部币种" @change="load"><el-option v-for="item in symbols" :key="item" :label="item" :value="item" /></el-select>
      <el-select v-model="status" clearable placeholder="全部状态" @change="load"><el-option label="等待复核" value="pending" /><el-option label="已复核" value="completed" /><el-option label="仅监控" value="not_evaluable" /></el-select>
    </div>

    <section class="records">
      <article v-for="row in rows" :key="row.id" class="record">
        <div class="record-main">
          <div><span class="id">#{{ row.id }} · {{ row.evidence_id || '无三段证据' }}</span><h3>{{ row.symbol }} · {{ row.strategy_name }}</h3><p>{{ row.verdict }}</p></div>
          <div class="record-result"><b :class="row.success === true ? 'up' : row.success === false ? 'down' : 'flat'">{{ row.success === true ? '验证成功' : row.success === false ? '验证失败' : statusText[row.review_status] }}</b><small>{{ outcomeText[row.outcome] || statusText[row.review_status] }}</small></div>
        </div>
        <div class="metrics">
          <div><span>命中时间</span><b>{{ timeText(row.created_at) }}</b></div><div><span>监控周期</span><b>{{ row.monitor_timeframe }}</b></div><div><span>当时价格</span><b>{{ price(row.entry_price) }}</b></div><div><span>方向</span><b>{{ row.direction }}</b></div><div><span>三天收益</span><b :class="Number(row.actual_return_pct) > 0 ? 'up' : Number(row.actual_return_pct) < 0 ? 'down' : ''">{{ pct(row.actual_return_pct) }}</b></div><div><span>复核时间</span><b>{{ row.reviewed_at ? timeText(row.reviewed_at) : timeText(row.review_due_at) }}</b></div>
        </div>
        <section v-if="row.forecast" class="forecast-panel">
          <div class="forecast-title"><div><span>历史路径预测区间</span><b>{{ row.forecast.direction }} · 观察后续 {{ row.forecast.horizon_bars }} 根 {{ row.forecast.interval === '1d' ? '日 K' : 'K 线' }}</b></div><small>历史匹配案例 25%～75% 分位，不是价格保证</small></div>
          <div class="forecast-grid">
            <div><span>上探空间</span><b class="up">{{ range(row.forecast.upside_probe_pct, pct) }}</b><small>{{ range(row.forecast.upside_probe_price, price) }} USDT</small></div>
            <div><span>下探空间</span><b class="down">{{ range(row.forecast.downside_probe_pct, pct) }}</b><small>{{ range(row.forecast.downside_probe_price, price) }} USDT</small></div>
            <div><span>到期收盘区间</span><b>{{ range(row.forecast.final_return_pct, pct) }}</b><small>中位 {{ pct(row.forecast.final_return_pct?.median) }} · {{ price(row.forecast.final_return_price?.median) }} USDT</small></div>
            <div><span>常见方向持续</span><b>{{ duration(row) }}</b><small>到达阶段极值的常见时间</small></div>
            <div><span>冻结目标 / 失效位</span><b>{{ price(row.target_price) }} / {{ price(row.stop_price) }}</b><small>策略生成时冻结，不随行情回写</small></div>
            <div><span>样本与共振</span><b>{{ row.forecast.sample_count }} 例 · {{ row.evidence?.consensus?.agreement || 0 }}/3 同向</b><small>{{ row.forecast.window_count }} 个同向阶段参与区间统计</small></div>
          </div>
        </section>
        <section v-else class="forecast-panel legacy">
          <div class="forecast-title"><div><span>冻结价格参考</span><b>目标 {{ price(row.target_price) }} · 失效 {{ price(row.stop_price) }}</b></div><small>旧记录未冻结完整后续路径；新命中会自动保存上探、下探和持续时间区间</small></div>
        </section>
        <div class="hits"><span v-for="item in row.hit_indicators" :key="item.id">{{ item.name }}</span></div>
        <div class="audit-grid"><div v-for="item in row.audit?.dimensions || []" :key="item.key" :class="`audit-${item.status}`"><span>{{ item.name }}</span><b>{{ item.status === 'passed' ? '已验证' : item.status === 'pending' ? '待验证' : '证据不足' }}</b><small>{{ item.detail }}</small></div></div>
        <button class="detail-toggle" type="button" @click="expanded = expanded === row.id ? null : row.id">{{ expanded === row.id ? '收起完整证据' : '查看当时完整策略与三段证据' }}</button>
        <pre v-if="expanded === row.id">{{ row.report_text }}</pre>
      </article>
      <p v-if="!rows.length" class="empty">暂无真实命中记录。新事件命中后会自动保存在这里，不补造旧记录。</p>
    </section>

    <section class="monthly">
      <div class="section-title"><h3>月度验证报表</h3><p>用于判断系统是否持续有价值，不以单次命中下结论。</p></div>
      <div class="month-row month-head"><span>月份</span><span>可验证记录</span><span>成功</span><span>成功率</span><span>平均三天收益</span></div>
      <div v-for="item in stats.monthly || []" :key="item.month" class="month-row"><b>{{ item.month }}</b><span>{{ item.completed }}</span><span>{{ item.successes }}</span><b>{{ item.success_rate }}%</b><span>{{ pct(item.avg_return_pct) }}</span></div>
      <p v-if="!(stats.monthly || []).length" class="empty">尚无到期复核数据</p>
    </section>
  </div>
</template>

<style scoped>
.signal-page{display:grid;gap:18px}.signal-head{display:flex;align-items:flex-end;justify-content:space-between;gap:20px;padding:4px}.signal-head h2{margin:4px 0;font-size:24px}.signal-head p,.section-title p{margin:0;color:var(--muted)}.eyebrow{color:var(--primary);font-size:11px}.summary-strip{display:grid;grid-template-columns:repeat(5,1fr);overflow:hidden;border:1px solid var(--line);border-radius:8px;background:var(--panel)}.summary-strip>div{display:grid;gap:5px;padding:16px;background:rgba(80,130,210,.05)}.summary-strip span,.summary-strip small,.metrics span{font-size:10px;color:var(--muted)}.summary-strip b{font-size:21px}.method-note{display:flex;gap:16px;padding:13px 16px;border-radius:8px;background:rgba(84,135,220,.1);font-size:11px;line-height:1.6}.method-note b{flex:0 0 auto;color:var(--primary)}.filters{display:flex;gap:10px}.filters .el-select{width:180px}.records{display:grid;gap:12px}.record{overflow:hidden;border:1px solid var(--line);border-radius:8px;background:var(--panel)}.record-main{display:flex;justify-content:space-between;gap:20px;padding:16px;background:rgba(80,130,210,.045)}.record-main h3{margin:4px 0 6px;font-size:15px}.record-main p{margin:0;color:var(--muted);font-size:11px}.id{font-family:var(--mono);font-size:9px;color:var(--faint)}.record-result{display:grid;align-content:center;gap:4px;text-align:right}.record-result small{color:var(--muted);font-size:9px}.metrics{display:grid;grid-template-columns:repeat(6,1fr);gap:1px;background:var(--line)}.metrics>div{display:grid;gap:5px;padding:12px;background:var(--panel)}.metrics b{font-size:11px}.hits{display:flex;flex-wrap:wrap;gap:7px;padding:12px 16px}.hits span{padding:4px 7px;border-radius:4px;background:rgba(84,135,220,.12);font-size:9px;color:#a9c5f7}.detail-toggle{width:100%;padding:10px 16px;border:0;background:rgba(255,255,255,.025);color:var(--primary);text-align:left;cursor:pointer}.record pre{margin:0;padding:16px;overflow:auto;background:#080d14;color:#c7d2e2;font:10px/1.65 var(--mono);white-space:pre-wrap}.monthly{overflow:hidden;border:1px solid var(--line);border-radius:8px;background:var(--panel)}.section-title{padding:16px;background:rgba(80,130,210,.045)}.section-title h3{margin:0 0 4px;font-size:15px}.month-row{display:grid;grid-template-columns:1.2fr repeat(4,1fr);padding:11px 16px;font-size:11px}.month-row:not(.month-head):nth-child(odd){background:rgba(80,130,210,.035)}.month-head{color:var(--muted);font-size:9px}.empty{margin:0;padding:28px;color:var(--muted);text-align:center}.up{color:#3ddc97}.down{color:#ff667a}.flat{color:#f4c46b}@media(max-width:900px){.summary-strip{grid-template-columns:repeat(2,1fr)}.metrics{grid-template-columns:repeat(2,1fr)}}@media(max-width:600px){.signal-head{align-items:flex-start;flex-direction:column}.record-main{flex-direction:column}.record-result{text-align:left}.month-row{grid-template-columns:1fr 1fr}.month-row>*:nth-child(n+3){display:none}}
.signal-page{font-size:14px}.signal-head h2{font-size:26px}.signal-head p,.section-title p{font-size:14px;line-height:1.6}.eyebrow{font-size:13px}.summary-strip>div{gap:7px;padding:18px}.summary-strip span,.summary-strip small,.metrics span{font-size:13px}.summary-strip b{font-size:23px}.method-note,.proof-note{display:flex;gap:16px;padding:15px 18px;border-radius:8px;background:rgba(84,135,220,.1);font-size:13px;line-height:1.7}.method-note b,.proof-note>b{flex:0 0 auto;color:var(--primary)}.proof-note div{display:grid;gap:4px}.record-main h3,.section-title h3{font-size:18px}.record-main p{font-size:14px;line-height:1.6}.id,.record-result small{font-size:12px}.metrics b{font-size:13px}.hits span{padding:6px 9px;font-size:12px}.detail-toggle{padding:13px 16px;font-size:13px}.record pre{font:13px/1.75 var(--mono)}.month-row{font-size:13px}.month-head{font-size:12px}.audit-grid{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:8px;padding:0 16px 14px}.audit-grid>div{display:grid;gap:5px;padding:10px;border-radius:6px;background:rgba(84,135,220,.07)}.audit-grid span,.audit-grid small{font-size:11px;color:var(--muted);line-height:1.4}.audit-grid b{font-size:13px}.audit-passed b{color:#3ddc97}.audit-pending b{color:#f4c46b}.audit-insufficient b,.audit-missing b{color:#ff667a}.forecast-panel{margin:14px 16px;padding:15px;border-radius:8px;background:rgba(84,135,220,.075)}.forecast-title{display:flex;align-items:flex-end;justify-content:space-between;gap:18px;margin-bottom:13px}.forecast-title>div{display:grid;gap:4px}.forecast-title span,.forecast-grid span{color:var(--muted);font-size:12px}.forecast-title b{font-size:16px}.forecast-title small,.forecast-grid small{color:var(--faint);font-size:11px;line-height:1.45}.forecast-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:9px}.forecast-grid>div{display:grid;gap:5px;padding:11px;border-radius:6px;background:rgba(5,12,20,.42)}.forecast-grid b{font-size:14px}.forecast-panel.legacy{margin-bottom:14px;background:rgba(244,196,107,.07)}.forecast-panel.legacy .forecast-title{margin:0}@media(max-width:1100px){.audit-grid,.forecast-grid{grid-template-columns:repeat(3,1fr)}}@media(max-width:700px){.forecast-grid{grid-template-columns:1fr 1fr}.forecast-title{align-items:flex-start;flex-direction:column}}@media(max-width:600px){.audit-grid{grid-template-columns:1fr 1fr}.proof-note{flex-direction:column}}
</style>
