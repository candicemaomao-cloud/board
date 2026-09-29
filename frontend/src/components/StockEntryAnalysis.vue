<script setup>
import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from '../api'
import { can } from '../auth'
const props = defineProps({
  stock: { type: Object, required: true },
  allowRecord: { type: Boolean, default: true },
})
const emit = defineEmits(['record', 'price-saved'])
const horizon = ref(10), cost = ref(20), borrow = ref(5)
const result = ref(null), busy = ref(false), error = ref('')
const savingSide = ref(''), saveError = ref(''), savedSide = ref('')
let version = 0
const stale = computed(() => result.value && (result.value.horizon !== horizon.value || result.value.cost_bps !== cost.value || result.value.borrow_pct !== borrow.value))
const fmt = value => value == null ? '—' : Number(value).toLocaleString('en-US', { maximumFractionDigits: 2 })
const price = value => value == null ? '—' : `$${fmt(value)}`
const pct = value => value == null ? '—' : `${fmt(value)}%`
watch(() => [props.stock.id, props.stock.symbol], () => { version++; result.value = null; busy.value = false; error.value = ''; savingSide.value = ''; saveError.value = ''; savedSide.value = '' })
async function run() {
  const request = ++version
  busy.value = true; error.value = ''; result.value = null
  try {
    const body = { horizon: horizon.value, cost_bps: cost.value, borrow_pct: borrow.value }
    const data = props.stock.id ? await api.stockEntryAnalysis(props.stock.id, body) : await api.stockEntryAnalysisBySymbol(props.stock.symbol, body)
    if (request === version) result.value = data
  } catch (e) { if (request === version) error.value = e.message }
  finally { if (request === version) busy.value = false }
}
async function savePrice(plan) {
  if (!plan.entry || stale.value || savingSide.value) return
  savingSide.value = plan.side; saveError.value = ''; savedSide.value = ''
  try {
    const body = {
      symbol: props.stock.symbol,
      prev_close: props.stock.prev_close ?? null,
      prev_open: props.stock.prev_open ?? null,
      prev_low: props.stock.prev_low ?? null,
      prev_high: props.stock.prev_high ?? null,
      current_price: props.stock.current_price ?? null,
      gamma_low: props.stock.gamma_low ?? null,
      gamma_high: props.stock.gamma_high ?? null,
      regression_line: props.stock.regression_line ?? null,
      max_pain: props.stock.max_pain ?? null,
      short_entry_price: plan.side === 'short' ? plan.entry : props.stock.short_entry_price ?? null,
      long_entry_price: plan.side === 'long' ? plan.entry : props.stock.long_entry_price ?? null,
      is_potential: !!props.stock.is_potential,
      market_direction: props.stock.market_direction || '横盘',
      sector: props.stock.sector || '其他',
      events: props.stock.events || null,
    }
    const updated = await api.updateDailyWatch(props.stock.id, body)
    savedSide.value = plan.side
    emit('price-saved', updated)
    ElMessage.success(`${props.stock.symbol} 的${plan.side === 'long' ? '多买入价' : '空买入价'}已保存为 ${price(plan.entry)}`)
  } catch (e) {
    saveError.value = e.message
    ElMessage.error(e.message || '保存失败')
  }
  finally { savingSide.value = '' }
}
function record() {
  const d = result.value
  const rows = d.sides.map(p => `${p.side === 'long' ? '买入' : '做空'}：${p.status}；区间 ${p.zone?.map(price).join(' — ') || '—'}；参考挂单 ${price(p.entry)}；止损 ${price(p.stop)}；目标 ${price(p.target)}`)
  emit('record', `【${props.stock.symbol} 买卖价分析 · ${d.horizon} 个交易日】\n截至 ${d.asof} · ${d.method} · 趋势 ${d.trend}\n${rows.join('\n')}\n历史模拟：往返成本 ${d.cost_bps} bp，做空年化借券成本假设 ${d.borrow_pct}%。候选价仅供下一交易日参考，非最优价格保证。`)
}
</script>

<template>
  <section id="stock-entry" class="panel entry-panel">
    <div class="entry-heading"><div><h2>买卖价分析</h2><p class="sub">选择持有周期后计算候选区间，同时查看止损、目标和历史模拟。</p></div><span class="model-tag">规则模型 · 日线</span></div>
    <form class="entry-controls" @submit.prevent="run">
      <label>持有周期<span class="control-shell select-shell"><select v-model.number="horizon" :disabled="busy"><option :value="3">短线 · 3 个交易日</option><option :value="10">波段 · 10 个交易日</option><option :value="30">中期 · 30 个交易日</option><option :value="60">中期 · 60 个交易日</option></select><svg class="select-chevron" viewBox="0 0 20 20" aria-hidden="true"><path d="m6 8 4 4 4-4" /></svg></span></label>
      <label>往返手续费及滑点<span class="control-shell"><input v-model.number="cost" aria-label="往返手续费及滑点（bp）" type="number" min="0" max="500" step="1" required :disabled="busy" /><span class="input-unit" aria-hidden="true">bp</span></span></label>
      <label>做空年化借券成本<span class="control-shell"><input v-model.number="borrow" aria-label="做空年化借券成本（%）" type="number" min="0" max="200" step="0.1" required :disabled="busy" /><span class="input-unit" aria-hidden="true">%</span></span></label>
      <button class="btn btn-primary" :disabled="busy">{{ busy ? '计算中…' : '计算买卖价格' }}</button>
    </form>
    <p class="sub">1 bp = 0.01%；成本为可调整的模拟假设。日内周期需分钟级成交验证，当前不提供。</p>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <p v-if="!result && !busy && !error" class="entry-empty">选择周期并点击计算，生成这只股票的多空候选方案。</p>
    <p v-if="busy" role="status" class="sub">正在读取完整日线并进行历史模拟…</p>
    <p v-if="stale" class="error">参数已更改，请重新计算；下方仍为上次结果。</p>
    <template v-if="result">
      <div class="entry-meta"><span>截至 {{ result.asof }} · {{ result.source }}</span><span>持有 {{ result.horizon }} 个交易日</span><span>趋势：{{ result.trend }}</span><span>参考收盘价 {{ price(result.price) }}</span></div>
      <div class="entry-cards">
        <article v-for="plan in result.sides" :key="plan.side" class="entry-card" :class="plan.side">
          <div class="entry-heading"><h3>{{ plan.side === 'long' ? '买入候选' : '做空候选' }}</h3><span class="status">{{ plan.status }} · {{ plan.history.trades }} 笔</span></div>
          <p class="entry-zone">{{ plan.zone ? plan.zone.map(price).join(' — ') : '暂无有效区间' }}</p>
          <p class="sub">{{ plan.reason }}</p>
          <dl class="entry-prices"><div><dt>参考限价</dt><dd>{{ price(plan.entry) }}</dd></div><div><dt>止损价</dt><dd>{{ price(plan.stop) }}</dd></div><div><dt>目标价</dt><dd>{{ price(plan.target) }}</dd></div><div><dt>毛收益 / 风险</dt><dd>{{ plan.reward_risk ? `${plan.reward_risk} : 1` : '—' }}</dd></div></dl>
          <p class="entry-condition">{{ plan.side === 'long' ? '仅当当前趋势不是下降趋势时考虑回调限价；开盘跌穿止损则取消。' : '仅当当前趋势不是上升趋势时考虑反弹限价；开盘越过止损或无法借券则取消。' }} 挂单下一交易日有效，未成交需重算。</p>
          <h4>历史模拟 · 固定规则</h4>
          <dl class="history-grid"><div><dt>成交 / 候选次数</dt><dd>{{ plan.history.trades }} / {{ plan.history.opportunities }}</dd></div><div><dt>成交率</dt><dd>{{ pct(plan.history.fill_rate) }}</dd></div><div><dt>净盈利交易占比</dt><dd>{{ pct(plan.history.win_rate) }}</dd></div><div><dt>每笔平均净收益</dt><dd>{{ pct(plan.history.mean_return_pct) }}</dd></div><div><dt>平均风险倍数</dt><dd>{{ fmt(plan.history.mean_r) }} R</dd></div><div><dt>最差单笔净收益</dt><dd>{{ pct(plan.history.worst_return_pct) }}</dd></div></dl>
          <p v-if="plan.history.trades < 20" class="sample-note">当前只有 {{ plan.history.trades }} 笔历史成交，未达到 20 笔验证门槛。候选价仍可保存，历史统计暂不代表稳定胜率。</p>
          <div class="save-price-row">
            <p v-if="savedSide === plan.side" class="save-ok" role="status">已保存到股票列表的“{{ plan.side === 'long' ? '多买入价' : '空买入价' }}”</p>
            <button
              v-if="can('btn.daily_watch.write') && stock.id"
              class="btn save-price-btn"
              type="button"
              :disabled="!plan.entry || stale || !!savingSide"
              @click="savePrice(plan)"
            >
              <svg v-if="savingSide !== plan.side" viewBox="0 0 20 20" aria-hidden="true"><path d="M4 3h10l2 2v12H4zM7 3v5h6V3M7 13h6" /></svg>
              {{ savingSide === plan.side ? '保存中…' : savedSide === plan.side ? `已保存 ${price(plan.entry)}` : `保存为${plan.side === 'long' ? '多买入价' : '空买入价'}` }}
            </button>
          </div>
        </article>
      </div>
      <p v-if="saveError" class="error" role="alert">{{ saveError }}</p>
      <div class="entry-meta"><span>回归参考 {{ price(result.center) }}</span><span>ATR（14日）{{ price(result.atr) }}</span><span>近20日低点 {{ price(result.support) }}</span><span>近20日高点 {{ price(result.resistance) }}</span><span>回归窗口 {{ result.window }} 根 · 可用 {{ result.bars }} 根日线</span></div>
      <details class="entry-method"><summary>计算依据、历史模拟与限制</summary><p>使用 Theil–Sen 稳健回归与 MAD 残差波动估计；通道宽度取 1.5 倍残差波动与 ATR 的较大值。止损距限价 1.5 ATR，目标距限价 3 ATR；区间为限价上下 0.2 ATR。历史模拟按参考限价成交，不按整个区间成交。近20日高低点仅作参考，不参与加权。</p><ul><li v-for="text in result.limits" :key="text">{{ text }}</li></ul></details>
      <button v-if="allowRecord" class="btn" :disabled="stale" @click="record">写入我的分析草稿</button>
    </template>
  </section>
</template>

<style scoped>
.entry-panel { margin: 0 0 20px; scroll-margin-top: 20px; }.entry-heading { display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap; } h2 { margin:0;font-size:18px; } h3 { margin:0;font-size:17px; }.model-tag,.status { font-size:12px;color:#a8bde6;background:rgba(130,165,255,.09);padding:6px 10px;border-radius:20px; }
.entry-controls { display:flex;gap:14px;align-items:flex-end;flex-wrap:wrap;margin:18px 0 10px; }.entry-controls label { display:grid;gap:8px;font-size:12px;color:var(--muted);flex:1;min-width:180px; }.entry-controls input,.entry-controls select { width:100%;box-sizing:border-box; }.entry-empty { padding:24px 0;color:var(--muted); }.entry-meta { display:flex;gap:16px;flex-wrap:wrap;margin:18px 0;color:var(--muted);font-size:12px; }
.entry-cards { display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px; }.entry-card { border:1px solid var(--line);border-top:3px solid #56c5ab;border-radius:14px;padding:20px;background:rgba(10,17,26,.4);min-width:0; }.entry-card.short { border-top-color:#e88ba6; }.entry-zone { font-size:clamp(19px,2vw,27px);font-weight:600;overflow-wrap:anywhere;font-variant-numeric:tabular-nums; }.entry-prices,.history-grid { display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px; } dt { font-size:12px;color:var(--muted); } dd { margin:6px 0 0;font-variant-numeric:tabular-nums; } .entry-condition,.entry-method,.sample-note { font-size:12px;line-height:1.8;color:var(--muted); }.sample-note { color:#d9b879; }.entry-method { margin:18px 0; }.entry-method summary { cursor:pointer; } h4 { border-top:1px solid var(--line);padding-top:16px;font-size:14px; }
.save-price-row { min-height: 40px; display:flex;align-items:center;justify-content:space-between;gap:12px;margin-top:16px;padding-top:16px;border-top:1px solid var(--line); }.save-price-row:has(.save-price-btn:only-child) { justify-content:flex-end; }.save-price-btn { display:inline-flex;align-items:center;justify-content:center;gap:7px;min-width:150px;height:38px;border-radius:9px; }.save-price-btn svg { width:14px;fill:none;stroke:currentColor;stroke-width:1.5;stroke-linecap:round;stroke-linejoin:round; }.save-ok { margin:0;color:#4dd6ad;font-size:12px;line-height:1.5; }
@media(max-width:850px) { .entry-cards { grid-template-columns:1fr; } }

.entry-controls { display: grid; grid-template-columns: minmax(210px, 1.25fr) repeat(2, minmax(170px, 1fr)) auto; gap: 16px; margin: 24px 0 14px; }
.entry-controls label { min-width: 0; gap: 10px; font-size: 13px; font-weight: 500; }
.control-shell { position: relative; display: flex; align-items: center; min-width: 0; }
.entry-controls input, .entry-controls select {
  appearance: none; -webkit-appearance: none; color-scheme: dark; height: 46px; margin: 0;
  border: 1px solid #2c3a4d; border-radius: 10px; background: #0d1620; color: #e5edf8;
  padding: 0 48px 0 14px; font: inherit; font-size: 14px; font-variant-numeric: tabular-nums;
  outline: none; transition: border-color .16s ease, box-shadow .16s ease, background .16s ease;
}
.entry-controls select { cursor: pointer; }
.entry-controls option { background: #111c29; color: #e5edf8; }
.entry-controls input:hover:not(:disabled), .entry-controls select:hover:not(:disabled) { border-color: #4d6383; background: #101c29; }
.entry-controls input:focus, .entry-controls select:focus { border-color: #82a5ff; box-shadow: 0 0 0 3px rgba(130,165,255,.14); }
.entry-controls input:disabled, .entry-controls select:disabled { opacity: .55; cursor: not-allowed; }
.entry-controls input[type="number"] { -moz-appearance: textfield; }
.entry-controls input::-webkit-inner-spin-button, .entry-controls input::-webkit-outer-spin-button { -webkit-appearance: none; margin: 0; }
.input-unit { position: absolute; right: 14px; color: #8395ae; font-size: 12px; pointer-events: none; }
.select-chevron { position: absolute; right: 14px; width: 18px; height: 18px; fill: none; stroke: #93a9c6; stroke-width: 1.6; stroke-linecap: round; stroke-linejoin: round; pointer-events: none; }
.entry-controls > .btn { height: 46px; padding: 0 22px; border-radius: 10px; white-space: nowrap; }
@media(max-width: 1100px) { .entry-controls { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
@media(max-width: 600px) { .entry-controls { grid-template-columns: 1fr; gap: 14px; }.entry-controls > .btn { width: 100%; margin-top: 4px; } }
</style>
