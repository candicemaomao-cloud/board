<script setup>
import { reactive, watch } from 'vue'
import { compactUsd, money, pct, tone } from '../format'

const props = defineProps({
  goal: { type: Object, required: true },
})
const emit = defineEmits(['save'])

const form = reactive({
  total_amount: 0,
  current_pnl: 0,
  target_profit: 0,
  monthly_pct: 8,
})

watch(
  () => props.goal,
  (g) => {
    if (!g) return
    form.total_amount = g.total_amount ?? g.principal ?? 0
    form.current_pnl = g.starting_pnl ?? g.current_pnl
    form.target_profit = g.target_profit
    form.monthly_pct = Number((g.monthly_return * 100).toFixed(2))
  },
  { immediate: true },
)

function save() {
  emit('save', {
    total_amount: Number(form.total_amount),
    current_pnl: Number(form.current_pnl),
    target_profit: Number(form.target_profit),
    monthly_return: Number(form.monthly_pct) / 100,
  })
}
</script>

<template>
  <section class="panel goal-panel">
    <div class="goal-head">
      <div>
        <h2>
          <span class="freedom-mark">财富自由</span>
          距离 3,000 万美元还要多久
        </h2>
        <p class="goal-lead">
          总金额 {{ money(goal.total_amount ?? goal.principal, { signed: false }) }}，
          当前{{ goal.current_pnl >= 0 ? '盈利' : '亏损' }}
          {{ money(Math.abs(goal.current_pnl), { signed: false }) }}，
          可用资金 {{ money(goal.equity, { signed: false }) }}。
          每记一笔盈亏都会按最新资金重算：赚了天数变少，亏了天数变多。
        </p>
      </div>
      <div class="goal-eta">
        <div class="label">按月化 {{ (goal.monthly_return * 100).toFixed(1) }}% 还要</div>
        <div class="value">{{ goal.assumed.days != null ? goal.assumed.days.toLocaleString() + ' 天' : goal.assumed.label }}</div>
        <div class="sub">约 {{ goal.assumed.years ?? '—' }} 年 · 赚少亏多都会变</div>
      </div>
    </div>

    <div class="goal-progress">
      <div class="goal-progress-bar">
        <i :style="{ width: Math.max(goal.progress_pct, 0.4) + '%' }"></i>
      </div>
      <div class="goal-progress-meta">
        <span>现在 {{ compactUsd(goal.equity) }}</span>
        <span>还差 {{ compactUsd(goal.remaining) }}</span>
        <span>财富自由 {{ compactUsd(goal.target_equity) }}</span>
      </div>
    </div>

    <div class="goal-stats">
      <div class="stat">
        <span>总金额</span>
        <b>{{ money(goal.total_amount ?? goal.principal, { signed: false }) }}</b>
      </div>
      <div class="stat">
        <span>当前盈亏</span>
        <b :class="tone(goal.current_pnl)">{{ money(goal.current_pnl) }}</b>
      </div>
      <div class="stat">
        <span>财富自由目标</span>
        <b>{{ compactUsd(goal.target_profit) }}</b>
      </div>
    </div>

    <div class="goal-split">
      <div>
        <h3>不同收益率要多久</h3>
        <table class="goal-table">
          <thead>
            <tr>
              <th>假设收益率</th>
              <th>约等于月化</th>
              <th>还需时间</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in goal.scenarios" :key="row.key" :class="{ active: row.highlight }">
              <td>{{ row.name }}</td>
              <td>{{ row.monthly_pct.toFixed(2) }}%</td>
              <td>{{ row.reachable ? row.label : '无法到达' }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <div>
        <h3>想在这几年内赚到，需要多狠</h3>
        <table class="goal-table">
          <thead>
            <tr>
              <th>想要的年限</th>
              <th>需要月化</th>
              <th>约等于周化</th>
              <th>每周大约赚</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in goal.deadlines" :key="row.years">
              <td>{{ row.years }} 年</td>
              <td>{{ row.reachable ? pct(row.monthly_pct) : '—' }}</td>
              <td>{{ row.reachable ? pct(row.weekly_pct) : '—' }}</td>
              <td>{{ row.reachable ? money(row.weekly_pnl, { signed: false }) : '—' }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <div v-if="goal.history" class="goal-note">
      <div>
        <p class="sub">
          按近 {{ goal.history.sample_days }} 周实际节奏推算：
          {{ goal.history.label }}。
        </p>
      </div>
    </div>

    <div class="goal-edit">
      <h3>改总金额 / 目标 / 假设月化</h3>
      <div class="form-grid">
        <div class="field">
          <label>总金额 ($)</label>
          <input v-model="form.total_amount" type="number" step="0.01" />
        </div>
        <div class="field">
          <label>起始盈亏 $（亏填负数，之后以交易记录为准）</label>
          <input v-model="form.current_pnl" type="number" step="0.01" />
        </div>
        <div class="field">
          <label>目标盈利 ($)</label>
          <input v-model="form.target_profit" type="number" step="1" />
        </div>
        <div class="field">
          <label>假设月化收益率 %</label>
          <input v-model="form.monthly_pct" type="number" step="0.1" />
        </div>
        <div class="field" style="justify-content: flex-end">
          <label>&nbsp;</label>
          <button class="btn btn-primary" type="button" @click="save">更新推算</button>
        </div>
      </div>
    </div>
  </section>
</template>
