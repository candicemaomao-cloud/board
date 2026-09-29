<script setup>
import { ref } from 'vue'
import { useChart } from '../useChart'

const props = defineProps({
  curve: { type: Array, default: () => [] },
})

const el = ref(null)
useChart(el, () => props.curve, (curve) => ({
  backgroundColor: 'transparent',
  tooltip: {
    trigger: 'axis',
    backgroundColor: '#101820',
    borderColor: 'rgba(148,176,210,0.2)',
    textStyle: { color: '#e9f0f7' },
  },
  grid: { left: 52, right: 18, top: 28, bottom: 32 },
  xAxis: {
    type: 'category',
    data: curve.map((d) => d.date.slice(5)),
    axisLine: { lineStyle: { color: 'rgba(148,176,210,0.2)' } },
    axisLabel: { color: '#8b9bb0' },
  },
  yAxis: {
    type: 'value',
    splitLine: { lineStyle: { color: 'rgba(148,176,210,0.08)' } },
    axisLabel: { color: '#8b9bb0' },
  },
  series: [
    {
      name: '累计盈亏',
      type: 'line',
      smooth: true,
      showSymbol: false,
      data: curve.map((d) => d.cumulative_pnl),
      lineStyle: { width: 2.4, color: '#7aa2ff' },
      areaStyle: {
        color: {
          type: 'linear',
          x: 0, y: 0, x2: 0, y2: 1,
          colorStops: [
            { offset: 0, color: 'rgba(122,162,255,0.35)' },
            { offset: 1, color: 'rgba(122,162,255,0)' },
          ],
        },
      },
    },
  ],
}))
</script>

<template>
  <div ref="el" class="chart"></div>
</template>
