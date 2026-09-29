<script setup>
import { ref } from 'vue'
import { useChart } from '../useChart'

const props = defineProps({
  tags: { type: Array, default: () => [] },
})

const el = ref(null)
useChart(el, () => props.tags, (tags) => {
  const rows = [...tags].slice(0, 8).reverse()
  return {
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'axis',
      backgroundColor: '#101820',
      borderColor: 'rgba(148,176,210,0.2)',
      textStyle: { color: '#e9f0f7' },
    },
    grid: { left: 88, right: 16, top: 16, bottom: 24 },
    xAxis: {
      type: 'value',
      splitLine: { lineStyle: { color: 'rgba(148,176,210,0.08)' } },
      axisLabel: { color: '#8b9bb0' },
    },
    yAxis: {
      type: 'category',
      data: rows.map((d) => d.name),
      axisLabel: { color: '#d5e2ef' },
    },
    series: [
      {
        type: 'bar',
        data: rows.map((d) => ({
          value: d.total_pnl,
          itemStyle: { color: d.total_pnl >= 0 ? '#3ee0a0' : '#ff6b7a', borderRadius: [0, 6, 6, 0] },
        })),
      },
    ],
  }
})
</script>

<template>
  <div ref="el" class="chart chart-sm"></div>
</template>
