<script setup>
import { onMounted, ref } from 'vue'
import { api } from '../api'

const symbol = ref('QQQ')
const indicator = ref('cpi')
const bucket = ref('hot')
const lookbackYears = ref(5)
const options = ref({ indicators: [], buckets: [], presets: [], lookbacks: [] })
const loading = ref(false)
const error = ref('')
const data = ref(null)

async function loadOptions() {
  try {
    options.value = await api.macroEventStudyOptions()
    if (!options.value.indicators?.length) return
    if (!options.value.indicators.find((x) => x.id === indicator.value)) {
      indicator.value = options.value.indicators[0].id
    }
    if (options.value.default_lookback) lookbackYears.value = options.value.default_lookback
  } catch (e) {
    error.value = e.message
  }
}

async function run() {
  error.value = ''
  const sym = String(symbol.value || '').trim().toUpperCase()
  if (!sym) {
    error.value = '请填写代码'
    return
  }
  loading.value = true
  data.value = null
  try {
    data.value = await api.macroEventStudy({
      symbol: sym,
      indicator: indicator.value,
      bucket: bucket.value,
      lookback_years: Number(lookbackYears.value) || 5,
      compare_all: true,
    })
  } catch (e) {
    error.value = e.message || '统计失败'
  } finally {
    loading.value = false
  }
}

function pct(n, digits = 2) {
  if (n == null || Number.isNaN(Number(n))) return '—'
  return `${(Number(n) * 100).toFixed(digits)}%`
}

function pp(n, digits = 2) {
  if (n == null || Number.isNaN(Number(n))) return '—'
  const x = Number(n) * 100
  return `${x >= 0 ? '+' : ''}${x.toFixed(digits)}pp`
}

function toneRet(n) {
  if (n == null) return ''
  if (n > 0.001) return 'up'
  if (n < -0.001) return 'down'
  return ''
}

function pickPreset(s) {
  symbol.value = s
  run()
}

onMounted(async () => {
  await loadOptions()
  run()
})
</script>

<template>
  <div>
    <p class="goal-lead">
      选股票/指数 + 宏观指标 + 回看年数 + 事件桶。表头「1/5/20 日」= 事件后持有的<strong>交易日</strong>，不是年份。
    </p>

    <section class="panel" style="margin-top: 12px">
      <div class="range-row" style="gap: 8px; flex-wrap: wrap; margin-bottom: 8px">
        <input
          v-model="symbol"
          class="strategy-select"
          style="width: 110px"
          placeholder="代码"
          @keyup.enter="run"
        />
        <select v-model="indicator" class="strategy-select">
          <option v-for="opt in options.indicators" :key="opt.id" :value="opt.id">
            {{ opt.label }}
          </option>
        </select>
        <select v-model.number="lookbackYears" class="strategy-select">
          <option v-for="opt in options.lookbacks || []" :key="opt.id" :value="opt.id">
            {{ opt.label }}
          </option>
          <option v-if="!(options.lookbacks || []).length" :value="5">近 5 年</option>
          <option v-if="!(options.lookbacks || []).length" :value="10">近 10 年</option>
          <option v-if="!(options.lookbacks || []).length" :value="20">近 20 年</option>
        </select>
        <select v-model="bucket" class="strategy-select">
          <option v-for="opt in options.buckets" :key="opt.id" :value="opt.id">
            {{ opt.label }}
          </option>
        </select>
        <button class="btn btn-primary" type="button" :disabled="loading" @click="run">
          {{ loading ? '统计中…' : '跑统计' }}
        </button>
      </div>
      <div class="range-row" style="gap: 6px; flex-wrap: wrap">
        <button
          v-for="p in options.presets || []"
          :key="p"
          class="pill"
          type="button"
          :class="{ active: String(symbol).toUpperCase() === p }"
          @click="pickPreset(p)"
        >
          {{ p }}
        </button>
      </div>
      <div class="error" v-if="error" style="margin-top: 8px">{{ error }}</div>
    </section>

    <template v-if="data">
      <section class="panel" style="margin-top: 12px">
        <div class="range-row" style="gap: 24px; flex-wrap: wrap; margin-bottom: 8px">
          <div>
            <div class="sub">标的</div>
            <b>{{ data.symbol }}</b>
            <div class="sub">样本 {{ data.sample_from || '—' }} → {{ data.sample_to || '—' }}</div>
          </div>
          <div>
            <div class="sub">回看</div>
            <b>近 {{ data.lookback_years }} 年</b>
            <div class="sub">截止 {{ data.cutoff }}</div>
          </div>
          <div>
            <div class="sub">宏观</div>
            <b>{{ data.indicator_label }}</b>
            <div class="sub">{{ data.series_name }}</div>
          </div>
          <div>
            <div class="sub">事件</div>
            <b>{{ data.bucket_label }}</b>
            <div class="sub">n = {{ data.summary?.n }}</div>
          </div>
        </div>
        <p v-if="data.verdict" class="sub">{{ data.verdict }}</p>

        <div class="range-row" style="gap: 20px; flex-wrap: wrap; margin-top: 12px">
          <div v-for="h in [1, 5, 20]" :key="'r' + h">
            <div class="sub">{{ h }} 交易日均收益</div>
            <b class="mono" style="font-size: 20px" :class="toneRet(data.summary?.['ret_' + h + '_mean'])">
              {{ pct(data.summary?.['ret_' + h + '_mean']) }}
            </b>
            <div class="sub">
              中位 {{ pct(data.summary?.['ret_' + h + '_med']) }}
              · 胜率 {{ pct(data.summary?.['ret_' + h + '_pos'], 1) }}
            </div>
            <div class="sub" v-if="data.excess_vs_all">
              超额 {{ pp(data.excess_vs_all['ret_' + h]) }}
            </div>
          </div>
          <div>
            <div class="sub">20 交易日波动（年化）</div>
            <b class="mono" style="font-size: 20px">{{ pct(data.summary?.vol_20_mean, 1) }}</b>
            <div class="sub">事前 {{ pct(data.summary?.vol20_pre_mean, 1) }}</div>
            <div class="sub">后/前 Δ {{ pct(data.summary?.vol20_rel_change, 1) }}</div>
          </div>
        </div>
      </section>

      <section class="panel" style="margin-top: 12px" v-if="data.baseline">
        <h2 style="margin: 0 0 8px; font-size: 15px">对照：同回看内全部发布</h2>
        <div class="range-row" style="gap: 20px; flex-wrap: wrap">
          <div v-for="h in [1, 5, 20]" :key="'b' + h">
            <div class="sub">{{ h }} 交易日</div>
            <b class="mono" :class="toneRet(data.baseline['ret_' + h + '_mean'])">
              {{ pct(data.baseline['ret_' + h + '_mean']) }}
            </b>
            <div class="sub">n={{ data.baseline.n }}</div>
          </div>
          <div>
            <div class="sub">20 交易日 vol</div>
            <b class="mono">{{ pct(data.baseline.vol_20_mean, 1) }}</b>
          </div>
        </div>
      </section>

      <section class="panel" style="margin-top: 12px">
        <h2 style="margin: 0 0 8px; font-size: 15px">事件明细（近→远）</h2>
        <p class="sub" style="margin-bottom: 8px">
          财报：「是」= 宏观交易日 ±3 天内公布；「持仓内」= 之后约 28 天内有财报；ETF/指数通常为 —
          <template v-if="data.earnings_note">（{{ data.earnings_note }}）</template>
        </p>
        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>参考期</th>
                <th>交易日</th>
                <th>财报</th>
                <th>Z</th>
                <th>Actual</th>
                <th>Expected</th>
                <th>1交易日</th>
                <th>5交易日</th>
                <th>20交易日</th>
                <th>vol5</th>
                <th>vol20</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(e, i) in data.events || []" :key="i">
                <td>{{ e.period }}</td>
                <td class="sub">{{ e.trade_date }}</td>
                <td :class="e.earnings ? 'warn' : e.earnings_in_hold ? 'sub' : 'sub'">
                  {{ e.earnings_label || '—' }}
                </td>
                <td class="mono">{{ e.z == null ? '—' : Number(e.z).toFixed(2) }}</td>
                <td class="mono">{{ e.actual == null ? '—' : Number(e.actual).toFixed(2) }}</td>
                <td class="mono">{{ e.expected == null ? '—' : Number(e.expected).toFixed(2) }}</td>
                <td class="mono" :class="toneRet(e.ret_1)">{{ pct(e.ret_1) }}</td>
                <td class="mono" :class="toneRet(e.ret_5)">{{ pct(e.ret_5) }}</td>
                <td class="mono" :class="toneRet(e.ret_20)">{{ pct(e.ret_20) }}</td>
                <td class="mono">{{ pct(e.vol_5, 1) }}</td>
                <td class="mono">{{ pct(e.vol_20, 1) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <p class="sub" style="margin-top: 8px">{{ data.method }}</p>
      </section>
    </template>
  </div>
</template>
