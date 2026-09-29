<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from '../api'
import { money, tone } from '../format'

const props = defineProps({
  strategyId: { type: [Number, String], default: null },
})
const emit = defineEmits(['back'])

const loading = ref(false)
const error = ref('')
const result = ref(null)
const row = ref(null)
const specs = ref([])
const defaults = ref({})
const useRange = ref(false)

const form = reactive({
  symbol: 'BTCUSDT',
  range_start: '',
  range_end: '',
  params: {},
})

async function loadMeta() {
  const [cat, list] = await Promise.all([api.cryptoCustomCatalog(), api.cryptoCustomStrategies()])
  specs.value = cat.param_specs || []
  defaults.value = cat.defaults || {}
  form.params = { ...defaults.value }
  if (props.strategyId) {
    const hit = (list.items || []).find((x) => String(x.id) === String(props.strategyId))
    if (hit) {
      row.value = hit
      form.symbol = (hit.watch_symbols && hit.watch_symbols[0]) || hit.binance_symbol || `${hit.symbol}USDT`
      form.params = { ...defaults.value, ...(hit.params || {}) }
      delete form.params.watch_symbols
      if (!form.params.timeframe) form.params.timeframe = hit.timeframe || '5m'
    }
  }
}

async function run() {
  error.value = ''
  loading.value = true
  try {
    result.value = await api.cryptoCustomBacktest({
      id: props.strategyId ? Number(props.strategyId) : null,
      symbol: form.symbol.trim().toUpperCase(),
      params: { ...form.params },
      range_start: useRange.value && form.range_start ? form.range_start : null,
      range_end: useRange.value && form.range_end ? form.range_end : null,
    })
  } catch (e) {
    error.value = e.message || '回测失败'
    result.value = null
  } finally {
    loading.value = false
  }
}

const summary = computed(() => result.value?.summary || null)
const realizedTrades = computed(() => (result.value?.trades || []).filter((t) => t.exit_day))
const fieldSpecs = computed(() =>
  (specs.value || []).filter((s) => (!s.strategies || s.strategies.includes(row.value?.strategy_key || 'deriv')) && s.type !== 'bool' && s.key !== 'watch_symbols' && s.key !== 'timeframe'),
)
const OPTION_LABEL = { breakout: '突破（导数+放量）', pullback: '回踩趋势线' }
const hasRegime = computed(() => realizedTrades.value.some((t) => t.entry_substate))
const boolSpecs = computed(() => (specs.value || []).filter((s) => (!s.strategies || s.strategies.includes(row.value?.strategy_key || 'deriv')) && s.type === 'bool'))

watch(
  () => props.strategyId,
  () => {
    loadMeta().catch((e) => ElMessage.error(e.message || '加载失败'))
  },
)

onMounted(() => {
  loadMeta()
    .then(() => {
      /* ready */
    })
    .catch((e) => {
      error.value = e.message || '加载失败'
    })
})
</script>

<template>
  <p class="goal-lead">
    {{ row?.name || '自定义策略' }}回测（虚拟币 · {{ form.params.timeframe || '5m' }} · UTC 日）。
    <template v-if="row">当前实例：{{ row.name }} · {{ row.symbol }}</template>
  </p>

  <div class="range-row" style="margin-bottom: 10px; gap: 8px; flex-wrap: wrap">
    <el-button @click="emit('back')">← 返回列表</el-button>
    <el-select
      v-if="row?.watch_symbols?.length"
      v-model="form.symbol"
      filterable
      allow-create
      style="width: 160px"
    >
      <el-option v-for="s in row.watch_symbols" :key="s" :label="s" :value="s" />
    </el-select>
    <el-input v-else v-model="form.symbol" placeholder="BTCUSDT" style="width: 140px" @keydown.enter.prevent="run" />
    <el-select v-model="form.params.timeframe" style="width: 120px">
      <el-option value="5m" label="5 分钟" />
      <el-option value="30m" label="30 分钟" />
      <el-option value="1h" label="1 小时" />
      <el-option value="1d" label="日线" />
    </el-select>
    <el-button type="primary" :loading="loading" @click="run">{{ loading ? '回测中…' : '回测' }}</el-button>
  </div>

  <div class="range-row" style="margin-bottom: 10px; gap: 8px; flex-wrap: wrap">
    <el-checkbox v-model="useRange">指定日期区间</el-checkbox>
    <template v-if="useRange">
      <el-date-picker v-model="form.range_start" type="date" value-format="YYYY-MM-DD" placeholder="开始" />
      <span class="sub">~</span>
      <el-date-picker v-model="form.range_end" type="date" value-format="YYYY-MM-DD" placeholder="结束" />
    </template>
    <template v-else>
      <span class="sub">最近</span>
      <el-input-number v-model="form.params.backtest_trading_days" :min="5" :max="120" controls-position="right" />
      <span class="sub">个 UTC 日</span>
    </template>
  </div>

  <section class="panel" style="margin-bottom: 12px">
    <h2 style="margin: 0 0 10px; font-size: 15px">参数（仅本次回测，不改实例）</h2>
    <div style="display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap: 8px 12px">
      <div v-for="s in fieldSpecs" :key="s.key">
        <div class="sub" style="margin-bottom: 4px">{{ s.label }}</div>
        <el-input-number
          v-if="s.type === 'int' || s.type === 'float'"
          v-model="form.params[s.key]"
          :min="s.min"
          :max="s.max"
          :step="s.type === 'float' ? 0.1 : 1"
          controls-position="right"
          style="width: 100%"
        />
        <el-select v-else-if="s.type === 'enum'" v-model="form.params[s.key]" style="width: 100%">
          <el-option v-for="o in s.options" :key="o" :label="OPTION_LABEL[o] || o" :value="o" />
        </el-select>
        <el-input v-else v-model="form.params[s.key]" />
      </div>
    </div>
    <div style="margin-top: 10px">
      <el-checkbox v-for="s in boolSpecs" :key="s.key" v-model="form.params[s.key]" style="margin-right: 12px">
        {{ s.label }}
      </el-checkbox>
    </div>
  </section>

  <p v-if="error" class="error">{{ error }}</p>
  <p v-if="row?.strategy_key === 'deriv_regime'" class="sub">三态趋势线：急涨/急跌（直线拉砸）顺势追、横盘通道高抛低吸、普通趋势沿回归趋势线开仓。三个开关可单独关掉对照；关掉「启用三态趋势线」就是原导数策略的结果。</p>
  <p v-if="row?.strategy_key === 'deriv_advanced'" class="sub">高级策略 V2 · 研究版，尚未验证盈利。24小时连续运行，周末及跨日不强平。回踩开关、ADX和追价过滤可独立对照；保留已保存参数，新增参数使用默认值。</p>

  <section class="panel" v-if="summary">
    <h2 style="margin: 0 0 10px; font-size: 15px">结果</h2>
    <div class="range-row" style="gap: 24px; flex-wrap: wrap">
      <div><div class="sub">交易笔数</div><b>{{ summary.n_trades ?? realizedTrades.length }}</b></div>
      <div>
        <div class="sub">胜率</div>
        <b :class="tone((summary.win_rate || 0) - 50)">{{
          summary.win_rate != null ? `${Number(summary.win_rate).toFixed(1)}%` : '—'
        }}</b>
      </div>
      <div>
        <div class="sub">盈亏比率</div>
        <b>{{
          summary.payoff_ratio != null
            ? `1 : ${Number(summary.payoff_ratio).toFixed(2)}`
            : summary.avg_win > 0 && !summary.avg_loss
              ? '全胜'
              : '—'
        }}</b>
      </div>
      <div>
        <div class="sub">已实现盈亏</div>
        <b :class="tone(summary.total_pnl)">{{ money(summary.total_pnl) }}</b>
      </div>
      <div>
        <div class="sub">最终权益</div>
        <b>{{ money(summary.final_equity) }}</b>
      </div>
      <div v-if="summary.unrealized_pnl != null"><div class="sub">未平仓浮盈亏（含入场费）</div><b :class="tone(summary.unrealized_pnl)">{{ money(summary.unrealized_pnl) }}</b></div>
      <div><div class="sub">回测天数</div><b>{{ result?.n_days ?? '—' }}</b></div>
    </div>
    <p class="sub" v-if="summary.by_side">多头：{{ summary.by_side['多'].n_trades }}笔 / 胜率{{ summary.by_side['多'].win_rate }}% / {{ money(summary.by_side['多'].total_pnl) }}；空头：{{ summary.by_side['空'].n_trades }}笔 / 胜率{{ summary.by_side['空'].win_rate }}% / {{ money(summary.by_side['空'].total_pnl) }}。</p>
    <p class="sub" v-if="summary.exit_counts">离场原因：<span v-for="(count, reason) in summary.exit_counts" :key="reason">{{ reason }} {{ count }}笔　</span></p>
    <div v-if="summary.by_regime" style="margin-top: 8px">
      <div class="sub" style="margin-bottom: 4px">按入场状态</div>
      <div style="display: flex; flex-wrap: wrap; gap: 8px">
        <span v-for="(v, k) in summary.by_regime" :key="k" class="pill" style="cursor: default">
          {{ k }} · {{ v.n_trades }}笔 · 胜率{{ v.win_rate }}% · <span :class="tone(v.total_pnl)">{{ money(v.total_pnl) }}</span>
        </span>
      </div>
    </div>
    <p class="sub" v-if="result?.warnings?.length" style="margin-top: 8px">
      {{ result.warnings.join('；') }}
    </p>
  </section>

  <section class="panel" style="margin-top: 12px" v-if="realizedTrades.length">
    <h2 style="margin: 0 0 10px; font-size: 15px">已平仓交易</h2>
    <el-table :data="realizedTrades" stripe size="small" max-height="420">
      <el-table-column prop="side" label="方向" width="56" />
      <el-table-column v-if="hasRegime" prop="entry_substate" label="入场状态" width="96" />
      <el-table-column prop="entry_day" label="开仓日" width="110" />
      <el-table-column prop="entry_time" label="开仓UTC" width="85" />
      <el-table-column prop="exit_day" label="平仓日" width="110" />
      <el-table-column prop="exit_time" label="平仓UTC" width="85" />
      <el-table-column prop="entry_price" label="开仓价" width="100" />
      <el-table-column prop="exit_price" label="平仓价" width="100" />
      <el-table-column label="盈亏" width="100">
        <template #default="{ row: t }">
          <span :class="tone(t.pnl)">{{ money(t.pnl) }}</span>
        </template>
      </el-table-column>
      <el-table-column prop="exit_reason" label="原因" min-width="100" />
    </el-table>
  </section>
</template>
