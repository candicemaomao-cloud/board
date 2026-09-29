<script setup>
import { ref } from 'vue'
import { useChart } from '../useChart'

const props = defineProps({
  weekday: { type: Array, default: () => [] },
})

const el = ref(null)
useChart(el, () => props.weekday, (weekday) => {
  const days = weekday.filter((d) => d.weekday < 5)
  return {
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'axis',
      backgroundColor: '#101820',
      borderColor: 'rgba(148,176,210,0.2)',
      textStyle: { color: '#e9f0f7' },
    },
    grid: { left: 48, right: 12, top: 24, bottom: 28 },
    xAxis: {
      type: 'category',
      data: days.map((d) => d.name),
      axisLabel: { color: '#8b9bb0' },
      axisLine: { lineStyle: { color: 'rgba(148,176,210,0.2)' } },
    },
    yAxis: {
      type: 'value',
      splitLine: { lineStyle: { color: 'rgba(148,176,210,0.08)' } },
      axisLabel: { color: '#8b9bb0' },
    },
    series: [
      {
        type: 'bar',
        data: days.map((d) => ({
          value: d.total_pnl,
          itemStyle: { color: d.total_pnl >= 0 ? '#3ee0a0' : '#ff6b7a', borderRadius: [6, 6, 0, 0] },
        })),
      },
    ],
  }
})
</script>

<template>
  <div ref="el" class="chart chart-sm"></div>
</template>
