<script setup>
import { computed, ref } from 'vue'
import { useChart } from '../useChart'

const props = defineProps({
  points: { type: Array, default: () => [] },
  factors: { type: Array, default: () => [] },
  highlight: { type: Array, default: () => [] },
})

const COLORS = ['#5b8def', '#3ecf8e', '#f0b429', '#e85d75', '#a78bfa', '#38bdf8', '#fb923c', '#94a3b8']

const el = ref(null)
const seriesKeys = computed(() => {
  const pref = props.highlight?.length ? props.highlight : props.factors
  const keys = pref.filter((k) => props.factors.includes(k))
  return (keys.length ? keys : props.factors).slice(0, 5)
})

useChart(el, () => ({ points: props.points, keys: seriesKeys.value }), ({ points, keys }) => {
  const dates = (points || []).map((p) => p.date)
  return {
    backgroundColor: 'transparent',
    tooltip: {
      trigger: 'axis',
      backgroundColor: '#101820',
      borderColor: 'rgba(148,176,210,0.2)',
      textStyle: { color: '#e9f0f7' },
    },
    legend: {
      top: 0,
      textStyle: { color: '#8b9bb0' },
      data: keys,
    },
    grid: { left: 48, right: 16, top: 36, bottom: 28 },
    xAxis: {
      type: 'category',
      data: dates,
      axisLabel: { color: '#8b9bb0', fontSize: 10 },
      axisLine: { lineStyle: { color: 'rgba(148,176,210,0.2)' } },
    },
    yAxis: {
      type: 'value',
      scale: true,
      name: 'β',
      nameTextStyle: { color: '#8b9bb0' },
      splitLine: { lineStyle: { color: 'rgba(148,176,210,0.08)' } },
      axisLabel: { color: '#8b9bb0' },
    },
    series: keys.map((k, i) => ({
      name: k,
      type: 'line',
      showSymbol: false,
      smooth: true,
      lineStyle: { width: 2 },
      itemStyle: { color: COLORS[i % COLORS.length] },
      data: (points || []).map((p) => p[k] ?? null),
    })),
  }
})
</script>

<template>
  <div ref="el" style="width: 100%; height: 280px" />
</template>
