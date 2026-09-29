<script setup>
import { computed, ref, watch } from 'vue'
import { api } from '../api'
import { money, pct } from '../format'

const props = defineProps({ initialSymbol: { type: String, default: '' } })
const symbol = ref('TSLA')
const loading = ref(false)
const error = ref('')
const data = ref(null)
const tab = ref('quarter')

const company = computed(() => data.value?.company || {})
const valuation = computed(() => company.value?.valuation || {})
const notes = computed(() => data.value?.notes || [])
const surprise = computed(() => data.value?.surprise || [])
const sheet = computed(() => (tab.value === 'annual' ? data.value?.annual : data.value?.quarter) || null)
const riskFactors = computed(() => data.value?.risk_factors || null)
const riskExpanded = ref(false)
const RISK_PREVIEW_COUNT = 4

function compact(n) {
  if (n == null || Number.isNaN(Number(n))) return '—'
  const value = Number(n)
  const abs = Math.abs(value)
  const sign = value < 0 ? '-' : ''
  if (abs >= 1_000_000_000) return `${sign}$${(abs / 1_000_000_000).toFixed(abs >= 10_000_000_000 ? 1 : 2)}B`
  if (abs >= 1_000_000) return `${sign}$${(abs / 1_000_000).toFixed(abs >= 10_000_000 ? 0 : 1)}M`
  return money(value, { signed: false })
}

function px(n) {
  if (n == null || n === '') return '—'
  return money(n, { signed: false })
}

function ratio(n) {
  if (n == null) return '—'
  return `${Number(n).toFixed(1)}%`
}

function multiple(n) {
  if (n == null || Number.isNaN(Number(n))) return '—'
  return `${Number(n).toFixed(2)}x`
}

function surpriseText(n) {
  if (n == null) return '—'
  return pct(n, true)
}

function rowName(name) {
  return (
    {
      'Total Revenue': '营收',
      'Gross Profit': '毛利',
      'Operating Income': '经营利润',
      'Net Income': '净利润',
      'Cash and Cash Equivalents': '现金及等价物',
      'Total Current Assets': '流动资产',
      'Total Assets': '总资产',
      'Total Current Liabilities': '流动负债',
      'Long-Term Debt': '长期负债',
      'Total Liabilities': '总负债',
      'Total Equity': '股东权益',
      'Net Cash Flow-Operating': '经营现金流',
      'Capital Expenditures': '资本开支',
      'Free Cash Flow': '自由现金流',
      'Net Cash Flow': '现金净变动',
      'Current Ratio': '流动比率',
      'Gross Margin': '毛利率',
      'Operating Margin': '经营利润率',
      'Profit Margin': '净利率',
    }[name] || name
  )
}

function cell(row, idx) {
  const value = row?.values?.[idx]
  if (value == null) return '—'
  if (row.name === 'Current Ratio') return `${(Number(value) / 100).toFixed(2)}x`
  if (['Gross Margin', 'Operating Margin', 'Profit Margin'].includes(row.name)) {
    return ratio(value)
  }
  return compact(value)
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
    data.value = await api.fundamentals(code)
    tab.value = 'quarter'
    riskExpanded.value = false
  } catch (e) {
    data.value = null
    error.value = e.message
  } finally {
    loading.value = false
  }
}
watch(() => props.initialSymbol, (value) => {
  if (value) { symbol.value = value; run() }
}, { immediate: true })
</script>

<template>
  <p class="goal-lead">输入美股代码，看最近几年年报、最近几季季报，以及营收利润是在变好还是变差。</p>

  <div class="range-row">
    <div class="watch-edit" style="flex: 1; margin: 0">
      <input
        v-model="symbol"
        placeholder="TSLA / AAPL / NVDA"
        @keydown.enter.prevent="run"
      />
      <button class="btn btn-primary" type="button" :disabled="loading" @click="run">
        {{ loading ? '分析中…' : '分析' }}
      </button>
    </div>
  </div>
  <div class="error" v-if="error">{{ error }}</div>
  <p class="sub" v-else-if="loading">正在拉 Nasdaq 财报…</p>
  <p class="sub" v-else-if="!data">输入代码后点分析。</p>

  <template v-if="data">
    <section class="panel" style="margin-top: 12px">
      <h2>{{ company.symbol }} · {{ company.name }}</h2>
      <p class="sub">
        {{ company.exchange }}
        <template v-if="company.sector"> · {{ company.sector }}</template>
        <template v-if="company.industry"> · {{ company.industry }}</template>
        · {{ data.source }}
      </p>
      <div class="price-grid">
        <div class="price-card">
          <span>现价</span>
          <b>{{ px(company.price) }}</b>
        </div>
        <div class="price-card">
          <span>市值</span>
          <b>{{ compact(company.market_cap) }}</b>
        </div>
        <div class="price-card">
          <span>一年目标</span>
          <b>{{ px(company.target) }}</b>
        </div>
        <div class="price-card">
          <span>52 周</span>
          <b>{{ company.range_52w || '—' }}</b>
        </div>
      </div>
      <p class="sub" style="margin-top: 10px" v-if="company.about">{{ company.about }}</p>
    </section>

    <section class="panel" style="margin-top: 12px">
      <div class="fund-head">
        <div>
          <h2 style="margin: 0">估值与 TTM</h2>
          <p class="sub" style="margin: 7px 0 0">滚动十二个月（Trailing Twelve Months）及当前市场估值。</p>
        </div>
        <span class="sub">{{ valuation.as_of || 'Yahoo Finance · TTM/当前估值' }}</span>
      </div>
      <div class="price-grid ttm-grid">
        <div class="price-card"><span>PE（TTM）</span><b>{{ multiple(valuation.trailing_pe) }}</b></div>
        <div class="price-card"><span>预期 PE</span><b>{{ multiple(valuation.forward_pe) }}</b></div>
        <div class="price-card"><span>EPS（TTM）</span><b>{{ px(valuation.eps_ttm) }}</b></div>
        <div class="price-card"><span>营收（TTM）</span><b>{{ compact(valuation.revenue_ttm) }}</b></div>
        <div class="price-card"><span>自由现金流（TTM）</span><b>{{ compact(valuation.free_cash_flow_ttm) }}</b></div>
        <div class="price-card"><span>净利率（TTM）</span><b>{{ ratio(valuation.net_margin_ttm) }}</b></div>
      </div>
      <p v-if="!Object.keys(valuation).length" class="sub">当前数据源没有返回这只股票的估值或 TTM 指标。</p>
    </section>

    <section class="panel" style="margin-top: 12px" v-if="notes.length">
      <h2>要点</h2>
      <div class="fund-notes">
        <div
          v-for="item in notes"
          :key="item.title"
          class="fund-note"
          :class="item.tone"
        >
          <span>{{ item.title }}</span>
          <p>{{ item.text }}</p>
        </div>
      </div>
    </section>

    <section class="panel" style="margin-top: 12px">
      <div class="fund-head">
        <h2 style="margin: 0">报表</h2>
        <div class="pills">
          <button class="pill" type="button" :class="{ active: tab === 'quarter' }" @click="tab = 'quarter'">季报</button>
          <button class="pill" type="button" :class="{ active: tab === 'annual' }" @click="tab = 'annual'">年报</button>
        </div>
      </div>
      <p class="sub">金额已换成美元。Nasdaq 原表单位是千美元。</p>

      <h3 class="fund-h">利润表</h3>
      <div class="table-wrap" v-if="sheet?.income?.length">
        <table>
          <thead>
            <tr>
              <th>科目</th>
              <th v-for="p in sheet.periods" :key="p">{{ p }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in sheet.income" :key="row.name">
              <td>{{ rowName(row.name) }}</td>
              <td v-for="(_, i) in sheet.periods" :key="i" class="mono">{{ cell(row, i) }}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <h3 class="fund-h">资产负债表</h3>
      <div class="table-wrap" v-if="sheet?.balance?.length">
        <table>
          <thead>
            <tr>
              <th>科目</th>
              <th v-for="p in sheet.periods" :key="'b'+p">{{ p }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in sheet.balance" :key="row.name">
              <td>{{ rowName(row.name) }}</td>
              <td v-for="(_, i) in sheet.periods" :key="i" class="mono">{{ cell(row, i) }}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <h3 class="fund-h">现金流</h3>
      <p class="sub">自由现金流 = 经营现金流 + 资本开支（资本开支为负）。和上面的「现金及等价物」不是同一项：前者是当期流量，后者是期末余额。</p>
      <div class="table-wrap" v-if="sheet?.cash?.length">
        <table>
          <thead>
            <tr>
              <th>科目</th>
              <th v-for="p in sheet.periods" :key="'c'+p">{{ p }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in sheet.cash" :key="row.name">
              <td>{{ rowName(row.name) }}</td>
              <td v-for="(_, i) in sheet.periods" :key="i" class="mono">{{ cell(row, i) }}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <template v-if="tab === 'quarter' && sheet?.ratios?.length">
        <h3 class="fund-h">比率</h3>
        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>科目</th>
                <th v-for="p in sheet.periods" :key="'r'+p">{{ p }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in sheet.ratios" :key="row.name">
                <td>{{ rowName(row.name) }}</td>
                <td v-for="(_, i) in sheet.periods" :key="i" class="mono">{{ cell(row, i) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </template>
    </section>

    <section class="panel" style="margin-top: 12px" v-if="surprise.length">
      <h2>EPS 意外</h2>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>季度</th>
              <th>公布日</th>
              <th>实际 EPS</th>
              <th>预期</th>
              <th>意外</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in surprise" :key="row.period + row.reported">
              <td>{{ row.period }}</td>
              <td>{{ row.reported }}</td>
              <td class="mono">{{ row.eps ?? '—' }}</td>
              <td class="mono">{{ row.estimate ?? '—' }}</td>
              <td :class="row.surprise > 0 ? 'up' : row.surprise < 0 ? 'down' : ''">
                {{ surpriseText(row.surprise) }}
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>

    <section class="panel" style="margin-top: 12px" v-if="riskFactors">
      <h2>风险因素</h2>
      <p class="sub">
        来自最新 {{ riskFactors.form }}（{{ riskFactors.filed }} 提交）的 Item 1A 原文，公司自己列出的经营风险，不是我们的分析。
        <a :href="riskFactors.url" target="_blank" rel="noopener">查看原文</a>
      </p>
      <div class="fund-risk">
        <p
          v-for="(para, i) in (riskExpanded ? riskFactors.paragraphs : riskFactors.paragraphs.slice(0, RISK_PREVIEW_COUNT))"
          :key="i"
        >
          {{ para }}
        </p>
      </div>
      <button
        v-if="riskFactors.paragraphs.length > RISK_PREVIEW_COUNT"
        class="pill"
        type="button"
        @click="riskExpanded = !riskExpanded"
      >
        {{ riskExpanded ? '收起' : `展开全部（还有 ${riskFactors.paragraphs.length - RISK_PREVIEW_COUNT} 段）` }}
      </button>
      <p class="sub" v-if="riskFactors.truncated" style="margin-top: 8px">原文更长，这里只截取了前面一部分。</p>
    </section>
    <section class="panel" style="margin-top: 12px" v-else-if="data && company.symbol">
      <h2>风险因素</h2>
      <p class="sub">没拉到这家公司 SEC 10-K 里的风险因素章节（可能不直接向 SEC 报送，或者最新一期还没解析出来）。</p>
    </section>
  </template>
</template>

<style scoped>
.ttm-grid { grid-template-columns: repeat(auto-fit, minmax(min(100%, 180px), 1fr)); }
.ttm-grid b { overflow-wrap: anywhere; }
</style>
