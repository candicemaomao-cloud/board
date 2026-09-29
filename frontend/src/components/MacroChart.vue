<script setup>
import { ref } from 'vue'
import { useChart } from '../useChart'

const props = defineProps({
  option: { type: Object, default: null },
  height: { type: String, default: '' },
})

const el = ref(null)
useChart(el, () => props.option, (option) => option || {})
</script>

<template>
  <div ref="el" class="chart chart-macro" :style="height ? { height } : {}"></div>
</template>
