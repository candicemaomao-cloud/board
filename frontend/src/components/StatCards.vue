<script setup>
import { money, pct, tone } from '../format'

defineProps({
  cards: { type: Object, required: true },
})
</script>

<template>
  <section class="cards">
    <article class="card">
      <div class="label">账户总资产</div>
      <div class="value">{{ money(cards.total_equity, { signed: false }) }}</div>
      <div class="sub">起始 {{ money(cards.starting_equity, { signed: false }) }}</div>
    </article>
    <article class="card">
      <div class="label">本周盈亏</div>
      <div class="value" :class="tone(cards.daily_pnl)">{{ money(cards.daily_pnl) }}</div>
      <div class="sub">{{ cards.latest_date || '本周尚未记账' }} · {{ pct(cards.daily_return_pct) }}</div>
    </article>
    <article class="card">
      <div class="label">本月累计</div>
      <div class="value" :class="tone(cards.mtd_pnl)">{{ money(cards.mtd_pnl) }}</div>
      <div class="sub">
        本年 {{ money(cards.ytd_pnl) }}
        <span v-if="cards.pnl_source?.mtd === 'trades'"> · 来自交易日志</span>
      </div>
    </article>
    <article class="card">
      <div class="label">本年累计</div>
      <div class="value" :class="tone(cards.ytd_pnl)">{{ money(cards.ytd_pnl) }}</div>
      <div class="sub">区间 {{ money(cards.range_pnl) }}</div>
    </article>
    <article class="card">
      <div class="label">周胜率 / 盈亏比</div>
      <div class="value">{{ cards.win_rate.toFixed(1) }}%</div>
      <div class="sub">
        PF
        {{ cards.profit_factor == null ? '∞' : cards.profit_factor }}
        · 回撤 {{ money(cards.max_drawdown.amount, { signed: false }) }}
        ({{ cards.max_drawdown.pct }}%)
      </div>
    </article>
  </section>
</template>
