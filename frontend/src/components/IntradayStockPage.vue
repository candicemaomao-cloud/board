<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { api } from '../api'
import { money, pct, tone } from '../format'
import DailyBarChart from './DailyBarChart.vue'
import EquityChart from './EquityChart.vue'

const form = reactive({
  symbol: 'MU',
  pool: false,
  symbols: 'MU, TSLA, NVDA, WMT',
  days: 10,
  budget: 10000,
  hedge: false,
  integral: true,
  gradient: true,
  day_filter: true,
  vol_adapt: true,
  allow_short: true,
  allow_trend: false,
  vol_confirm: false,
  vol_z_th: 1,
  kelly: false,
  hedge_symbol: 'SPY',
  wma_filter: false,
  wma_period: 20,
})

const plans = ref([])
const loading = ref(false)
const error = ref('')
const result = ref(null)
const poolResult = ref(null)

const filePlans = computed(() =>
  (plans.value || []).filter((p) => p.source === 'file' && p.symbol),
)

const now = computed(() => result.value?.now || null)

const nowAction = computed(() => {
  const n = now.value
  if (!n) return ''
  if (n.fire_trend) {
    return `顺势买 @ ${n.price}（站上开盘区间 ${n.or_hi ?? ''}）`
  }
  if (n.fire_trend_short) {
    return `顺势空 @ ${n.price}（跌破开盘区间 ${n.or_lo ?? ''}）`
  }
  if (n.fire) {
    const pctn = n.frac != null ? Math.round(n.frac * 100) : 100
    return `现在买 @ ${n.price} · 仓位 ${pctn}%`
  }
  if (n.fire_short) {
    const pctn = n.frac_short != null ? Math.round(n.frac_short * 100) : 100
    return `现在空 @ ${n.price} · 仓位 ${pctn}%`
  }
  if (n.trend_long) return '顺势：已站上开盘 30min 高点且在加速，等下一根确认'
  if (n.trend_short) return '顺势：已跌破开盘 30min 低点且在加速，等下一根确认'
  if (n.time_ok === false || n.dip_time_ok === false) return '距收盘不足 90 分钟，不再开新仓'
  if (n.fade && (!result.value?.options?.vol_adapt || n.vol_tight !== false)) return '涨速变慢，考虑平多'
  if (n.fade_short && (!result.value?.options?.vol_adapt || n.vol_tight !== false)) return '跌速变慢，考虑平空'
  if (n.halfway) {
    return `半山腰（多）：面积 ${n.panic?.toFixed(4) ?? '—'} 低于 70 分位 ${n.p70?.toFixed(4) ?? '—'}，不买`
  }
  if (n.halfway_short) {
    return `半山腰（空）：面积 ${n.euphoria?.toFixed(4) ?? '—'} 低于 70 分位 ${n.p70s?.toFixed(4) ?? '—'}，不空`
  }
  if (n.local_min && result.value?.options?.vol_confirm && n.vol_ok === false) {
    return `量能不够：近3根最高 Z ${n.vol_z_peak ?? n.vol_z ?? '—'} < ${n.vol_z_th ?? 1}，再等放量`
  }
  if (n.local_max && result.value?.options?.vol_confirm && n.vol_ok === false) {
    return `量能不够：近3根最高 Z ${n.vol_z_peak ?? n.vol_z ?? '—'} < ${n.vol_z_th ?? 1}，再等放量`
  }
  if (n.local_min && n.frac > 0 && !n.confirm) return '多头面积过关，等下一根 5min 收盘突破前高'
  if (n.local_max && n.frac_short > 0 && !n.confirm_down) return '空头面积过关，等下一根 5min 收盘跌破前低'
  if (n.local_min && result.value?.options?.day_filter && !n.day_ok) {
    if (!n.last_up) return '过滤：最近一根 30min 还在跌，不做多'
    return '过滤：当天方向未过'
  }
  if (n.local_max && result.value?.options?.day_filter && !n.day_ok_short) {
    if (!n.last_down) return '过滤：最近一根 30min 还在涨，不做空'
    return '过滤：当天空头方向未过'
  }
  if (n.local_min && !n.integral_ok && result.value?.options?.integral) return '有底拐点，但 VWAP 下陷面积不够深'
  if (n.local_max && !n.integral_ok_short && result.value?.options?.integral) return '有顶拐点，但 VWAP 上伸面积不够高'
  if (n.mu != null && n.mu > 0) return '等局部底'
  if (n.mu != null && n.mu < 0 && result.value?.options?.allow_short) return '等局部顶'
  return '现在不开仓'
})

async function loadPlans() {
  try {
    plans.value = await api.strategyPlans()
  } catch {
    plans.value = []
  }
}

async function run() {
  error.value = ''
  loading.value = true
  const common = {
    days: Number(form.days) || 10,
    budget: Number(form.budget) || 5000,
    hedge: form.hedge,
    integral: form.integral,
    gradient: form.gradient,
    day_filter: form.day_filter,
    vol_adapt: form.vol_adapt,
    allow_short: form.allow_short,
    allow_trend: form.allow_trend,
    vol_confirm: form.vol_confirm,
    vol_z_th: Number(form.vol_z_th) || 1,
    kelly: form.kelly,
    hedge_symbol: (form.hedge_symbol || 'SPY').trim().toUpperCase(),
    wma_filter: form.wma_filter,
    wma_period: Number(form.wma_period) || 20,
  }
  try {
    if (form.pool) {
      const symbols = form.symbols
        .split(/[,\s]+/)
        .map((s) => s.trim().toUpperCase())
        .filter(Boolean)
      poolResult.value = await api.strategyIntradayBtPool({ ...common, symbols })
      result.value = null
    } else {
      result.value = await api.strategyIntradayBt({ symbol: form.symbol.trim().toUpperCase(), ...common })
      poolResult.value = null
    }
  } catch (e) {
    error.value = e.message || '回测失败'
    result.value = null
    poolResult.value = null
  } finally {
    loading.value = false
  }
}

onMounted(loadPlans)
</script>

<template>
  <p class="goal-lead">
    填一只股票，回放最近若干个交易日的 5 分钟 K 线。
    买卖点仍是 5min 拐点 + 30min μ。量能和半凯利默认关：勾上才过滤/调仓。
    量能看近 3 根成交量 Z 的最高值（拐点当根经常缩量）。抄底不要求价格站上 VWAP。
    半凯利用已平仓的真实胜率/盈亏比，不是写死 58%/2.0。本金是当天额度。
    多票轮动去「策略 → 日内配对」，不要把 PCA 塞进这一只的拐点。
  </p>

  <div class="range-row">
    <label class="check" style="margin-right: 8px">
      <input v-model="form.pool" type="checkbox" /> 候选池模式（多票每天只打最先出现的一单）
    </label>
  </div>

  <div class="range-row">
    <div class="watch-edit" style="flex: 1; margin: 0">
      <input
        v-if="!form.pool"
        v-model="form.symbol"
        placeholder="MU / TSLA / NVDA"
        @keydown.enter.prevent="run"
      />
      <input
        v-else
        v-model="form.symbols"
        placeholder="MU, TSLA, NVDA, WMT（逗号分隔，最多 8 只）"
        style="flex: 1"
        @keydown.enter.prevent="run"
      />
      <input v-model.number="form.days" type="number" min="3" max="40" title="回测天数" style="max-width: 88px" />
      <input v-model.number="form.budget" type="number" min="200" step="100" title="每天本金" style="max-width: 110px" />
      <button class="btn btn-primary" type="button" :disabled="loading" @click="run">
        {{ loading ? '回测中…' : '回测' }}
      </button>
    </div>
  </div>
  <div class="macro-chips" style="margin-top: 8px" v-if="!form.pool && filePlans.length">
    <button
      v-for="p in filePlans"
      :key="p.id"
      class="chip"
      type="button"
      :class="{ now: form.symbol === p.symbol }"
      @click="form.symbol = p.symbol"
    >
      {{ p.symbol }}
    </button>
  </div>

  <div class="range-row" style="margin-top: 8px">
    <label class="check"><input v-model="form.allow_trend" type="checkbox" /> 顺势突破（开盘 30min 区间 + 加速度）</label>
    <label class="check"><input v-model="form.allow_short" type="checkbox" /> 允许做空（局部顶 + μ 向下）</label>
    <label class="check"><input v-model="form.day_filter" type="checkbox" /> 当天过滤（μ 不含昨天 / 最近 30min 须同向）</label>
    <label class="check"><input v-model="form.vol_adapt" type="checkbox" /> 波动率出场（2.5×ATR 止损 + GARCH 调幅）</label>
    <label class="check"><input v-model="form.integral" type="checkbox" /> 积分闸门（VWAP 面积 90 分位 + 下一根确认 + 分批）</label>
    <label class="check"><input v-model="form.hedge" type="checkbox" /> 偏导对冲（{{ form.hedge_symbol || 'SPY' }}）</label>
    <label class="check"><input v-model="form.gradient" type="checkbox" /> 梯度调仓（收盘改权重）</label>
    <label class="check">
      <input v-model="form.vol_confirm" type="checkbox" />
      量能确认（近3根最高 Z ≥
      <input
        v-model.number="form.vol_z_th"
        type="number"
        min="0.5"
        max="3"
        step="0.1"
        style="width: 52px; margin: 0 4px"
        :disabled="!form.vol_confirm"
      />
      ）
    </label>
    <label class="check"><input v-model="form.kelly" type="checkbox" /> 半凯利（用已平仓的胜率/盈亏比）</label>
    <label class="check">
      <input v-model="form.wma_filter" type="checkbox" />
      WMA 方向确认（
      <input
        v-model.number="form.wma_period"
        type="number"
        min="3"
        max="60"
        step="1"
        style="width: 48px; margin: 0 4px"
        :disabled="!form.wma_filter"
      />
      根 5min，多要站上 / 空要跌破）
    </label>
  </div>

  <div class="error" v-if="error">{{ error }}</div>
  <p class="sub" v-else-if="loading">在拉 5min / 30min 和 {{ form.hedge ? form.hedge_symbol : 'K 线' }}，第一次可能要等十几秒。</p>

  <template v-if="result && !form.pool">
    <div class="match-row">
      <span class="match-badge" :class="result.total_pnl >= 0 ? 'up-dim' : 'down-dim'">
        {{ result.total_pnl >= 0 ? '净赚' : '净亏' }}
      </span>
      <div>
        <b>{{ result.symbol }} · {{ result.n_days }} 个交易日</b>
        <div class="sub">
          {{ result.source === 'binance' ? '币安' : 'TradingView' }}
          · {{ result.n_trades }} 笔<span v-if="result.n_short">（多 {{ result.n_long }} / 空 {{ result.n_short }}）</span> · 胜率 {{ result.win_rate }}%
          <span v-if="result.hedge_symbol"> · 对冲 {{ result.hedge_symbol }}</span>
        </div>
        <div class="sub" v-if="result.hedge_note">{{ result.hedge_note }}</div>
      </div>
    </div>

    <section class="cards" style="margin-top: 12px">
      <article class="card">
        <div class="label">合计净盈亏</div>
        <div class="value" :class="tone(result.total_pnl)">{{ money(result.total_pnl) }}</div>
        <div class="sub">每天 {{ money(result.budget, { signed: false }) }}</div>
      </article>
      <article class="card">
        <div class="label">股票腿</div>
        <div class="value" :class="tone(result.stock_pnl)">{{ money(result.stock_pnl) }}</div>
        <div class="sub">对冲腿 {{ money(result.hedge_pnl) }}</div>
      </article>
      <article class="card">
        <div class="label">胜率</div>
        <div class="value">{{ result.win_rate }}%</div>
        <div class="sub">{{ result.n_wins }} / {{ result.n_trades }} 笔</div>
      </article>
      <article class="card">
        <div class="label">最大回撤</div>
        <div class="value" :class="result.max_dd < 0 ? 'down' : ''">{{ money(result.max_dd) }}</div>
        <div class="sub">平均每笔 {{ money(result.avg_pnl) }}</div>
      </article>
    </section>

    <section class="panel" style="margin-top: 12px" v-if="now">
      <h2>现在 · {{ now.time }} ET @ {{ now.price }}</h2>
      <p class="sub">{{ nowAction }}</p>
      <div class="price-grid" style="margin-top: 10px">
        <div class="price-card">
          <span>f′ 速度</span>
          <b>{{ now.f1 == null ? '—' : (now.f1 >= 0 ? '+' : '') + now.f1.toFixed(3) }}</b>
        </div>
        <div class="price-card">
          <span>f″ 油门</span>
          <b>{{ now.f2 == null ? '—' : (now.f2 >= 0 ? '+' : '') + now.f2.toFixed(3) }}</b>
        </div>
        <div class="price-card">
          <span>30min μ</span>
          <b>{{ now.mu == null ? '—' : now.mu.toFixed(4) }}</b>
        </div>
        <div class="price-card">
          <span>VWAP 下陷 / 上伸</span>
          <b>{{ (now.panic == null ? '—' : now.panic.toFixed(4)) + ' / ' + (now.euphoria == null ? '—' : now.euphoria.toFixed(4)) }}</b>
        </div>
        <div class="price-card">
          <span>70 / 90 分位</span>
          <b>{{ (now.p70 == null ? '—' : now.p70.toFixed(4)) + ' / ' + (now.p90 == null ? '—' : now.p90.toFixed(4)) }}</b>
        </div>
        <div class="price-card">
          <span>z vs VWAP</span>
          <b>{{ now.z == null ? '—' : now.z.toFixed(2) }}</b>
        </div>
        <div class="price-card">
          <span>量 Z / 近3根</span>
          <b>{{ (now.vol_z == null ? '—' : now.vol_z.toFixed(2)) + ' / ' + (now.vol_z_peak == null ? '—' : now.vol_z_peak.toFixed(2)) }}</b>
        </div>
        <div class="price-card">
          <span>半凯利</span>
          <b>{{ now.kelly_scale == null ? '—' : now.kelly_scale.toFixed(2) }}</b>
        </div>
        <div class="price-card">
          <span>允许仓位</span>
          <b>{{ now.frac == null ? '—' : Math.round(now.frac * 100) + '%' }}</b>
        </div>
        <div class="price-card">
          <span>ATR / 止损</span>
          <b>{{ (now.atr == null ? '—' : now.atr.toFixed(2)) + ' / ' + (now.stop == null ? '—' : now.stop.toFixed(2)) }}</b>
        </div>
        <div class="price-card">
          <span>GARCH 倍率</span>
          <b>{{ now.garch_scale == null ? '—' : now.garch_scale.toFixed(2) + '×' }}</b>
        </div>
        <div class="price-card">
          <span>β vs {{ result.hedge_symbol || 'SPY' }}</span>
          <b>{{ now.beta == null ? '—' : now.beta.toFixed(2) }}</b>
        </div>
        <div class="price-card">
          <span>止盈（≥3×ATR / $20）</span>
          <b>{{ now.up == null ? '—' : now.up.toFixed(2) }}</b>
        </div>
        <div class="price-card" v-if="result.options && result.options.wma_filter">
          <span>WMA{{ result.options.wma_period }}</span>
          <b>{{ now.wma == null ? '—' : now.wma.toFixed(2) }}</b>
        </div>
      </div>
    </section>

    <section class="panel" style="margin-top: 12px">
      <h2>现在这些零件在干什么</h2>
      <div class="buy-grid" style="margin-top: 8px">
        <div v-for="tool in result.tools" :key="tool.id" class="buy-card">
          <span>{{ tool.role }}</span>
          <b>{{ tool.name }}</b>
          <div class="sub">{{ tool.now }}</div>
          <div class="sub">{{ tool.note }}</div>
        </div>
      </div>
    </section>

    <div class="grid-2" style="margin-top: 12px">
      <section class="panel">
        <h2>累计净盈亏</h2>
        <EquityChart :curve="result.daily" />
      </section>
      <section class="panel">
        <h2>每日净盈亏</h2>
        <DailyBarChart :curve="result.daily" />
      </section>
    </div>

    <section class="panel" style="margin-top: 12px">
      <h2>按日</h2>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>日期</th>
              <th>笔数</th>
              <th>权重</th>
              <th>股票</th>
              <th>对冲</th>
              <th>当日净</th>
              <th>累计</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in result.daily" :key="row.date">
              <td>{{ row.date.slice(5) }}</td>
              <td>{{ row.trades }}</td>
              <td>{{ row.weight }}</td>
              <td :class="tone(row.stock_pnl)">{{ money(row.stock_pnl) }}</td>
              <td :class="tone(row.hedge_pnl)">{{ money(row.hedge_pnl) }}</td>
              <td :class="tone(row.daily_pnl)">{{ money(row.daily_pnl) }}</td>
              <td>{{ money(row.cumulative_pnl) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <section class="panel" style="margin-top: 12px">
      <h2>逐笔</h2>
      <div class="table-wrap" v-if="result.trades.length">
        <table>
          <thead>
            <tr>
              <th>日期</th>
              <th>方向</th>
              <th>开仓</th>
              <th>开仓价</th>
              <th>平仓</th>
              <th>平仓价</th>
              <th>原因</th>
              <th>仓位</th>
              <th>量Z</th>
              <th>凯利</th>
              <th>β</th>
              <th>股票</th>
              <th>对冲</th>
              <th>净</th>
              <th>%</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(t, i) in result.trades" :key="i">
              <td>{{ t.day.slice(5) }}</td>
              <td>{{ t.side === 'short' ? '空' : '多' }}</td>
              <td>{{ t.buy_time }}</td>
              <td>{{ t.buy.toFixed(2) }}</td>
              <td>{{ t.sell_time }}</td>
              <td>{{ t.sell.toFixed(2) }}</td>
              <td>{{ t.exit }}</td>
              <td>{{ t.stage || (t.frac == null ? '—' : Math.round(t.frac * 100) + '%') }}</td>
              <td>{{ t.vol_z == null ? '—' : t.vol_z.toFixed(2) }}</td>
              <td>{{ t.kelly == null ? '—' : t.kelly.toFixed(2) }}</td>
              <td>{{ t.beta == null ? '—' : t.beta.toFixed(2) }}</td>
              <td :class="tone(t.stock_pnl)">{{ money(t.stock_pnl) }}</td>
              <td :class="tone(t.hedge_pnl)">{{ money(t.hedge_pnl) }}</td>
              <td :class="tone(t.pnl)">{{ money(t.pnl) }}</td>
              <td :class="tone(t.pnl_pct)">{{ pct(t.pnl_pct) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <p class="sub" v-else>这段日子没有开仓。积分开着时，VWAP 下陷/上伸面积不到自己的 70 分位、或下一根没有突破确认，就不会进。</p>
    </section>
  </template>

  <template v-if="poolResult">
    <div class="match-row">
      <span class="match-badge" :class="poolResult.total_pnl >= 0 ? 'up-dim' : 'down-dim'">
        {{ poolResult.total_pnl >= 0 ? '净赚' : '净亏' }}
      </span>
      <div>
        <b>候选池 {{ poolResult.symbols.join(' / ') }} · {{ poolResult.n_days }} 个交易日</b>
        <div class="sub">
          {{ poolResult.n_days_traded }} / {{ poolResult.n_days }} 天开了单
          · {{ poolResult.n_trades }} 笔<span v-if="poolResult.n_short">（多 {{ poolResult.n_long }} / 空 {{ poolResult.n_short }}）</span>
          · 胜率 {{ poolResult.win_rate }}%
          <span v-if="poolResult.hedge_symbol"> · 对冲 {{ poolResult.hedge_symbol }}</span>
        </div>
        <div class="sub" v-if="poolResult.hedge_note">{{ poolResult.hedge_note }}</div>
        <div class="sub" v-if="poolResult.skipped && poolResult.skipped.length">
          跳过：{{ poolResult.skipped.map((s) => `${s.symbol}(${s.error})`).join('，') }}
        </div>
      </div>
    </div>

    <section class="cards" style="margin-top: 12px">
      <article class="card">
        <div class="label">合计净盈亏</div>
        <div class="value" :class="tone(poolResult.total_pnl)">{{ money(poolResult.total_pnl) }}</div>
        <div class="sub">每天 {{ money(poolResult.budget, { signed: false }) }}</div>
      </article>
      <article class="card">
        <div class="label">股票腿</div>
        <div class="value" :class="tone(poolResult.stock_pnl)">{{ money(poolResult.stock_pnl) }}</div>
        <div class="sub">对冲腿 {{ money(poolResult.hedge_pnl) }}</div>
      </article>
      <article class="card">
        <div class="label">胜率</div>
        <div class="value">{{ poolResult.win_rate }}%</div>
        <div class="sub">{{ poolResult.n_wins }} / {{ poolResult.n_trades }} 笔</div>
      </article>
      <article class="card">
        <div class="label">最大回撤</div>
        <div class="value" :class="poolResult.max_dd < 0 ? 'down' : ''">{{ money(poolResult.max_dd) }}</div>
        <div class="sub">平均每笔 {{ money(poolResult.avg_pnl) }}</div>
      </article>
    </section>

    <section class="panel" style="margin-top: 12px" v-if="poolResult.now && poolResult.now.length">
      <h2>候选池现在的状态</h2>
      <div class="buy-grid" style="margin-top: 8px">
        <div v-for="n in poolResult.now" :key="n.symbol" class="buy-card">
          <span>{{ n.local_min ? '局部底' : n.local_max ? '局部顶' : '等拐点' }}</span>
          <b>{{ n.symbol }} @ {{ n.price }}</b>
          <div class="sub">f′ {{ n.f1 == null ? '—' : n.f1.toFixed(3) }} · f″ {{ n.f2 == null ? '—' : n.f2.toFixed(3) }} · μ {{ n.mu == null ? '—' : n.mu.toFixed(4) }}</div>
          <div class="sub" v-if="poolResult.options && poolResult.options.wma_filter">
            WMA{{ poolResult.options.wma_period }} {{ n.wma == null ? '—' : n.wma.toFixed(2) }}
          </div>
          <div class="sub">
            {{ n.fire ? `现在买 · 仓位 ${Math.round((n.frac || 0) * 100)}%` : n.fire_short ? `现在空 · 仓位 ${Math.round((n.frac_short || 0) * 100)}%` : '未触发' }}
          </div>
        </div>
      </div>
    </section>

    <div class="grid-2" style="margin-top: 12px">
      <section class="panel">
        <h2>累计净盈亏</h2>
        <EquityChart :curve="poolResult.daily" />
      </section>
      <section class="panel">
        <h2>每日净盈亏</h2>
        <DailyBarChart :curve="poolResult.daily" />
      </section>
    </div>

    <section class="panel" style="margin-top: 12px">
      <h2>按日 · 打的哪只</h2>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>日期</th>
              <th>票</th>
              <th>入场时间</th>
              <th>笔数</th>
              <th>当日净</th>
              <th>累计</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in poolResult.picks" :key="row.day">
              <td>{{ row.day.slice(5) }}</td>
              <td>{{ row.symbol || '—' }}</td>
              <td>{{ row.time || '—' }}</td>
              <td>{{ (poolResult.daily.find((d) => d.date === row.day) || {}).trades || 0 }}</td>
              <td :class="tone((poolResult.daily.find((d) => d.date === row.day) || {}).daily_pnl)">
                {{ money((poolResult.daily.find((d) => d.date === row.day) || {}).daily_pnl || 0) }}
              </td>
              <td>{{ money((poolResult.daily.find((d) => d.date === row.day) || {}).cumulative_pnl || 0) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <section class="panel" style="margin-top: 12px">
      <h2>逐笔</h2>
      <div class="table-wrap" v-if="poolResult.trades.length">
        <table>
          <thead>
            <tr>
              <th>日期</th>
              <th>票</th>
              <th>方向</th>
              <th>开仓</th>
              <th>开仓价</th>
              <th>平仓</th>
              <th>平仓价</th>
              <th>原因</th>
              <th>仓位</th>
              <th>股票</th>
              <th>对冲</th>
              <th>净</th>
              <th>%</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(t, i) in poolResult.trades" :key="i">
              <td>{{ t.day.slice(5) }}</td>
              <td>{{ t.symbol }}</td>
              <td>{{ t.side === 'short' ? '空' : '多' }}</td>
              <td>{{ t.buy_time }}</td>
              <td>{{ t.buy.toFixed(2) }}</td>
              <td>{{ t.sell_time }}</td>
              <td>{{ t.sell.toFixed(2) }}</td>
              <td>{{ t.exit }}</td>
              <td>{{ t.stage || (t.frac == null ? '—' : Math.round(t.frac * 100) + '%') }}</td>
              <td :class="tone(t.stock_pnl)">{{ money(t.stock_pnl) }}</td>
              <td :class="tone(t.hedge_pnl)">{{ money(t.hedge_pnl) }}</td>
              <td :class="tone(t.pnl)">{{ money(t.pnl) }}</td>
              <td :class="tone(t.pnl_pct)">{{ pct(t.pnl_pct) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <p class="sub" v-else>这段日子候选池里没有一只出过信号。</p>
    </section>
  </template>
</template>
