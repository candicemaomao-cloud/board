<script setup>
import MarketPulse from './MarketPulse.vue'
import NewsPushPanel from './NewsPushPanel.vue'

defineProps({
  market: { type: Object, default: null },
  loading: { type: Boolean, default: false },
})
</script>

<template>
  <MarketPulse :market="market" :loading="loading" />
  <NewsPushPanel scope="stock" />
</template>
