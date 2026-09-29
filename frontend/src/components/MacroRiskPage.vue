<script setup>
import { computed, onMounted, ref } from 'vue'
import { api } from '../api'

const loading = ref(false)
const error = ref('')
const data = ref(null)
const asOf = ref('')

function ym() {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`
}

async function load(force = false) {
  loading.value = true
  error.value = ''
  try {
    data.value = await api.fedRisk({
      force: force ? true : undefined,
      as_of: asOf.value || undefined,
    })
  } catch (e) {
    data.value = null
    error.value = e.message || '加载失败'
  } finally {
    loading.value = false
  }
}

function onMonthChange() {
  load(false)
}

function resetLatest() {
  asOf.value = ''
  load(false)
}

function pct(n, digits = 0) {
  if (n == null || Number.isNaN(Number(n))) return '—'
  return Number(n).toFixed(digits)
}

function toneRisk(n) {
  const x = Number(n)
  if (x >= 80) return 'down'
  if (x >= 60) return 'warn'
  if (x >= 40) return ''
  return 'up'
}

function toneDir(n) {
  const x = Number(n)
  if (x <= -20) return 'down'
  if (x >= 20) return 'up'
  return 'warn'
}

function riskDot(n) {
  const x = Number(n)
  if (x >= 80) return '🔴'
  if (x >= 70) return '🟠'
  if (x >= 40) return '🟡'
  return '🟢'
}

function sourceHot(n) {
  return Number(n) >= 70
}

function probPct(n) {
  if (n == null || Number.isNaN(Number(n))) return '—'
  return `${Number(n).toFixed(0)}%`
}

function toneCall(call) {
  const t = String(call || '')
  if (t.includes('加息')) return 'down'
  if (t.includes('降息')) return 'up'
  return 'warn'
}

function barW(n) {
  const x = Number(n)
  if (!Number.isFinite(x)) return '0%'
  return `${Math.max(0, Math.min(100, x))}%`
}

const jpyCarry = computed(() => {
  const fx = (data.value?.modules || []).find((m) => m.id === 'fx')
  return (fx?.children || []).find((c) => c.id === 'jpy_carry') || null
})

const rateCall = computed(() => data.value?.rate_call || null)
const fsi = computed(() => data.value?.financial_stress || null)

const PILLAR_ALERT_SPEC = {
  inflation: {
    hot: 70,
    red: 80,
    labelHot: '通胀偏热',
    labelRed: '通胀过热',
    detail: (s) => `通胀支柱 ${s}。粘性通胀会抬高中性利率，对久期和成长股估值不利。`,
    dominant: (s) => `当前宏观风险主要来自通胀（${s}），其他支柱相对不热。`,
  },
  growth: {
    hot: 70,
    red: 80,
    labelHot: '就业/经济转弱',
    labelRed: '衰退压力',
    detail: (s) => `就业/经济支柱 ${s}。NFP 要和失业率、时薪一起看，不是单独利空。`,
    dominant: (s) => `当前宏观风险主要来自就业/经济（${s}），其他支柱相对不热。`,
  },
  usd: {
    hot: 70,
    red: 85,
    labelHot: '美元过强',
    labelRed: '美元流动性收紧',
    detail: (s) => `美元/流动性支柱 ${s}。强美元抬高海外融资成本，风险资产承压。`,
    dominant: (s) => `当前宏观风险主要来自美元（${s}），其他支柱相对不热。`,
  },
  jpy_carry: {
    hot: 70,
    red: 80,
    labelHot: '日元 Carry 升温',
    labelRed: '日元套利平仓风险',
    detail: (s) => `日元 Carry ${s}。日元升值本身不等于利空，需同时看到风险资产压力才按 Unwind 计价。`,
    dominant: (s) => `当前宏观风险主要来自日元套利交易（${s}）。`,
  },
  credit: {
    hot: 70,
    red: 80,
    labelHot: '信用压力',
    labelRed: '信用收缩',
    detail: (s) => `信用/流动性支柱 ${s}。利差走阔会收紧企业再融资和银行风险偏好。`,
    dominant: (s) => `当前宏观风险主要来自信用（${s}），其他支柱相对不热。`,
  },
}

function buildRatesAlert(d) {
  if (!d) return null
  if (d.rates_alert) return d.rates_alert
  const sources = d.sources || []
  const tsy = sources.find((s) => s.id === 'treasury')
  const rest = sources.filter((s) => s.id !== 'treasury')
  const second = Math.max(0, ...rest.map((s) => Number(s.score) || 0))
  const tsyMod = (d.modules || []).find((m) => m.id === 'treasury')
  const kids = tsyMod?.children || []
  const realV = kids.find((c) => c.id === 'real10')?.detail?.actual
  const n10 = kids.find((c) => c.id === 'ust10')?.detail?.actual
  const tsyScore = Number(tsy?.score)
  const highReal = realV != null && Number(realV) >= 2
  const dominant = tsy && tsyScore >= 55 && tsyScore - second >= 12
  if (!(tsy && (highReal || dominant || tsyScore >= 60))) return null
  const bits = []
  if (n10 != null) bits.push(`10Y ${Number(n10).toFixed(2)}%`)
  if (realV != null) bits.push(`实际利率 ${Number(realV).toFixed(2)}%`)
  return {
    level: 'orange',
    key: highReal ? 'high_real_yield' : 'high_yield',
    emoji: '🟠',
    label: highReal ? '美债实际利率过高' : '美债利率过高',
    detail:
      (bits.length ? `${bits.join('、')}。` : '') +
      `当前宏观风险几乎全部来自美债/利率（${Math.round(tsyScore)}），其他支柱未过热。` +
      '成长股估值对实际利率更敏感；高利率本身不等于次贷危机。',
  }
}

function buildPillarAlerts(d) {
  if (!d) return []
  const sources = d.sources || []
  const ranked = [...sources].sort((a, b) => Number(b.score) - Number(a.score))
  const top = ranked[0]
  const second = Number(ranked[1]?.score) || 0
  const out = []
  for (const s of sources) {
    const spec = PILLAR_ALERT_SPEC[s.id]
    if (!spec) continue
    if (s.id === 'jpy_carry' && d.jpy_alert) continue
    const score = Number(s.score)
    if (!Number.isFinite(score)) continue
    const dominant = top?.id === s.id && score >= 55 && score - second >= 12
    const hot = score >= spec.hot
    if (!hot && !dominant) continue
    const red = score >= spec.red
    out.push({
      level: red ? 'red' : 'orange',
      key: `pillar_${s.id}`,
      emoji: red ? '🔴' : '🟠',
      label: red ? spec.labelRed : spec.labelHot,
      detail: (dominant ? spec.dominant : spec.detail)(Math.round(score)),
    })
  }
  return out
}

const ratesAlert = computed(() => buildRatesAlert(data.value))

const alerts = computed(() => {
  const d = data.value
  if (!d) return []
  const out = d.alerts?.length ? [...d.alerts] : []
  if (!out.length) {
    if (ratesAlert.value) out.push(ratesAlert.value)
    if (d.jpy_alert) out.push(d.jpy_alert)
  }
  const have = new Set(out.map((a) => a.key))
  for (const a of buildPillarAlerts(d)) {
    if (!have.has(a.key)) out.push(a)
  }
  const fs = d.financial_stress
  if (fs?.regime === 'financial_crisis' || fs?.regime === 'credit_contraction') {
    if (!have.has(fs.regime)) {
      out.push({
        level: fs.regime === 'financial_crisis' ? 'red' : 'orange',
        key: fs.regime,
        emoji: fs.emoji,
        label: fs.call,
        detail: fs.answer,
      })
    }
  }
  const hot = d.resonance?.hot || []
  if (hot.length >= 2 && !have.has('resonance')) {
    out.push({
      level: 'orange',
      key: 'resonance',
      emoji: '🟠',
      label: '跨模块共振',
      detail: `${hot.join('、')}同时偏热，共振 +${d.resonance?.penalty || 0}。`,
    })
  }
  return out
})

const drivers = computed(() => {
  const raw = data.value?.drivers || []
  const a = ratesAlert.value
  if (!a) return raw
  if (raw.some((x) => x.includes('实际利率') || x.includes('Treasury') || x.includes('美债'))) {
    return raw
  }
  return [`${a.label}：${a.detail}`, ...raw]
})

onMounted(() => load())
</script>

<template>
  <div>
    <p class="goal-lead">
      Macro Risk 是风险环境，不是买卖信号。日元升值本身不等于利空；只有与风险资产压力同时出现，才计为 Carry Unwind。
    </p>

    <div class="range-row" style="margin-bottom: 12px; gap: 8px; flex-wrap: wrap; align-items: flex-end">
      <label class="macro-dates">
        <span>回测月份</span>
        <input
          v-model="asOf"
          type="month"
          min="2018-01"
          :max="ym()"
          :disabled="loading"
          @change="onMonthChange"
        />
      </label>
      <button class="btn" type="button" :disabled="loading || !asOf" @click="resetLatest">
        回到最新
      </button>
      <button class="btn btn-primary" type="button" :disabled="loading" @click="load(true)">
        {{ loading ? '计算中…' : '刷新指数' }}
      </button>
      <div class="sub" v-if="data?.updated_at">
        {{ data.as_of_label || '最新' }}
        · 更新 {{ data.updated_at }}
      </div>
    </div>
    <p class="sub" style="margin: -4px 0 12px">
      默认最新。选某个月会用该月末及之前的数据重算同一套逻辑（按数据所属期，不是公布日）。历史月份不混入当前 FOMC 市场定价。
    </p>
    <div class="error" v-if="error">{{ error }}</div>

    <template v-if="data">
      <section class="panel">
        <div class="range-row" style="gap: 28px; flex-wrap: wrap; align-items: flex-start">
          <div>
            <div class="sub">Macro Risk</div>
            <b class="mono" style="font-size: 36px" :class="toneRisk(data.macro_risk)">
              {{ pct(data.macro_risk, 0) }}
            </b>
            <span class="sub"> / 100</span>
            <div>
              {{ data.macro_risk_band?.emoji }} {{ data.macro_risk_band?.label }}
              · {{ data.macro_risk_band?.action }}
            </div>
            <div class="sub" v-if="data.resonance?.penalty">
              Base {{ pct(data.base_risk, 0) }} + 共振 {{ pct(data.resonance.penalty, 0) }}
            </div>
          </div>
          <div>
            <div class="sub">Macro Direction</div>
            <b class="mono" style="font-size: 36px" :class="toneDir(data.macro_direction?.score)">
              {{ pct(data.macro_direction?.score, 0) }}
            </b>
            <span class="sub"> / 100</span>
            <div>
              {{ data.macro_direction?.band?.emoji }} {{ data.macro_direction?.band?.label }}
            </div>
          </div>
          <div style="flex: 1; min-width: 220px">
            <div class="sub">市场状态</div>
            <b style="font-size: 18px">{{ data.regime_label }}</b>
            <p class="narrative" v-if="data.as_of_mode === 'month'">
              回测 {{ data.as_of_label }}（截止 {{ data.as_of_cutoff }}，按数据所属期切片）。
            </p>
            <p class="narrative">{{ data.narrative }}</p>
          </div>
        </div>
        <p class="sub" style="margin: 10px 0 0">
          Macro Direction 是对股票偏多还是偏空（−100～+100），不是风险高低。
          档位：≤−60 极度利空 · ≤−20 偏空 · −20～+20 中性 · ≥+20 偏多 · ≥+60 极度利好。不是买卖信号。
        </p>

        <div
          v-for="a in alerts"
          :key="a.key"
          class="jpy-alert"
          :class="a.level"
        >
          {{ a.emoji }}
          <b>{{ a.label }}</b>
          · {{ a.detail }}
        </div>

        <div class="source-grid">
          <div
            v-for="s in data.sources || []"
            :key="s.id"
            class="source-card"
            :class="{ hot: sourceHot(s.score) }"
          >
            <span>{{ s.zh || s.label }}</span>
            <b class="mono" :class="toneRisk(s.score)">
              {{ pct(s.score, 0) }} {{ riskDot(s.score) }}
            </b>
            <em>{{ s.label }}</em>
          </div>
        </div>
      </section>

      <section class="panel" style="margin-top: 12px" v-if="rateCall && !rateCall.skipped">
        <h2 style="margin: 0 0 4px; font-size: 15px">下次 FOMC · 降息 / 加息</h2>
        <div class="error" v-if="rateCall.error && !rateCall.ok">利率路径算失败：{{ rateCall.error }}</div>
        <template v-if="rateCall.ok">
        <p class="sub" style="margin: 0 0 12px">
          下次会议 {{ rateCall.meeting?.title || '下次议息' }}
          · 当前联邦基金 {{ pct(rateCall.current_rate, 2) }}%
          <span v-if="rateCall.target_lower != null">
            （目标区间 {{ pct(rateCall.target_lower, 2) }}–{{ pct(rateCall.target_upper, 2) }}%）
          </span>
        </p>
        <div class="rate-call-grid" :class="{ triple: rateCall.dots }">
          <div class="rate-col">
            <div class="sub">市场隐含 · 下次会议</div>
            <b :class="toneCall(rateCall.market?.call)" style="font-size: 22px">
              {{ rateCall.market?.call || '—' }}
            </b>
            <div class="sub">{{ rateCall.market?.source }} · 期望 {{ pct(rateCall.market?.expected_bp, 0) }}bp</div>
            <div class="prob-row"><span>降息</span><i><b :style="{ width: barW(rateCall.market?.p_cut) }"></b></i><em>{{ probPct(rateCall.market?.p_cut) }}</em></div>
            <div class="prob-row"><span>维持</span><i><b class="hold" :style="{ width: barW(rateCall.market?.p_hold) }"></b></i><em>{{ probPct(rateCall.market?.p_hold) }}</em></div>
            <div class="prob-row"><span>加息</span><i><b class="hike" :style="{ width: barW(rateCall.market?.p_hike) }"></b></i><em>{{ probPct(rateCall.market?.p_hike) }}</em></div>
            <div class="sub" v-if="rateCall.market?.note">{{ rateCall.market.note }}</div>
          </div>
          <div class="rate-col" v-if="rateCall.dots">
            <div class="sub">委员会点阵 · {{ rateCall.dots.year }}年底</div>
            <b :class="toneCall(rateCall.dots.call)" style="font-size: 22px">
              {{ rateCall.dots.call }}
            </b>
            <div class="sub">
              {{ rateCall.dots.label }}（7月议息无 SEP）· {{ rateCall.dots.n }} 人
              · 中位 {{ pct(rateCall.dots.median, 1) }}%
            </div>
            <div class="prob-row">
              <span>降息</span>
              <i><b :style="{ width: barW(rateCall.dots.p_cut) }"></b></i>
              <em>{{ rateCall.dots.n_cut }}/{{ rateCall.dots.n }}</em>
            </div>
            <div class="prob-row">
              <span>维持</span>
              <i><b class="hold" :style="{ width: barW(rateCall.dots.p_hold) }"></b></i>
              <em>{{ rateCall.dots.n_hold }}/{{ rateCall.dots.n }}</em>
            </div>
            <div class="prob-row">
              <span>加息</span>
              <i><b class="hike" :style="{ width: barW(rateCall.dots.p_hike) }"></b></i>
              <em>{{ rateCall.dots.n_hike }}/{{ rateCall.dots.n }}</em>
            </div>
            <div class="sub">
              相对当前 {{ pct(rateCall.dots.current_mid, 2) }}%：
              3.875 以上 {{ rateCall.dots.n_hike }} 人，3.625 {{ rateCall.dots.n_hold }} 人，3.375 {{ rateCall.dots.n_cut }} 人。
              {{ rateCall.dots.skipped }}。这是年底路径，不是对下次会议投票。
            </div>
          </div>
          <div class="rate-col">
            <div class="sub">模型（泰勒规则）</div>
            <b :class="toneCall(rateCall.model?.call)" style="font-size: 22px">
              {{ rateCall.model?.call || '—' }}
            </b>
            <div class="sub">
              i* {{ pct(rateCall.model?.i_star, 2) }}%
              vs 当前 {{ pct(rateCall.current_rate, 2) }}%
              · 政策缺口 {{ pct(rateCall.model?.gap_bp, 0) }}bp
              （下次会议按约 40% 计价）
            </div>
            <div class="prob-row"><span>降息</span><i><b :style="{ width: barW(rateCall.model?.p_cut) }"></b></i><em>{{ probPct(rateCall.model?.p_cut) }}</em></div>
            <div class="prob-row"><span>维持</span><i><b class="hold" :style="{ width: barW(rateCall.model?.p_hold) }"></b></i><em>{{ probPct(rateCall.model?.p_hold) }}</em></div>
            <div class="prob-row"><span>加息</span><i><b class="hike" :style="{ width: barW(rateCall.model?.p_hike) }"></b></i><em>{{ probPct(rateCall.model?.p_hike) }}</em></div>
            <div class="sub">
              π {{ rateCall.model?.pi_source }} {{ pct(rateCall.model?.pi, 2) }}%
              · 失业 {{ pct(rateCall.model?.unemployment, 2) }}%
              · r* {{ pct(rateCall.model?.r_star, 2) }}%
            </div>
          </div>
        </div>
        <div
          class="jpy-alert"
          :class="rateCall.divergence?.key === 'agree' ? 'agree' : 'orange'"
          style="margin-top: 12px"
        >
          {{ rateCall.divergence?.emoji }}
          <b>{{ rateCall.divergence?.label }}</b>
          · {{ rateCall.divergence?.detail }}
        </div>
        <p class="sub" style="margin: 8px 0 0">{{ rateCall.path_note }}</p>
        <p class="sub">{{ rateCall.note }}</p>
        </template>
      </section>

      <section class="panel" style="margin-top: 12px">
        <h2 style="margin: 0 0 4px; font-size: 15px">五大模块</h2>
        <p class="sub" style="margin: 0 0 10px">
          通胀 22% · 就业/经济 23% · 美债/利率 25% · 外汇/日元 20% · 信用 10%。
          日元在外汇模块中单独建立 Carry Unwind，而不是把 USD/JPY 当普通汇率。
        </p>
        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>模块 / 指标</th>
                <th>权重</th>
                <th>风险</th>
                <th>Actual</th>
                <th>Expected</th>
                <th>Previous</th>
                <th>Surprise Z</th>
                <th>Trend Z</th>
              </tr>
            </thead>
            <tbody>
              <template v-for="mod in data.modules || []" :key="mod.id">
                <tr>
                  <td>
                    <b>{{ mod.emoji }} {{ mod.label }}</b>
                    {{ riskDot(mod.risk) }}
                  </td>
                  <td class="mono">{{ pct((mod.weight || 0) * 100, 0) }}%</td>
                  <td class="mono" :class="toneRisk(mod.risk)">{{ pct(mod.risk, 0) }}</td>
                  <td class="sub" colspan="5">
                    <template v-if="mod.id === 'fx'">
                      DXY {{ data.market_snapshot?.dxy }}
                      · USDJPY {{ data.market_snapshot?.usdjpy }}
                      （5D {{ pct(data.market_snapshot?.jpy_5d, 2) }}%）
                      · VIX {{ data.market_snapshot?.vix }}
                    </template>
                    <template v-else-if="mod.id === 'growth'">
                      {{ data.growth_note }}
                    </template>
                    <template v-else-if="mod.id === 'treasury'">
                      10Y 权重最高：成长股估值对实际利率更敏感
                    </template>
                  </td>
                </tr>
                <tr v-for="child in mod.children || []" :key="mod.id + '-' + child.id">
                  <td class="sub" style="padding-left: 18px">
                    {{ child.detail?.series || child.label }}
                  </td>
                  <td class="mono sub">{{ pct((child.weight || 0) * 100, 0) }}%</td>
                  <td class="mono" :class="toneRisk(child.risk)">{{ pct(child.risk, 0) }}</td>
                  <td class="mono">
                    {{ pct(child.detail?.actual, 2) }}
                    <div class="sub" v-if="child.detail?.unit">{{ child.detail.unit }}</div>
                  </td>
                  <td class="mono">
                    {{ pct(child.detail?.expected, 2) }}
                    <div class="sub">{{ child.detail?.expected_source }}</div>
                  </td>
                  <td class="mono">{{ pct(child.detail?.previous, 2) }}</td>
                  <td class="mono">{{ pct(child.detail?.z_surprise, 2) }}</td>
                  <td class="mono">{{ pct(child.detail?.z_trend, 2) }}</td>
                </tr>
                <tr v-if="mod.id === 'fx' && jpyCarry">
                  <td class="sub" colspan="8" style="padding-left: 18px">
                    JPY Carry =
                    0.35×动量 {{ pct(jpyCarry.detail?.momentum_score, 0) }}
                    + 0.25×波动 {{ pct(jpyCarry.detail?.vol_score, 0) }}
                    + 0.20×利差 {{ pct(jpyCarry.detail?.spread_score, 0) }}
                    + 0.20×风险资产 {{ pct(jpyCarry.detail?.stress_score, 0) }}
                    <span v-if="jpyCarry.detail?.note"> · {{ jpyCarry.detail.note }}</span>
                  </td>
                </tr>
              </template>
            </tbody>
          </table>
        </div>
      </section>

      <section class="panel" style="margin-top: 12px" v-if="fsi?.ok">
        <h2 style="margin: 0 0 4px; font-size: 15px">美债会引发次贷危机吗 · Financial Stress</h2>
        <div class="range-row" style="gap: 28px; flex-wrap: wrap; align-items: flex-start; margin-top: 8px">
          <div>
            <div class="sub">FSI</div>
            <b class="mono" style="font-size: 36px" :class="toneRisk(fsi.score)">
              {{ pct(fsi.score, 0) }}
            </b>
            <span class="sub"> / 100</span>
            <div>{{ fsi.emoji }} {{ fsi.call }}</div>
          </div>
          <div style="flex: 1; min-width: 240px">
            <b>{{ fsi.answer }}</b>
            <p class="narrative">{{ fsi.headline }}</p>
            <p class="sub">{{ fsi.interaction_note }}</p>
          </div>
        </div>
        <div
          v-if="ratesAlert"
          class="jpy-alert"
          :class="ratesAlert.level"
          style="margin-top: 12px"
        >
          {{ ratesAlert.emoji }}
          <b>{{ ratesAlert.label }}</b>
          · {{ ratesAlert.detail }}
        </div>
        <div class="source-grid fsi-grid">
          <div
            v-for="s in fsi.pillars || []"
            :key="s.id"
            class="source-card"
            :class="{ hot: sourceHot(s.score) }"
          >
            <span>{{ s.zh }}</span>
            <b class="mono" :class="toneRisk(s.score)">
              {{ pct(s.score, 0) }} {{ riskDot(s.score) }}
            </b>
            <em>{{ s.label }}</em>
          </div>
        </div>
        <div class="table-wrap" style="margin-top: 12px">
          <table>
            <thead>
              <tr>
                <th>指标</th>
                <th>看什么</th>
                <th>现值</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td>10Y Treasury</td>
                <td class="sub">无风险长期利率</td>
                <td class="mono">{{ pct(fsi.rates?.ust10, 2) }}%</td>
              </tr>
              <tr>
                <td>30Y Mortgage</td>
                <td class="sub">房地产融资成本</td>
                <td class="mono">{{ pct(fsi.housing?.mortgage_30y, 2) }}%</td>
              </tr>
              <tr>
                <td>Mortgage − 10Y</td>
                <td class="sub">MBS/房贷利差代理</td>
                <td class="mono">{{ pct(fsi.housing?.mortgage_spread, 2) }} pp</td>
              </tr>
              <tr>
                <td>House Price 12M</td>
                <td class="sub">房价</td>
                <td class="mono">{{ pct(fsi.housing?.home_price_12m, 1) }}%</td>
              </tr>
              <tr>
                <td>Mortgage Delinquency</td>
                <td class="sub">房贷逾期率</td>
                <td class="mono">{{ pct(fsi.housing?.delinquency, 2) }}%</td>
              </tr>
              <tr>
                <td>Household Debt Service</td>
                <td class="sub">家庭偿债压力</td>
                <td class="mono">{{ pct(fsi.housing?.tdsp, 2) }}%</td>
              </tr>
              <tr>
                <td>CRE Delinquency</td>
                <td class="sub">商业地产风险</td>
                <td class="mono">{{ pct(fsi.credit?.cre_delinquency, 2) }}%</td>
              </tr>
              <tr>
                <td>HY Spread</td>
                <td class="sub">企业信用 / 再融资</td>
                <td class="mono">{{ pct(fsi.credit?.hy_oas, 2) }}%</td>
              </tr>
              <tr>
                <td>IG OAS</td>
                <td class="sub">投资级信用</td>
                <td class="mono">{{ pct(fsi.credit?.ig_oas, 2) }}%</td>
              </tr>
              <tr>
                <td>KRE</td>
                <td class="sub">区域银行（银行 CDS 代理）</td>
                <td class="mono">{{ pct(fsi.bank?.kre, 2) }} · 20D {{ pct(fsi.bank?.chg20, 1) }}%</td>
              </tr>
              <tr>
                <td>10Y &gt; 4% 持续</td>
                <td class="sub">高利率维持多久</td>
                <td class="mono">{{ pct(fsi.rates?.weeks_above, 0) }} 周 · 近一年 {{ pct(fsi.rates?.share_1y, 0) }}%</td>
              </tr>
            </tbody>
          </table>
        </div>
        <ol class="sub" style="margin: 12px 0 0; padding-left: 18px">
          <li v-for="(c, i) in fsi.chain || []" :key="i">{{ c }}</li>
        </ol>
        <p class="sub" style="margin-top: 8px">{{ fsi.note }}</p>
      </section>

      <section class="panel" style="margin-top: 12px" v-if="drivers.length">
        <h2 style="margin: 0 0 8px; font-size: 15px">主要风险来源</h2>
        <ol style="margin: 0; padding-left: 20px">
          <li v-for="(d, i) in drivers" :key="i">{{ d }}</li>
        </ol>
        <p class="sub" v-if="data.resonance?.hot?.length" style="margin: 8px 0 0">
          共振：{{ data.resonance.hot.join(' · ') }}（+{{ pct(data.resonance.penalty, 0) }}）
        </p>
      </section>

      <section class="panel" style="margin-top: 12px">
        <h2 style="margin: 0 0 8px; font-size: 15px">资产提示 · MC 情景</h2>
        <div class="range-row" style="gap: 20px; flex-wrap: wrap; margin-bottom: 10px">
          <div v-for="(v, k) in data.asset_hints || {}" :key="k">
            <div class="sub">对 {{ k }}</div>
            <b>{{ v }}</b>
          </div>
        </div>
        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>参数</th>
                <th>值</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td>vol_mult</td>
                <td class="mono">{{ data.mc_scenario?.vol_mult }}</td>
              </tr>
              <tr>
                <td>drift_shift_annual</td>
                <td class="mono">{{ data.mc_scenario?.drift_shift_annual }}</td>
              </tr>
              <tr>
                <td>left_tail_mult</td>
                <td class="mono">{{ data.mc_scenario?.left_tail_mult }}</td>
              </tr>
              <tr>
                <td>gap_down</td>
                <td class="mono">{{ data.mc_scenario?.gap_down }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <p class="sub" style="margin-top: 8px">{{ data.mc_scenario?.note }}</p>
        <p class="sub">{{ data.equity_risk?.note }} · 映射年化波动约 {{ pct((data.equity_risk?.implied_vol_annual || 0) * 100, 1) }}%</p>
      </section>

      <p class="sub" style="margin-top: 10px">{{ data.definition }}</p>
      <p class="sub">{{ data.note }}</p>
    </template>
  </div>
</template>
