<script setup>
import { computed, ref, watch } from 'vue'
import { api } from '../api'
import MacroChart from './MacroChart.vue'

const props = defineProps({ initialSymbol: { type: String, default: '' } })
const symbol = ref('')
const expiration = ref('')
const riskFreeRatePct = ref(4.5)
const loading = ref(false)
const error = ref('')
const data = ref(null)

const AXIS_LABEL = { color: '#8b9bb0', fontSize: 11 }
const AXIS_LINE = { lineStyle: { color: 'rgba(148,176,210,0.2)' } }
const SPLIT_LINE = { lineStyle: { color: 'rgba(148,176,210,0.08)' } }
const TOOLTIP_BASE = {
  trigger: 'axis',
  backgroundColor: '#101820',
  borderColor: 'rgba(148,176,210,0.2)',
  textStyle: { color: '#e9f0f7' },
}

async function run() {
  error.value = ''
  const code = symbol.value.trim().toUpperCase()
  if (!code) {
    error.value = '请输入股票代码'
    return
  }
  symbol.value = code
  loading.value = true
  try {
    data.value = await api.optionsAnalysis({
      symbol: code,
      expiration: expiration.value || undefined,
      risk_free_rate: Number(riskFreeRatePct.value || 4.5) / 100,
    })
    expiration.value = data.value.expiration
  } catch (e) {
    data.value = null
    error.value = e.message
  } finally {
    loading.value = false
  }
}

function onExpirationChange() {
  if (data.value) run()
}
watch(() => props.initialSymbol, (value) => {
  if (value) { symbol.value = value; expiration.value = ''; run() }
}, { immediate: true })

const iv = computed(() => data.value?.iv)
const range = computed(() => data.value?.expected_range)
const maxPain = computed(() => data.value?.max_pain)
const diag = computed(() => data.value?.diagnostics)
const itmBreakdown = computed(() => data.value?.itm_breakdown)
const itmExtremes = computed(() => data.value?.itm_extremes)
const gammaFlip = computed(() => data.value?.gamma_flip)
const bl = computed(() => data.value?.breeden_litzenberger)
const spot = computed(() => data.value?.spot)

function px(n) {
  if (n == null || Number.isNaN(Number(n))) return '—'
  return `$${Number(n).toFixed(2)}`
}
function pct2(n) {
  if (n == null || Number.isNaN(Number(n))) return '—'
  return `${(Number(n) * 100).toFixed(2)}%`
}
function pctSigned(n) {
  if (n == null || Number.isNaN(Number(n))) return '—'
  const v = Number(n) * 100
  return `${v > 0 ? '+' : ''}${v.toFixed(2)}%`
}
function distPct(strike) {
  if (!spot.value) return '—'
  const v = ((strike - spot.value) / spot.value) * 100
  return `${v > 0 ? '+' : ''}${v.toFixed(1)}%`
}
function fmtInt(n) {
  if (n == null) return '—'
  return Number(n).toLocaleString('en-US', { maximumFractionDigits: 0 })
}

const smileOption = computed(() => {
  const d = data.value?.volatility_smile
  if (!d || (!d.calls?.strikes?.length && !d.puts?.strikes?.length)) return null
  const callPts = (d.calls?.strikes || []).map((k, i) => [k, d.calls.ivs[i] * 100])
  const putPts = (d.puts?.strikes || []).map((k, i) => [k, d.puts.ivs[i] * 100])
  return {
    backgroundColor: 'transparent',
    tooltip: { ...TOOLTIP_BASE, valueFormatter: (v) => `${Number(v).toFixed(1)}%` },
    legend: { top: 0, textStyle: { color: '#8b9bb0' } },
    grid: { left: 52, right: 20, top: 36, bottom: 32 },
    xAxis: {
      type: 'value', name: 'Strike', nameTextStyle: { color: '#8b9bb0' },
      axisLabel: AXIS_LABEL, axisLine: AXIS_LINE, splitLine: { show: false },
    },
    yAxis: {
      type: 'value', name: 'IV %', nameTextStyle: { color: '#8b9bb0' },
      axisLabel: AXIS_LABEL, splitLine: SPLIT_LINE,
    },
    series: [
      {
        name: 'Call IV', type: 'line', data: callPts, showSymbol: true, symbolSize: 5,
        lineStyle: { width: 2, color: '#5b8def' }, itemStyle: { color: '#5b8def' },
      },
      {
        name: 'Put IV', type: 'line', data: putPts, showSymbol: true, symbolSize: 5,
        lineStyle: { width: 2, color: '#e85d75' }, itemStyle: { color: '#e85d75' },
        markLine: {
          symbol: 'none', silent: true,
          lineStyle: { color: '#8b9bb0', type: 'dashed' },
          label: { color: '#8b9bb0', formatter: () => `Spot ${spot.value}` },
          data: [{ xAxis: spot.value }],
        },
      },
    ],
  }
})

const maxPainOption = computed(() => {
  const mp = data.value?.max_pain
  if (!mp || !mp.curve?.length) return null
  const pts = mp.curve.map((r) => [r.strike, r.loss])
  return {
    backgroundColor: 'transparent',
    tooltip: { ...TOOLTIP_BASE },
    grid: { left: 64, right: 20, top: 20, bottom: 32 },
    xAxis: {
      type: 'value', name: 'Strike', nameTextStyle: { color: '#8b9bb0' },
      axisLabel: AXIS_LABEL, axisLine: AXIS_LINE, splitLine: { show: false },
    },
    yAxis: {
      type: 'value', name: 'Dealer Loss', nameTextStyle: { color: '#8b9bb0' },
      axisLabel: AXIS_LABEL, splitLine: SPLIT_LINE,
    },
    series: [{
      type: 'line', data: pts, showSymbol: false, lineStyle: { width: 2, color: '#457B9D' },
      areaStyle: { color: '#457B9D', opacity: 0.12 },
      markLine: {
        symbol: 'none', silent: true, label: { color: '#8b9bb0' },
        data: [
          { xAxis: mp.strike, lineStyle: { color: '#e85d75', type: 'dashed' }, label: { formatter: () => `Max Pain ${mp.strike}` } },
          { xAxis: spot.value, lineStyle: { color: '#8b9bb0', type: 'dashed' }, label: { formatter: () => `Spot ${spot.value}` } },
        ],
      },
    }],
  }
})

const gammaOption = computed(() => {
  const rows = data.value?.gamma_by_strike
  if (!rows || !rows.length) return null
  return {
    backgroundColor: 'transparent',
    tooltip: { ...TOOLTIP_BASE },
    grid: { left: 64, right: 20, top: 20, bottom: 48 },
    xAxis: {
      type: 'category', data: rows.map((r) => r.strike),
      axisLabel: { ...AXIS_LABEL, rotate: 60 }, axisLine: AXIS_LINE,
    },
    yAxis: {
      type: 'value', name: 'Gamma Exposure', nameTextStyle: { color: '#8b9bb0' },
      axisLabel: AXIS_LABEL, splitLine: SPLIT_LINE,
    },
    series: [{
      type: 'bar',
      data: rows.map((r) => ({ value: r.gamma_exposure, itemStyle: { color: r.gamma_exposure >= 0 ? '#3ecf8e' : '#e85d75' } })),
    }],
  }
})

const gammaFlipOption = computed(() => {
  const gf = data.value?.gamma_flip
  if (!gf || !gf.curve?.length) return null
  const pts = gf.curve.map((r) => [r.price, r.gex])
  const flipLines = (gf.flip_points || []).map((p) => ({
    xAxis: p, lineStyle: { color: '#f0b429', type: 'dashed' }, label: { formatter: () => `Flip ${p.toFixed(1)}` },
  }))
  return {
    backgroundColor: 'transparent',
    tooltip: { ...TOOLTIP_BASE },
    grid: { left: 64, right: 20, top: 20, bottom: 32 },
    xAxis: {
      type: 'value', name: 'Hypothetical Price', nameTextStyle: { color: '#8b9bb0' },
      axisLabel: AXIS_LABEL, axisLine: AXIS_LINE, splitLine: { show: false },
    },
    yAxis: {
      type: 'value', name: 'Net GEX', nameTextStyle: { color: '#8b9bb0' },
      axisLabel: AXIS_LABEL, splitLine: SPLIT_LINE,
    },
    series: [{
      type: 'line', data: pts, showSymbol: false, lineStyle: { width: 2, color: '#5b8def' },
      areaStyle: { color: '#5b8def', opacity: 0.1 },
      markLine: {
        symbol: 'none', silent: true, label: { color: '#8b9bb0' },
        data: [
          { xAxis: spot.value, lineStyle: { color: '#8b9bb0', type: 'dashed' }, label: { formatter: () => `Spot ${spot.value}` } },
          ...flipLines,
        ],
      },
    }],
  }
})

const blOption = computed(() => {
  const b = data.value?.breeden_litzenberger
  if (!b || !b.grid?.length) return null
  const pts = b.grid.map((k, i) => [k, b.pdf[i]])
  return {
    backgroundColor: 'transparent',
    tooltip: { ...TOOLTIP_BASE },
    grid: { left: 64, right: 20, top: 20, bottom: 32 },
    xAxis: {
      type: 'value', name: 'Price at Expiration', nameTextStyle: { color: '#8b9bb0' },
      axisLabel: AXIS_LABEL, axisLine: AXIS_LINE, splitLine: { show: false },
    },
    yAxis: {
      type: 'value', name: 'Probability Density', nameTextStyle: { color: '#8b9bb0' },
      axisLabel: AXIS_LABEL, splitLine: SPLIT_LINE,
    },
    series: [{
      type: 'line', data: pts, showSymbol: false, smooth: true, lineStyle: { width: 2, color: '#1D3557' },
      areaStyle: { color: '#457B9D', opacity: 0.25 },
      markLine: {
        symbol: 'none', silent: true, label: { color: '#8b9bb0' },
        data: [{ xAxis: spot.value, lineStyle: { color: '#e85d75', type: 'dashed' }, label: { formatter: () => `Spot ${spot.value}` } }],
      },
    }],
  }
})
</script>

<template>
  <p class="goal-lead">输入美股代码，看期权链隐含的预期区间、Max Pain、价内OI分布、Gamma敞口/翻转点、以及 Breeden-Litzenberger 反推的隐含概率分布。数据源 yahooquery，仅供研究参考，不构成投资建议。</p>

  <div class="range-row">
    <div class="watch-edit" style="flex: 1; margin: 0; flex-wrap: wrap">
      <input v-model="symbol" placeholder="AVGO / NVDA / TSLA" style="min-width: 140px" @keydown.enter.prevent="run" />
      <select v-if="data?.expirations?.length" v-model="expiration" @change="onExpirationChange">
        <option v-for="e in data.expirations" :key="e" :value="e">{{ e }}</option>
      </select>
      <input v-model="riskFreeRatePct" type="number" step="0.1" style="width: 90px" title="无风险利率 %" />
      <span class="sub" style="align-self: center">无风险利率 %</span>
      <button class="btn btn-primary" type="button" :disabled="loading" @click="run">
        {{ loading ? '分析中…' : '分析' }}
      </button>
    </div>
  </div>
  <div class="error" v-if="error">{{ error }}</div>
  <p class="sub" v-else-if="loading">正在拉期权链…</p>
  <p class="sub" v-else-if="!data">输入代码后点分析。</p>

  <template v-if="data">
    <section class="panel" style="margin-top: 12px" v-if="data.warnings?.length">
      <div v-for="(w, i) in data.warnings" :key="i" class="warn-msg">⚠️ {{ w }}</div>
    </section>

    <section class="panel" style="margin-top: 12px">
      <h2>{{ data.symbol }} 期权分析</h2>
      <p class="sub">数据源 {{ data.source }} · 到期日 {{ data.expiration }}（{{ data.days_to_exp }} 天）</p>
      <div class="price-grid">
        <div class="price-card">
          <span>现价</span>
          <b>{{ px(data.spot) }}</b>
        </div>
        <div class="price-card" v-if="iv">
          <span>平值 Call IV</span>
          <b>{{ pct2(iv.call_iv) }}</b>
        </div>
        <div class="price-card" v-if="iv?.put_iv != null">
          <span>平值 Put IV</span>
          <b>{{ pct2(iv.put_iv) }}</b>
        </div>
        <div class="price-card" v-if="iv?.skew_pct != null">
          <span>偏斜</span>
          <b>{{ pctSigned(iv.skew_pct) }}</b>
        </div>
      </div>
      <p class="sub" v-if="iv?.skew_note" style="margin-top: 8px">{{ iv.skew_note }}</p>
      <p class="sub">
        偏斜 = 正数 → 市场情绪偏空一点点(买跌保险的人愿意多付一点钱)
    
      </p>
      <p class="sub">
            偏斜 = 负数 → 市场情绪偏多一点点
       
      </p>
      <p class="sub">
 偏斜 = 0附近 → 说不清是涨是跌,市场没有明显倾向
      </p>
    </section>

    <section class="panel" style="margin-top: 12px" v-if="range">
      <h2>隐含预期区间</h2>
      <p class="sub">{{ range.confidence }} 概率覆盖，{{ range.down_move != null ? '上下不对称（用 Call/Put IV 分别算）' : '对称近似' }}</p>
      <div class="price-grid">
        <div class="price-card">
          <span>下方</span>
          <b>{{ px(range.expected_low) }}</b>
        </div>
        <div class="price-card">
          <span>上方</span>
          <b>{{ px(range.expected_high) }}</b>
        </div>
        <div class="price-card" v-if="range.down_move != null">
          <span>下方空间</span>
          <b>-{{ px(range.down_move) }}</b>
        </div>
        <div class="price-card" v-if="range.up_move != null">
          <span>上方空间</span>
          <b>+{{ px(range.up_move) }}</b>
        </div>
      </div>
    </section>

    <section class="panel" style="margin-top: 12px" v-if="maxPain">
      <h2>Max Pain 最大痛点</h2>
      <p class="sub">现价 ±25% 范围内计算 · 做市商总浮亏最小的价格点：<b>{{ px(maxPain.strike) }}</b></p>
      <MacroChart v-if="maxPainOption" :option="maxPainOption" />
    </section>

    <section class="panel" style="margin-top: 12px" v-if="diag">
      <h2>诊断 / 价内OI分布</h2>
      <p class="sub">
        全链 Call OI 总和 {{ fmtInt(diag.total_call_oi) }} · Put OI 总和 {{ fmtInt(diag.total_put_oi) }}
        <span v-if="diag.thin" class="warn"> · OI 数据太薄，以下结果不可信</span>
      </p>
      <template v-if="itmBreakdown">
        <div class="price-grid">
          <div class="price-card">
            <span>价内 Call OI（多方赢面）</span>
            <b>{{ fmtInt(itmBreakdown.itm_call_oi) }}</b>
          </div>
          <div class="price-card">
            <span>价内 Put OI（空方赢面）</span>
            <b>{{ fmtInt(itmBreakdown.itm_put_oi) }}</b>
          </div>
          <div class="price-card" v-if="itmBreakdown.call_win_share != null">
            <span>价内 OI 中 Call 占比</span>
            <b>{{ pct2(itmBreakdown.call_win_share) }}</b>
          </div>
        </div>
        <div class="table-wrap" style="margin-top: 10px" v-if="itmExtremes">
          <table>
            <thead>
              <tr>
                <th>价内 Call 行权价</th><th>OI</th>
                <th>价内 Put 行权价</th><th>OI</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="i in Math.max(itmExtremes.calls.length, itmExtremes.puts.length)" :key="i">
                <td>{{ itmExtremes.calls[i-1] ? itmExtremes.calls[i-1].strike : '—' }}</td>
                <td>{{ itmExtremes.calls[i-1] ? fmtInt(itmExtremes.calls[i-1].oi) : '—' }}</td>
                <td>{{ itmExtremes.puts[i-1] ? itmExtremes.puts[i-1].strike : '—' }}</td>
                <td>{{ itmExtremes.puts[i-1] ? fmtInt(itmExtremes.puts[i-1].oi) : '—' }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </template>
      <div class="table-wrap" style="margin-top: 10px" v-if="diag.top_outliers?.length">
        <p class="sub">浮亏权重最高的行权价（排查异常大单用）</p>
        <table>
          <thead>
            <tr><th>行权价</th><th>距现价</th><th>Call OI</th><th>Put OI</th></tr>
          </thead>
          <tbody>
            <tr v-for="r in diag.top_outliers" :key="r.strike">
              <td>{{ r.strike }}</td>
              <td>{{ distPct(r.strike) }}</td>
              <td>{{ fmtInt(r.call_oi) }}</td>
              <td>{{ fmtInt(r.put_oi) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <section class="panel" style="margin-top: 12px" v-if="smileOption">
      <h2>波动率微笑</h2>
      <p class="sub">看市场情绪偏多还是偏空，call > put: 偏多，call < put : 偏少；只在贴近现价、流动性好、曲线平滑的区间里比较才有效；离现价远的行权价交易清淡，数据容易剧烈跳动失真</p>  
      <p class="sub">对整条期权链逐个行权价反推 IV，看不同行权价隐含波动率的形状</p>
      <MacroChart :option="smileOption" />
    </section>

    <section class="panel" style="margin-top: 12px" v-if="gammaOption">
      <h2>Gamma 分布</h2>
      <p class="sub">绿色代表：踩刹车，市商会控制涨跌，红色代表：油门加速，市商会开始加速砸盘或者买盘，</p>
      <p class="sub">每个行权价的总 Gamma 敞口（绿正/红负），做市商对冲行为跟这个方向有关</p>
      <MacroChart :option="gammaOption" />
    </section>

    <section class="panel" style="margin-top: 12px" v-if="gammaFlip">
      <h2>Net GEX / Gamma Flip</h2>
      <p class="sub">
        现价处净GEX {{ fmtInt(gammaFlip.gex_at_spot) }} · 当前机制：{{ gammaFlip.regime }}
      </p>
      <p class="sub" v-if="gammaFlip.flip_points?.length">
        Gamma Flip 点：{{ gammaFlip.flip_points.map((p) => p.toFixed(2)).join(', ') }} —— 价格穿过这些点，做市商对冲行为会在「压制波动」和「放大波动」之间切换
      </p>
      <p class="sub" v-else>在搜索范围内没有找到翻转点，说明当前机制在这个价格区间比较稳定</p>
      <MacroChart v-if="gammaFlipOption" :option="gammaFlipOption" />
    </section>

    <section class="panel" style="margin-top: 12px" v-if="bl">
      <h2>Breeden-Litzenberger 隐含概率分布</h2>
      <p class="sub">从 Call 期权价格反推的到期价格风险中性概率密度（不是真实世界概率，仅供参考）</p>
      <div class="price-grid">
        <div class="price-card">
          <span>概率落在现价以下</span>
          <b>{{ pct2(bl.prob_below_spot) }}</b>
        </div>
        <div class="price-card">
          <span>概率落在现价以上</span>
          <b>{{ pct2(bl.prob_above_spot) }}</b>
        </div>
      </div>
      <MacroChart v-if="blOption" :option="blOption" />
    </section>
  </template>
</template>
