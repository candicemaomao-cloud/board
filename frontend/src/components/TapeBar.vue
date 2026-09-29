<script setup>
import { pct, tone } from '../format'

defineProps({
  tape: { type: Object, default: null },
})

function priceText(row) {
  if (row.price == null) return '—'
  if (row.kind === 'yield') return `${row.price.toFixed(3)}%`
  return row.price.toLocaleString('en-US', { maximumFractionDigits: 2 })
}
</script>

<template>
  <section class="tape" v-if="tape?.indices?.length">
    <article v-for="row in tape.indices" :key="row.symbol" class="tape-item">
      <div class="tape-name">{{ row.name }} <span>{{ row.symbol.replace('^', '') }}</span></div>
      <div class="tape-px">{{ priceText(row) }}</div>
      <div class="tape-chg">
        <b :class="tone(row.day_pct)">日 {{ pct(row.day_pct) }}</b>
        <b :class="tone(row.week_pct)">周 {{ pct(row.week_pct) }}</b>
      </div>
    </article>
  </section>
</template>
