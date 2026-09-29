import { nextTick, onMounted, onUnmounted, watch } from 'vue'
import * as echarts from 'echarts'

function read(source) {
  return typeof source === 'function' ? source() : source?.value
}

export function useChart(elRef, source, buildOption) {
  let chart

  const render = async () => {
    await nextTick()
    if (!elRef.value) return
    if (!chart) chart = echarts.init(elRef.value, null, { renderer: 'canvas' })
    chart.setOption(buildOption(read(source) || []), true)
  }

  const resize = () => chart?.resize()
  let observer

  onMounted(() => {
    render()
    window.addEventListener('resize', resize)
    if (typeof ResizeObserver !== 'undefined' && elRef.value) {
      observer = new ResizeObserver(resize)
      observer.observe(elRef.value)
    }
  })
  onUnmounted(() => {
    window.removeEventListener('resize', resize)
    observer?.disconnect()
    observer = null
    chart?.dispose()
    chart = null
  })
  watch(source, render, { deep: true })

  return { render }
}
