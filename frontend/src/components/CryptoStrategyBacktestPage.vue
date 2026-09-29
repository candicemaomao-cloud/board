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
const form = reactive({ capital: 10000 })

async function loadMeta() {
  const list = await api.cryptoStrategies()
  const hit = (list.items || []).find((x) => String(x.id) === String(props.strategyId))
  row.value = hit || null
  if (!hit) error.value = '找不到策略'
}

async function run() {
  if (!props.strategyId) {
    error.value = '没有策略 id'
    return
  }
  error.value = ''
  loading.value = true
  try {
    result.value = await api.cryptoStrategyBacktest(Number(props.strategyId), {
      capital: Number(form.capital) || 10000,
    })
  } catch (e) {
    error.value = e.message || '回测失败'
    result.value = null
  } finally {
    loading.value = false
  }
}

const summary = computed(() => result.value?.summary || null)
const realized = computed(() => (result.value?.trades || []).filter((t) => t.exit_day))
const openTrade = computed(() => (result.value?.trades || []).find((t) => !t.exit_day) || null)

watch(
  () => props.strategyId,
  () => {
    result.value = null
    loadMeta().catch((e) => {
      error.value = e.message || '加载失败'
    })
  },
)

onMounted(() => {
  loadMeta()
    .then(() => {
      if (row.value) run()
    })
    .catch((e) => {
      error.value = e.message || '加载失败'
    })
})
</script>

<template>
  <p class="goal-lead">
    指标策略回测：组合从「未命中 → 命中」开多，从「命中 → 未命中」平多；用币安历史 K 线逐根复算指标。
    <template v-if="row">当前：{{ row.name }} · {{ row.symbol }} · {{ row.timeframe }}</template>
  </p>

  <div class="range-row" style="margin-bottom: 10px; gap: 8px; flex-wrap: wrap">
    <el-button @click="emit('back')">← 返回列表</el-button>
    <span class="sub">本金</span>
    <el-input-number v-model="form.capital" :min="100" :step="1000" controls-position="right" />
    <el-button type="primary" :loading="loading" @click="run">{{ loading ? '回测中…' : '重新回测' }}</el-button>
  </div>

  <p v-if="error" class="error">{{ error }}</p>
  <p class="sub" v-if="result?.warnings?.length">{{ result.warnings.join('；') }}</p>

  <section class="panel" v-if="summary">
    <h2 style="margin: 0 0 10px; font-size: 15px">结果</h2>
    <div class="range-row" style="gap: 24px; flex-wrap: wrap">
      <div><div class="sub">区间</div><b>{{ result.range?.start }} ~ {{ result.range?.end }}</b></div>
      <div><div class="sub">扫描 K 线</div><b>{{ result.bars_scanned }}</b></div>
      <div>
        <div class="sub">命中占比</div>
        <b>{{ result.hit_rate != null ? `${(result.hit_rate * 100).toFixed(1)}%` : '—' }}</b>
      </div>
      <div><div class="sub">已平仓</div><b>{{ summary.n_trades }}</b></div>
      <div>
        <div class="sub">胜率</div>
        <b :class="tone((summary.win_rate || 0) - 0.5)">{{
          summary.win_rate != null ? `${(summary.win_rate * 100).toFixed(1)}%` : '—'
        }}</b>
      </div>
      <div>
        <div class="sub">盈亏比率</div>
        <b>{{
          summary.payoff_ratio != null
            ? `1 : ${Number(summary.payoff_ratio).toFixed(2)}`
            : summary.avg_win > 0 && !(summary.avg_loss < 0)
              ? '全胜'
              : '—'
        }}</b>
      </div>
      <div>
        <div class="sub">总盈亏</div>
        <b :class="tone(summary.total_pnl)">{{ money(summary.total_pnl) }}</b>
      </div>
      <div><div class="sub">最终权益</div><b>{{ money(summary.final_equity) }}</b></div>
    </div>
    <p class="sub" style="margin-top: 8px" v-if="result.indicators?.length">
      指标（{{ result.join === 'or' ? '或' : '且' }}）：
      {{ result.indicators.map((x) => x.name).join('、') }}
    </p>
  </section>

  <section class="panel" style="margin-top: 12px" v-if="openTrade">
    <h2 style="margin: 0 0 8px; font-size: 15px">未平仓</h2>
    <p>
      {{ openTrade.entry_day }} @ {{ openTrade.entry_price }} → 现价 {{ openTrade.exit_price }}
      · 浮动
      <span :class="tone(openTrade.pnl)">{{ money(openTrade.pnl) }}</span>
    </p>
  </section>

  <section class="panel" style="margin-top: 12px" v-if="realized.length">
    <h2 style="margin: 0 0 10px; font-size: 15px">已平仓交易</h2>
    <el-table :data="realized" stripe size="small" max-height="420">
      <el-table-column prop="entry_day" label="开仓" width="140" />
      <el-table-column prop="exit_day" label="平仓" width="140" />
      <el-table-column prop="entry_price" label="开仓价" width="100" />
      <el-table-column prop="exit_price" label="平仓价" width="100" />
      <el-table-column label="盈亏" width="110">
        <template #default="{ row: t }">
          <span :class="tone(t.pnl)">{{ money(t.pnl) }}</span>
        </template>
      </el-table-column>
      <el-table-column prop="exit_reason" label="原因" min-width="100" />
    </el-table>
  </section>
</template>
