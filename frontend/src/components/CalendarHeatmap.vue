<script setup>
import { computed, ref } from 'vue'
import { useChart } from '../useChart'

const props = defineProps({
  calendar: { type: Array, default: () => [] },
})

const el = ref(null)
const range = computed(() => {
  if (!props.calendar.length) return isoYear(new Date())
  return [props.calendar[0].date, props.calendar[props.calendar.length - 1].date]
})

function isoYear(d) {
  return String(d.getFullYear())
}

useChart(el, () => props.calendar, (calendar) => {
  const values = calendar.map((d) => d.pnl)
  const maxAbs = Math.max(200, ...values.map((v) => Math.abs(v)))
  return {
    backgroundColor: 'transparent',
    tooltip: {
      formatter: (p) => `${p.data[0]}<br/>${p.data[1] >= 0 ? '+' : ''}${p.data[1].toFixed(2)}`,
      backgroundColor: '#101820',
      borderColor: 'rgba(148,176,210,0.2)',
      textStyle: { color: '#e9f0f7' },
    },
    visualMap: {
      min: -maxAbs,
      max: maxAbs,
      orient: 'horizontal',
      left: 'center',
      bottom: 0,
      textStyle: { color: '#8b9bb0' },
      inRange: { color: ['#ff6b7a', '#1a2330', '#3ee0a0'] },
    },
    calendar: {
      top: 24,
      left: 36,
      right: 12,
      bottom: 48,
      range: range.value,
      cellSize: ['auto', 16],
      itemStyle: { borderWidth: 2, borderColor: '#141b25', color: '#1a2330' },
      splitLine: { lineStyle: { color: 'rgba(148,176,210,0.15)' } },
      dayLabel: { color: '#8b9bb0', nameMap: 'ZH' },
      monthLabel: { color: '#8b9bb0', nameMap: 'ZH' },
      yearLabel: { color: '#8b9bb0' },
    },
    series: [
      {
        type: 'heatmap',
        coordinateSystem: 'calendar',
        data: calendar.map((d) => [d.date, d.pnl]),
      },
    ],
  }
})
</script>

<template>
  <div ref="el" class="chart chart-cal"></div>
</template>
