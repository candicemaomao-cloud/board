<script setup>
import { computed, reactive, ref } from 'vue'
import { api } from '../api'
import { money, tone } from '../format'

const form = reactive({
  symbol: 'MU',
  useRange: false,
  range_start: '',
  range_end: '',
  backtest_trading_days: 21,
  capital_per_trade: 10000,
  compound: true,
  allow_short: true,
  short_symbol: '',
  use_sma_precondition: true,
  sma_short_period: 10,
  sma_long_period: 20,
  stop_loss_atr_mult: 0.5,
  daily_atr_n: 14,
  lookback: 5,
  context_days: 3,
  vwap_trend_lookback: 6,
  volume_dist_days: 15,
  volume_high_pct: 90,
  volume_low_pct: 10,
  min_slot_samples: 8,
  volume_max_window_days: 14,
  trade_every_signal: false,
  use_shape_filter: false,
  shape_confidence_threshold: 40,
  shape_min_train_days: 30,
  close_no_trade_minutes: 0,
  market_symbol: 'SPY',
})

const loading = ref(false)
const error = ref('')
const result = ref(null)
const showAdvanced = ref(false)

async function run() {
  error.value = ''
  loading.value = true
  try {
    result.value = await api.newStrategyBacktest({
      symbol: form.symbol.trim().toUpperCase(),
      range_start: form.useRange && form.range_start ? form.range_start : null,
      range_end: form.useRange && form.range_end ? form.range_end : null,
      backtest_trading_days: Number(form.backtest_trading_days) || 21,
      capital_per_trade: Number(form.capital_per_trade) || 10000,
      compound: form.compound,
      allow_short: form.allow_short,
      short_symbol: form.allow_short && form.short_symbol.trim() ? form.short_symbol.trim().toUpperCase() : null,
      use_sma_precondition: form.use_sma_precondition,
      sma_short_period: Number(form.sma_short_period) || 10,
      sma_long_period: Number(form.sma_long_period) || 20,
      stop_loss_atr_mult: Number(form.stop_loss_atr_mult) || 0.5,
      daily_atr_n: Number(form.daily_atr_n) || 14,
      lookback: Number(form.lookback) || 5,
      context_days: Number(form.context_days) || 3,
      vwap_trend_lookback: Number(form.vwap_trend_lookback) || 6,
      volume_dist_days: Number(form.volume_dist_days) || 15,
      volume_high_pct: Number(form.volume_high_pct) || 90,
      volume_low_pct: Number(form.volume_low_pct) || 10,
      min_slot_samples: Number(form.min_slot_samples) || 8,
      volume_max_window_days: Number(form.volume_max_window_days) || 14,
      trade_every_signal: form.trade_every_signal,
      use_shape_filter: form.use_shape_filter,
      shape_confidence_threshold: Number(form.shape_confidence_threshold) || 40,
      shape_min_train_days: Number(form.shape_min_train_days) || 30,
      close_no_trade_minutes: Number(form.close_no_trade_minutes) || 0,
      market_symbol: form.market_symbol.trim() ? form.market_symbol.trim().toUpperCase() : null,
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
const unrealizedTrade = computed(() => (result.value?.trades || []).find((t) => !t.exit_day) || null)
const dailySignalCounts = computed(() => result.value?.daily_signal_counts || [])
const shapePredictions = computed(() => result.value?.shape_predictions || [])
const signalLog = computed(() => result.value?.signal_log || [])
const showSignalLog = ref(false)
const totalSignals = computed(() => dailySignalCounts.value.reduce((sum, d) => sum + d.total, 0))
const totalTaken = computed(() => dailySignalCounts.value.reduce((sum, d) => sum + d.taken, 0))
</script>

<template>
  <p class="goal-lead">
    从零重写的新策略：只靠 VWAP 一条参照线判断方向，放量确认，二阶导数判断真反转平仓，
    止损用日线 ATR。仓位可以跨日持有，不强制收盘平仓。做空前是否要求先"站上再跌破"均线
    可以自己开关。下面这些参数都是可选的，不填就用默认值。
  </p>

  <div class="range-row">
    <div class="watch-edit" style="flex: 1; margin: 0">
      <input v-model="form.symbol" placeholder="MU / TSLA / NVDA" @keydown.enter.prevent="run" />
      <input
        v-model.number="form.capital_per_trade"
        type="number"
        min="200"
        step="100"
        title="起始本金"
        style="max-width: 110px"
      />
      <button class="btn btn-primary" type="button" :disabled="loading" @click="run">
        {{ loading ? '回测中…' : '回测' }}
      </button>
    </div>
  </div>

  <div class="range-row" style="margin-top: 8px">
    <label class="check">
      <input v-model="form.compound" type="checkbox" />
      复利（每笔用当前账户权益算仓位，本金随盈亏滚动；关掉则每笔都固定按起始本金算，互不影响）
    </label>
  </div>

  <div class="range-row" style="margin-top: 8px">
    <label class="check"><input v-model="form.useRange" type="checkbox" /> 指定日期区间（不勾就用最近 N 个交易日）</label>
    <template v-if="!form.useRange">
      <label class="check">
        最近
        <input
          v-model.number="form.backtest_trading_days"
          type="number"
          min="5"
          max="120"
          style="width: 56px; margin: 0 4px"
        />
        个交易日
      </label>
    </template>
    <template v-else>
      <input v-model="form.range_start" type="date" title="开始日期" />
      <span class="sub">~</span>
      <input v-model="form.range_end" type="date" title="结束日期（留空到今天）" />
    </template>
  </div>

  <div class="range-row" style="margin-top: 8px">
    <label class="check"><input v-model="form.allow_short" type="checkbox" /> 允许做空（有些票不打算做空，关掉就只做多）</label>
    <label class="check" v-if="form.allow_short">
      做空走 ETF：
      <input
        v-model="form.short_symbol"
        type="text"
        placeholder="留空=直接做空这只票本身"
        style="width: 140px; margin: 0 4px"
      />
      （方向信号照样看主标的自己的走势，只是实际开/平空单、止损换成这只 ETF 同一时刻的价格）
    </label>
  </div>

  <div class="range-row" style="margin-top: 8px">
    <label class="check">
      <input v-model="form.use_sma_precondition" type="checkbox" :disabled="!form.allow_short" />
      做空需先"站上再跌破" SMA
      <input
        v-model.number="form.sma_short_period"
        type="number"
        min="2"
        max="60"
        style="width: 44px; margin: 0 4px"
        :disabled="!form.use_sma_precondition || !form.allow_short"
      />
      /
      <input
        v-model.number="form.sma_long_period"
        type="number"
        min="2"
        max="120"
        style="width: 44px; margin: 0 4px"
        :disabled="!form.use_sma_precondition || !form.allow_short"
      />
      （关掉后做空跟做多完全对称，只看 VWAP 趋势+放量）
    </label>
  </div>

  <div class="range-row" style="margin-top: 8px">
    <label class="check">
      止损 =
      <input
        v-model.number="form.stop_loss_atr_mult"
        type="number"
        min="0.1"
        max="5"
        step="0.1"
        style="width: 52px; margin: 0 4px"
      />
      × 日线
      <input
        v-model.number="form.daily_atr_n"
        type="number"
        min="5"
        max="60"
        style="width: 44px; margin: 0 4px"
      />
      期 ATR
    </label>
    <button class="btn" type="button" @click="showAdvanced = !showAdvanced">
      {{ showAdvanced ? '收起高级参数' : '展开高级参数' }}
    </button>
  </div>

  <div class="range-row" style="margin-top: 8px; flex-wrap: wrap">
    <label class="check">
      <input v-model="form.use_shape_filter" type="checkbox" />
      形态过滤（先预测今天最可能是哪种VWAP轨道形态，按形态决定只做多/只做空/双向/观望）
    </label>
    <template v-if="form.use_shape_filter">
      <label class="check">
        置信度阈值 ≥
        <input v-model.number="form.shape_confidence_threshold" type="number" min="10" max="90" style="width: 48px; margin: 0 4px" />
        %
      </label>
      <label class="check">
        大盘参照
        <input v-model="form.market_symbol" type="text" placeholder="SPY，留空=不看大盘方向" style="width: 70px; margin: 0 4px" />
      </label>
    </template>
    <label class="check">
      <input v-model="form.trade_every_signal" type="checkbox" />
      每次信号都交易（反向持仓先平再反手，不要求必须空仓才进场）
    </label>
    <label class="check">
      收盘前
      <input v-model.number="form.close_no_trade_minutes" type="number" min="0" max="120" style="width: 48px; margin: 0 4px" />
      分钟不开新仓（0=不限制，全局规则，跟"上涨"形态自己那条收盘前限制分开算）
    </label>
  </div>

  <div class="range-row" style="margin-top: 8px; flex-wrap: wrap" v-if="showAdvanced">
    <label class="check">
      导数滑窗
      <input v-model.number="form.lookback" type="number" min="3" max="20" style="width: 48px; margin: 0 4px" />
      根
    </label>
    <label class="check">
      铺垫
      <input v-model.number="form.context_days" type="number" min="1" max="10" style="width: 48px; margin: 0 4px" />
      个交易日
    </label>
    <label class="check">
      VWAP 趋势回看
      <input v-model.number="form.vwap_trend_lookback" type="number" min="2" max="30" style="width: 48px; margin: 0 4px" />
      根
    </label>
    <label class="check">
      量能分布取过去
      <input v-model.number="form.volume_dist_days" type="number" min="5" max="60" style="width: 48px; margin: 0 4px" />
      天
    </label>
    <label class="check">
      放量分位 ≥
      <input v-model.number="form.volume_high_pct" type="number" min="50" max="99" style="width: 48px; margin: 0 4px" />
      / 缩量分位 ≤
      <input v-model.number="form.volume_low_pct" type="number" min="1" max="50" style="width: 48px; margin: 0 4px" />
    </label>
    <label class="check">
      时间槽最少样本
      <input v-model.number="form.min_slot_samples" type="number" min="3" max="30" style="width: 48px; margin: 0 4px" />
    </label>
    <label class="check">
      滚动最高量取过去
      <input v-model.number="form.volume_max_window_days" type="number" min="5" max="60" style="width: 48px; margin: 0 4px" />
      天
    </label>
    <label class="check" v-if="form.use_shape_filter">
      形态模型最少训练天数
      <input v-model.number="form.shape_min_train_days" type="number" min="10" max="120" style="width: 48px; margin: 0 4px" />
    </label>
  </div>

  <div class="error" v-if="error">{{ error }}</div>
  <p class="sub" v-else-if="loading">在拉日线 + 5min K 线，第一次可能要等十几秒。</p>

  <template v-if="result">
    <div class="match-row" v-if="summary">
      <span class="match-badge" :class="summary.total_pnl >= 0 ? 'up-dim' : 'down-dim'">
        {{ summary.total_pnl >= 0 ? '净赚' : '净亏' }}
      </span>
      <div>
        <b>{{ result.symbol }}</b>
        <div class="sub" v-if="result.effective_range">
          {{ result.effective_range.start }} ~ {{ result.effective_range.end }}（{{ result.n_days }} 个交易日）
          · {{ summary.n_trades }} 笔（多 {{ summary.n_long }} / 空 {{ summary.n_short }}）
          · 胜率 {{ summary.win_rate }}%
        </div>
        <div class="sub" v-for="(w, i) in result.warnings" :key="i">⚠️ {{ w }}</div>
      </div>
    </div>

    <section class="cards" style="margin-top: 12px" v-if="summary">
      <article class="card">
        <div class="label">合计净盈亏</div>
        <div class="value" :class="tone(summary.total_pnl)">{{ money(summary.total_pnl) }}</div>
        <div class="sub">
          起始 {{ money(summary.starting_capital, { signed: false }) }} → 期末 {{ money(summary.final_equity, { signed: false }) }}
          （{{ summary.compound ? '复利' : '不复利' }}，收益率 {{ summary.return_pct }}%）
        </div>
      </article>
      <article class="card">
        <div class="label">胜率</div>
        <div class="value">{{ summary.win_rate }}%</div>
        <div class="sub">平均赚 {{ money(summary.avg_win) }} / 平均亏 {{ money(summary.avg_loss) }}</div>
      </article>
      <article class="card">
        <div class="label">盈亏比率</div>
        <div class="value">
          {{
            summary.payoff_ratio != null
              ? `1 : ${summary.payoff_ratio}`
              : summary.avg_win > 0 && !(summary.avg_loss < 0)
                ? '全胜'
                : '—'
          }}
        </div>
        <div class="sub">平均盈利 ÷ |平均亏损|</div>
      </article>
      <article class="card">
        <div class="label">出场原因</div>
        <div class="value">{{ summary.reversal_exits }} 反转</div>
        <div class="sub">止损 {{ summary.stop_exits }} 笔 · 跨日持仓 {{ summary.overnight }} 笔</div>
      </article>
      <article class="card">
        <div class="label">未平仓</div>
        <div class="value">{{ unrealizedTrade ? `${unrealizedTrade.side} ${unrealizedTrade.symbol}` : '—' }}</div>
        <div class="sub" v-if="unrealizedTrade">
          {{ unrealizedTrade.entry_day }} {{ unrealizedTrade.entry_time }} @ {{ unrealizedTrade.entry_price.toFixed(3) }}（未计入统计）
        </div>
        <div class="sub" v-else>没有遗留仓位</div>
      </article>
    </section>

    <section class="panel" style="margin-top: 12px">
      <h2>逐笔</h2>
      <div class="table-wrap" v-if="realizedTrades.length">
        <table>
          <thead>
            <tr>
              <th>方向</th>
              <th>标的</th>
              <th>开仓</th>
              <th>开仓价</th>
              <th>开仓形态</th>
              <th>开仓子状态</th>
              <th>平仓</th>
              <th>平仓价</th>
              <th>原因</th>
              <th>跨日</th>
              <th>盈亏</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(t, i) in realizedTrades" :key="i">
              <td>{{ t.side }}</td>
              <td>{{ t.symbol }}</td>
              <td>{{ t.entry_day }} {{ t.entry_time }}</td>
              <td>{{ t.entry_price.toFixed(3) }}</td>
              <td>{{ t.entry_shape || '—' }}</td>
              <td>{{ t.entry_substate || '—' }}</td>
              <td>{{ t.exit_day }} {{ t.exit_time }}</td>
              <td>{{ t.exit_price.toFixed(3) }}</td>
              <td>{{ t.exit_reason }}</td>
              <td>{{ t.entry_day !== t.exit_day ? '是' : '' }}</td>
              <td :class="tone(t.pnl)">{{ money(t.pnl) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <p class="sub" v-else>这段时间没有产生任何交易（进场条件从未触发）。</p>
    </section>

    <section class="panel" style="margin-top: 12px" v-if="form.use_shape_filter">
      <h2>形态预测</h2>
      <p class="sub" style="margin-top: 4px">
        每天开盘前用历史数据预测当天最可能是哪种 VWAP 轨道形态，按形态决定当天的开单模式。
        置信度低于阈值时按"其他"处理（观望，当天不开新仓）。
      </p>
      <div class="table-wrap" style="margin-top: 8px" v-if="shapePredictions.length">
        <table>
          <thead>
            <tr>
              <th>日期</th>
              <th>预测形态</th>
              <th>置信度</th>
              <th>应用模式</th>
              <th>备注</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in shapePredictions" :key="row.day">
              <td>{{ row.day.slice(5) }}</td>
              <td>{{ row.predicted_shape || '—' }}</td>
              <td>{{ row.confidence != null ? row.confidence + '%' : '—' }}</td>
              <td>{{ row.applied_mode }}</td>
              <td class="sub">{{ row.reason }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <p class="sub" v-else>形态过滤未生效（可能是训练样本不够，看上面的警告信息）。</p>
    </section>

    <section class="panel" style="margin-top: 12px">
      <h2>信号日志</h2>
      <p class="sub" style="margin-top: 4px">
        按天统计原始信号触发次数——只要 VWAP 趋势+放量（或反转+放量）条件成立就算一次，不管当时是不是已经持仓。
        「已成交」是这次触发真的变成了一笔开仓/平仓；没变成交易通常是因为当时已经持有同方向仓位。
        合计触发 {{ totalSignals }} 次，其中 {{ totalTaken }} 次真的成交。
      </p>
      <div class="table-wrap" style="margin-top: 8px" v-if="dailySignalCounts.length">
        <table>
          <thead>
            <tr>
              <th>日期</th>
              <th>买入信号</th>
              <th>卖出/做空信号</th>
              <th>多头反转</th>
              <th>空头反转</th>
              <th>合计</th>
              <th>已成交</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in dailySignalCounts" :key="row.date">
              <td>{{ row.date.slice(5) }}</td>
              <td>{{ row.buy }}</td>
              <td>{{ row.sell }}</td>
              <td>{{ row.exit_long }}</td>
              <td>{{ row.exit_short }}</td>
              <td>{{ row.total }}</td>
              <td>{{ row.taken }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <p class="sub" v-else>这段时间没有任何原始信号触发。</p>

      <button
        class="btn"
        type="button"
        style="margin-top: 10px"
        v-if="signalLog.length"
        @click="showSignalLog = !showSignalLog"
      >
        {{ showSignalLog ? '收起逐条日志' : `展开逐条日志（共 ${signalLog.length} 条）` }}
      </button>
      <div class="table-wrap" style="margin-top: 8px" v-if="showSignalLog && signalLog.length">
        <table>
          <thead>
            <tr>
              <th>日期</th>
              <th>时间</th>
              <th>类型</th>
              <th>价格</th>
              <th>已成交</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(e, i) in signalLog" :key="i">
              <td>{{ e.day.slice(5) }}</td>
              <td>{{ e.time }}</td>
              <td>{{ e.type }}</td>
              <td>{{ e.price.toFixed(3) }}</td>
              <td>{{ e.taken ? '是' : '' }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  </template>
</template>
