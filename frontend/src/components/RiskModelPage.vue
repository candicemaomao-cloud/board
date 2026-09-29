<script setup>
import { computed, ref } from 'vue'
import { api } from '../api'
import { money } from '../format'
import RollingBetaChart from './RollingBetaChart.vue'

const symbol = ref('TSLA')
const windowDays = ref(252)
const confidence = ref(0.95)
const loading = ref(false)
const error = ref('')
const data = ref(null)
const factorData = ref(null)
const factorLoading = ref(false)
const factorError = ref('')

const CONF_OPTS = [
  { value: 0.9, label: '90%' },
  { value: 0.95, label: '95%' },
  { value: 0.99, label: '99%' },
]

const WINDOW_OPTS = [
  { value: 63, label: '近 3 月' },
  { value: 126, label: '近 6 月' },
  { value: 252, label: '近 1 年' },
]
const shocks = ref({
  SPY: '-5', QQQ: '-6', SOXX: '-10', TNX: '0.20', DXY: '1', VIX: '15', USO: '-3', GLD: '1',
})

function pct(n, digits = 2) {
  if (n == null || Number.isNaN(Number(n))) return '—'
  return `${(Number(n) * 100).toFixed(digits)}%`
}

function pctPoints(n, digits = 2) {
  if (n == null || Number.isNaN(Number(n))) return '—'
  return `${Number(n).toFixed(digits)}%`
}

function num(n, digits = 2) {
  if (n == null || Number.isNaN(Number(n))) return '—'
  return Number(n).toFixed(digits)
}

function px(n) {
  if (n == null || n === '') return '—'
  return money(n, { signed: false })
}

function sig(s) {
  return s || ''
}

function ffTip(block, name) {
  const L = block?.loadings?.[name]
  if (!L) return ''
  const t = L.t_stat != null ? `t=${Number(L.t_stat).toFixed(2)}` : ''
  const p = L.p_value != null ? `p=${Number(L.p_value).toFixed(3)}` : ''
  return [t, p, L.sig].filter(Boolean).join(' ')
}

const vol = computed(() => data.value?.volatility || {})
const varBlock = computed(() => data.value?.var || {})
const cvar = computed(() => data.value?.cvar || {})
const beta = computed(() => data.value?.beta || {})
const factors = computed(() => data.value?.factors || {})
const industry = computed(() => data.value?.industry || {})
const ratios = computed(() => data.value?.ratios || {})
const dd = computed(() => data.value?.drawdown || {})
const evt = computed(() => data.value?.evt || {})
const liq = computed(() => data.value?.liquidity || {})
const ols = computed(() => factorData.value?.ols || {})
const scenario = computed(() => factorData.value?.scenario || {})
const rolling = computed(() => factorData.value?.rolling || {})
const rollHighlight = computed(() => (ols.value?.ranked || []).slice(0, 3).map((r) => r.key))

function shockPayload() {
  const out = {}
  const map = {
    SPY: 100, QQQ: 100, SOXX: 100, DXY: 100, VIX: 100, USO: 100, GLD: 100, TNX: 1,
  }
  for (const [k, raw] of Object.entries(shocks.value)) {
    if (raw === '' || raw == null) continue
    const n = Number(raw)
    if (Number.isNaN(n)) continue
    // UI 输入：权益/商品/VIX/DXY 用百分比点数；TNX 用百分点变化
    out[k] = k === 'TNX' ? n : n / (map[k] || 100)
  }
  return out
}

async function runFactors(code) {
  factorError.value = ''
  factorLoading.value = true
  try {
    factorData.value = await api.factorExposure({
      symbol: code,
      window: windowDays.value,
      roll_window: Math.min(252, windowDays.value),
      shocks: shockPayload(),
    })
  } catch (e) {
    factorData.value = null
    factorError.value = e.message
  } finally {
    factorLoading.value = false
  }
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
  factorData.value = null
  try {
    data.value = await api.risk({
      symbol: code,
      window: windowDays.value,
      confidence: confidence.value,
    })
    // 因子暴露独立拉取，不阻塞主风险结果展示
    runFactors(code)
  } catch (e) {
    data.value = null
    error.value = e.message
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <p class="goal-lead">
    波动率 / VaR / FF / EVT 做风险度量；下方「因子暴露」用多元 OLS 解释对谁敏感（不是点预测）。价格来自 TradingView / Yahoo。
  </p>

  <div class="range-row">
    <div class="watch-edit" style="flex: 1; margin: 0">
      <input
        v-model="symbol"
        placeholder="TSLA / AAPL / NVDA / BTCUSDT"
        @keydown.enter.prevent="run"
      />
      <select v-model.number="windowDays" class="strategy-select">
        <option v-for="opt in WINDOW_OPTS" :key="opt.value" :value="opt.value">
          {{ opt.label }}
        </option>
      </select>
      <select v-model.number="confidence" class="strategy-select">
        <option v-for="opt in CONF_OPTS" :key="opt.value" :value="opt.value">
          置信度 {{ opt.label }}
        </option>
      </select>
      <button class="btn btn-primary" type="button" :disabled="loading" @click="run">
        {{ loading ? '测算中…' : '测算风险' }}
      </button>
    </div>
  </div>

  <div class="error" v-if="error">{{ error }}</div>
  <p class="sub" v-else-if="loading">正在拉日线并计算风险指标…</p>
  <p class="sub" v-else-if="!data">输入代码后点测算。</p>

  <template v-if="data">
    <section class="panel" style="margin-top: 12px">
      <h2>{{ data.symbol }} · {{ px(data.price) }}</h2>
      <p class="sub">
        数据源 {{ data.source }} · 样本 {{ data.bars }} 根日线 / {{ data.returns }} 个收益 ·
        窗口 {{ data.window }} · 置信度 {{ (data.confidence * 100).toFixed(0) }}%
      </p>
    </section>

    <section class="panel" style="margin-top: 12px">
      <h2>1. 波动率</h2>
      <div class="price-grid">
        <div class="price-card">
          <span>历史波动率（年化）</span>
          <b>{{ pct(vol.historical?.annual) }}</b>
          <small class="sub">日 {{ pct(vol.historical?.daily, 3) }} · N={{ vol.historical?.window }}</small>
        </div>
        <div class="price-card">
          <span>EWMA（年化）</span>
          <b>{{ pct(vol.ewma?.annual) }}</b>
          <small class="sub">λ={{ vol.ewma?.lambda }} · RiskMetrics</small>
        </div>
        <div class="price-card">
          <span>GARCH(1,1)（年化）</span>
          <b>{{ pct(vol.garch?.annual) }}</b>
          <small class="sub" v-if="vol.garch?.params">
            α={{ vol.garch.params.alpha }} · β={{ vol.garch.params.beta }}
          </small>
          <small class="sub" v-else>{{ vol.garch?.note || '—' }}</small>
        </div>
        <div class="price-card">
          <span>GJR-GARCH（年化）</span>
          <b>{{ pct(vol.gjr_garch?.annual) }}</b>
          <small class="sub" v-if="vol.gjr_garch?.params">
            γ={{ vol.gjr_garch.params.gamma }} · {{ vol.gjr_garch.leverage }}
          </small>
          <small class="sub" v-else>{{ vol.gjr_garch?.note || '—' }}</small>
        </div>
        <div class="price-card">
          <span>EGARCH（年化）</span>
          <b>{{ pct(vol.egarch?.annual) }}</b>
          <small class="sub" v-if="vol.egarch?.params">
            γ={{ vol.egarch.params.gamma }} · {{ vol.egarch.leverage }}
          </small>
          <small class="sub" v-else>{{ vol.egarch?.note || '—' }}</small>
        </div>
      </div>
    </section>

    <section class="panel" style="margin-top: 12px">
      <h2>2. VaR（风险价值，1 日）</h2>
      <p class="sub">表示在该置信度下，单日亏损通常不超过这个水平（相对现价）。</p>
      <div class="table-wrap" style="margin-top: 8px">
        <table>
          <thead>
            <tr>
              <th>方法</th>
              <th>VaR %</th>
              <th>约合金额 / 股</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td>历史模拟</td>
              <td class="mono">{{ pctPoints(varBlock.historical?.var_pct) }}</td>
              <td class="mono">{{ px(data.var_dollar?.historical) }}</td>
            </tr>
            <tr>
              <td>参数法（正态）</td>
              <td class="mono">{{ pctPoints(varBlock.parametric?.var_pct) }}</td>
              <td class="mono">{{ px(data.var_dollar?.parametric) }}</td>
            </tr>
            <tr>
              <td>蒙特卡洛</td>
              <td class="mono">{{ pctPoints(varBlock.monte_carlo?.var_pct) }}</td>
              <td class="mono">{{ px(data.var_dollar?.monte_carlo) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <section class="panel" style="margin-top: 12px">
      <h2>3. CVaR / Expected Shortfall</h2>
      <p class="sub">{{ cvar.note }}</p>
      <div class="table-wrap" style="margin-top: 8px">
        <table>
          <thead>
            <tr>
              <th>方法</th>
              <th>CVaR %</th>
              <th>约合金额 / 股</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td>历史模拟</td>
              <td class="mono">{{ pctPoints(cvar.historical?.cvar_pct) }}</td>
              <td class="mono">{{ px(cvar.historical?.cvar_dollar) }}</td>
            </tr>
            <tr>
              <td>参数法</td>
              <td class="mono">{{ pctPoints(cvar.parametric?.cvar_pct) }}</td>
              <td class="mono">{{ px(cvar.parametric?.cvar_dollar) }}</td>
            </tr>
            <tr>
              <td>蒙特卡洛</td>
              <td class="mono">{{ pctPoints(cvar.monte_carlo?.cvar_pct) }}</td>
              <td class="mono">{{ px(cvar.monte_carlo?.cvar_dollar) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <section class="panel" style="margin-top: 12px">
      <h2>4. Beta / CAPM</h2>
      <div class="price-grid">
        <div class="price-card">
          <span>Beta（相对 {{ beta.benchmark || 'SPY' }}）</span>
          <b>{{ num(beta.beta, 2) }}{{ sig(beta.beta_sig) }}</b>
          <small class="sub">
            相关 {{ num(beta.corr, 2) }}
            <template v-if="beta.r_squared != null"> · R² {{ num(beta.r_squared, 2) }}</template>
            <template v-if="beta.beta_t != null"> · t={{ num(beta.beta_t, 2) }}</template>
          </small>
        </div>
        <div class="price-card">
          <span>Alpha（复利年化）</span>
          <b>{{ pct(beta.alpha_annual) }}{{ sig(beta.alpha_sig) }}</b>
          <small class="sub">
            日 α {{ pct(beta.alpha_daily, 4) }}
            <template v-if="beta.alpha_pvalue != null"> · p={{ num(beta.alpha_pvalue, 3) }}</template>
          </small>
        </div>
        <div class="price-card">
          <span>行业暴露</span>
          <b>{{ num(industry.beta, 2) }}</b>
          <small class="sub">
            {{ industry.sector || '—' }}
            <template v-if="industry.etf"> · vs {{ industry.etf }}</template>
            <template v-if="industry.corr != null"> · 相关 {{ num(industry.corr, 2) }}</template>
          </small>
        </div>
      </div>
      <p class="sub" style="margin-top: 8px">
        {{ beta.note || 'α 年化为复利 (1+日α)^252−1' }}
        {{ industry.note || '' }}
        · *** p&lt;0.01 · ** p&lt;0.05 · * p&lt;0.10
      </p>
    </section>

    <section class="panel" style="margin-top: 12px">
      <h2>5. 因子暴露与回归分析</h2>
      <p class="sub">
        独立于概率展望：回答「收益对哪些因素敏感」，不是「未来一定涨跌」。
        SPY/QQQ/SOXX 可能共线，系数请结合显著性与滚动 Beta 解读。
      </p>
      <p class="sub" v-if="factorLoading">正在拉因子序列并做 OLS / 滚动回归…</p>
      <div class="error" v-else-if="factorError">{{ factorError }}</div>
      <template v-else-if="factorData">
        <p class="sub">{{ factorData.narrative }}</p>
        <p class="sub">{{ factorData.disclaimer }}</p>
        <div class="price-grid" style="margin-top: 8px">
          <div class="price-card">
            <span>α 复利年化</span>
            <b>{{ pct(ols.alpha_annual) }}{{ sig(ols.alpha_sig) }}</b>
            <small class="sub">R² {{ num(ols.r2, 2) }} · N={{ ols.n }}</small>
          </div>
          <div class="price-card" v-for="row in (ols.ranked || []).slice(0, 3)" :key="row.key">
            <span>{{ row.label }}</span>
            <b>{{ num(row.beta, 2) }}{{ sig(row.sig) }}</b>
            <small class="sub">p={{ num(row.p_value, 3) }} · t={{ num(row.t_stat, 2) }}</small>
          </div>
        </div>

        <div class="table-wrap" style="margin-top: 12px">
          <table>
            <thead>
              <tr>
                <th>因子</th>
                <th>β</th>
                <th>显著性</th>
                <th>t</th>
                <th>p</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in ols.loadings || []" :key="row.key">
                <td>{{ row.label }}</td>
                <td class="mono">{{ num(row.beta, 3) }}{{ sig(row.sig) }}</td>
                <td>{{ row.sig_level || '—' }}</td>
                <td class="mono">{{ num(row.t_stat, 2) }}</td>
                <td class="mono">{{ num(row.p_value, 3) }}</td>
              </tr>
            </tbody>
          </table>
        </div>

        <h3 style="margin: 14px 0 6px; font-size: 14px">情景分析（条件期望，非预测）</h3>
        <p class="sub">输入冲击：权益/VIX/DXY/商品为 %；美债10Y 为百分点变化（如 0.20 = +20bp）。</p>
        <div class="range-row" style="margin-top: 8px; flex-wrap: wrap; gap: 6px">
          <label v-for="(val, key) in shocks" :key="key" class="sub" style="display: flex; gap: 4px; align-items: center">
            {{ key }}
            <input v-model="shocks[key]" style="width: 64px" />
          </label>
          <button class="btn" type="button" :disabled="factorLoading" @click="runFactors(symbol)">重算情景</button>
        </div>
        <div class="price-grid" style="margin-top: 8px">
          <div class="price-card">
            <span>情景期望日收益</span>
            <b>{{ pctPoints(scenario.expected_pct) }}</b>
            <small class="sub">{{ scenario.note }}</small>
          </div>
        </div>
        <div class="table-wrap" style="margin-top: 8px">
          <table>
            <thead>
              <tr><th>因子</th><th>β</th><th>冲击</th><th>贡献</th></tr>
            </thead>
            <tbody>
              <tr v-for="c in scenario.contributions || []" :key="c.key">
                <td>{{ c.label }}</td>
                <td class="mono">{{ num(c.beta, 3) }}</td>
                <td class="mono">{{ num(c.shock, 4) }}</td>
                <td class="mono">{{ pct(c.contribution, 3) }}</td>
              </tr>
            </tbody>
          </table>
        </div>

        <h3 style="margin: 14px 0 6px; font-size: 14px">滚动回归 Beta（{{ rolling.roll_window }} 日）</h3>
        <p class="sub">{{ rolling.note }}</p>
        <p class="sub" v-if="factorData.rolling_insight">{{ factorData.rolling_insight }}</p>
        <RollingBetaChart
          v-if="(rolling.points || []).length"
          :points="rolling.points"
          :factors="factorData.factors_used || []"
          :highlight="rollHighlight"
        />
        <div class="table-wrap" style="margin-top: 8px">
          <table>
            <thead>
              <tr>
                <th>日期</th>
                <th>R²</th>
                <th v-for="k in factorData.factors_used || []" :key="k">{{ k }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in (rolling.points || []).slice(-12)" :key="row.date">
                <td>{{ row.date }}</td>
                <td class="mono">{{ num(row.r2, 2) }}</td>
                <td v-for="k in factorData.factors_used || []" :key="k" class="mono">{{ num(row[k], 2) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <p class="sub" style="margin-top: 8px">
          {{ factorData.note }}
          <span v-if="Object.keys(factorData.factors_dropped || {}).length">
            · 拉数失败：{{ Object.keys(factorData.factors_dropped).join(', ') }}
          </span>
        </p>
      </template>
      <p class="sub" v-else-if="data && !factorLoading">测算风险后将自动估计因子暴露。</p>
    </section>

    <section class="panel" style="margin-top: 12px">
      <h2>6. Fama-French 因子</h2>
      <div class="table-wrap" style="margin-top: 8px">
        <table>
          <thead>
            <tr>
              <th>模型</th>
              <th>α 复利年化</th>
              <th>Mkt-RF</th>
              <th>SMB</th>
              <th>HML</th>
              <th>RMW</th>
              <th>CMA</th>
              <th>R²</th>
              <th>N</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td>FF3</td>
              <td class="mono">
                {{ pct(factors.ff3?.betas?.alpha_annual) }}{{ sig(factors.ff3?.betas?.alpha_sig) }}
              </td>
              <td class="mono" :title="ffTip(factors.ff3, 'Mkt-RF')">
                {{ num(factors.ff3?.betas?.mkt_rf, 2) }}{{ sig(factors.ff3?.loadings?.['Mkt-RF']?.sig) }}
              </td>
              <td class="mono" :title="ffTip(factors.ff3, 'SMB')">
                {{ num(factors.ff3?.betas?.smb, 2) }}{{ sig(factors.ff3?.loadings?.SMB?.sig) }}
              </td>
              <td class="mono" :title="ffTip(factors.ff3, 'HML')">
                {{ num(factors.ff3?.betas?.hml, 2) }}{{ sig(factors.ff3?.loadings?.HML?.sig) }}
              </td>
              <td class="mono">—</td>
              <td class="mono">—</td>
              <td class="mono">{{ num(factors.ff3?.r2, 2) }}</td>
              <td class="mono">{{ factors.ff3?.n ?? '—' }}</td>
            </tr>
            <tr>
              <td>FF5</td>
              <td class="mono">
                {{ pct(factors.ff5?.betas?.alpha_annual) }}{{ sig(factors.ff5?.betas?.alpha_sig) }}
              </td>
              <td class="mono" :title="ffTip(factors.ff5, 'Mkt-RF')">
                {{ num(factors.ff5?.betas?.mkt_rf, 2) }}{{ sig(factors.ff5?.loadings?.['Mkt-RF']?.sig) }}
              </td>
              <td class="mono" :title="ffTip(factors.ff5, 'SMB')">
                {{ num(factors.ff5?.betas?.smb, 2) }}{{ sig(factors.ff5?.loadings?.SMB?.sig) }}
              </td>
              <td class="mono" :title="ffTip(factors.ff5, 'HML')">
                {{ num(factors.ff5?.betas?.hml, 2) }}{{ sig(factors.ff5?.loadings?.HML?.sig) }}
              </td>
              <td class="mono" :title="ffTip(factors.ff5, 'RMW')">
                {{ num(factors.ff5?.betas?.rmw, 2) }}{{ sig(factors.ff5?.loadings?.RMW?.sig) }}
              </td>
              <td class="mono" :title="ffTip(factors.ff5, 'CMA')">
                {{ num(factors.ff5?.betas?.cma, 2) }}{{ sig(factors.ff5?.loadings?.CMA?.sig) }}
              </td>
              <td class="mono">{{ num(factors.ff5?.r2, 2) }}</td>
              <td class="mono">{{ factors.ff5?.n ?? '—' }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <p class="sub" style="margin-top: 8px">
        {{ factors.ff5?.note || factors.ff3?.note || '' }}
        · 悬停因子看 t / p
      </p>
    </section>

    <section class="panel" style="margin-top: 12px">
      <h2>7. 极值理论（EVT-GPD）</h2>
      <div class="price-grid">
        <div class="price-card">
          <span>GPD VaR</span>
          <b>{{ pctPoints(evt.var_pct) }}</b>
          <small class="sub">超额样本 {{ evt.exceedances ?? '—' }}</small>
        </div>
        <div class="price-card">
          <span>GPD CVaR</span>
          <b>{{ pctPoints(evt.cvar_pct) }}</b>
          <small class="sub">{{ evt.method }}</small>
        </div>
        <div class="price-card">
          <span>形状 ξ / 尺度 β</span>
          <b>{{ num(evt.xi, 3) }}</b>
          <small class="sub">β={{ num(evt.beta, 4) }} · u={{ pctPoints(evt.threshold != null ? evt.threshold * 100 : null, 2) }}</small>
        </div>
      </div>
      <p class="sub" style="margin-top: 8px">{{ evt.note }}</p>
    </section>

    <section class="panel" style="margin-top: 12px">
      <h2>8. 流动性 / 价差</h2>
      <div class="price-grid">
        <div class="price-card">
          <span>Amihud 非流动性</span>
          <b>{{ liq.amihud != null ? num(liq.amihud, 4) : '—' }}</b>
          <small class="sub">{{ liq.amihud_note || '×1e9' }}</small>
        </div>
        <div class="price-card">
          <span>Corwin-Schultz 价差</span>
          <b>{{ pctPoints(liq.corwin_schultz_pct) }}</b>
          <small class="sub">{{ liq.corwin_schultz_note || '高低价估计' }}</small>
        </div>
        <div class="price-card">
          <span>均量</span>
          <b>{{ liq.avg_volume != null ? num(liq.avg_volume, 0) : '—' }}</b>
          <small class="sub">CV {{ num(liq.volume_cv, 2) }}</small>
        </div>
      </div>
      <p class="sub" style="margin-top: 8px">{{ liq.note }}</p>
    </section>

    <section class="panel" style="margin-top: 12px">
      <h2>9. 其他辅助指标</h2>
      <div class="price-grid">
        <div class="price-card">
          <span>最大回撤</span>
          <b>{{ pctPoints(dd.max_drawdown_pct) }}</b>
        </div>
        <div class="price-card">
          <span>夏普比率</span>
          <b>{{ num(ratios.sharpe, 2) }}</b>
          <small class="sub">年化收益 {{ pct(ratios.ann_return) }}</small>
        </div>
        <div class="price-card">
          <span>Sortino</span>
          <b>{{ num(ratios.sortino, 2) }}</b>
          <small class="sub">只惩罚下行波动</small>
        </div>
      </div>
    </section>
  </template>
</template>
