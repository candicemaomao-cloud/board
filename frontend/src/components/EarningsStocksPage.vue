<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from '../api'

const loading = ref(false)
const enriching = ref(false)
const adding = ref({})
const data = ref(null)
const search = ref('')
const sectorFilter = ref('')
const days = ref(30)

const items = computed(() => data.value?.items || [])
const sectors = computed(() => {
  const set = new Set()
  for (const row of items.value) {
    if (row.sector_zh && row.sector_zh !== '其他') set.add(row.sector_zh)
    else if (row.sector) set.add(row.sector)
  }
  return [...set].sort()
})

const filtered = computed(() => {
  const q = search.value.trim().toUpperCase()
  return items.value.filter((row) => {
    if (q) {
      const hay = `${row.symbol || ''} ${row.name || ''}`.toUpperCase()
      if (!hay.includes(q)) return false
    }
    if (sectorFilter.value) {
      const sec = row.sector_zh || row.sector || ''
      if (sec !== sectorFilter.value) return false
    }
    return true
  })
})

function money(n) {
  if (n == null || n === '') return '—'
  const v = Number(n)
  if (!Number.isFinite(v)) return '—'
  return `$${v.toFixed(2)}`
}

function pct(n) {
  if (n == null || n === '') return '—'
  const v = Number(n)
  if (!Number.isFinite(v)) return '—'
  const sign = v > 0 ? '+' : ''
  return `${sign}${v.toFixed(2)}%`
}

function tone(n) {
  const v = Number(n)
  if (!Number.isFinite(v) || v === 0) return ''
  return v > 0 ? 'up' : 'down'
}

function marketCap(n) {
  if (n == null || n === '') return '—'
  const v = Number(n)
  if (!Number.isFinite(v)) return '—'
  if (v >= 1e12) return `$${(v / 1e12).toFixed(2)}T`
  if (v >= 1e9) return `$${(v / 1e9).toFixed(2)}B`
  if (v >= 1e6) return `$${(v / 1e6).toFixed(2)}M`
  return `$${v.toLocaleString()}`
}

async function load() {
  loading.value = true
  enriching.value = false
  try {
    // 先出日历列表，再补价格/板块
    data.value = await api.earningsStocks({ days: days.value, enrich: false })
    loading.value = false
    enriching.value = true
    data.value = await api.earningsStocks({ days: days.value, enrich: true, max_enrich: 60 })
  } catch (e) {
    ElMessage.error(e.message || '加载财报股票失败')
    if (!data.value) data.value = null
  } finally {
    loading.value = false
    enriching.value = false
  }
}

async function addToList(row) {
  const sym = row.symbol
  if (!sym || adding.value[sym]) return
  if (row.in_list) {
    ElMessage.info(`${sym} 已在股票列表中`)
    return
  }
  adding.value = { ...adding.value, [sym]: true }
  try {
    await api.addEarningsStock(row)
    row.in_list = true
    ElMessage.success(`「${sym}」已加入股票列表`)
  } catch (e) {
    ElMessage.error(e.message || '加入失败')
  } finally {
    adding.value = { ...adding.value, [sym]: false }
  }
}

onMounted(load)
</script>

<template>
  <div class="page">
    <div class="toolbar">
      <div class="toolbar-left">
        <el-input
          v-model="search"
          clearable
          placeholder="搜索代码 / 公司名"
          style="width: 200px"
        />
        <el-select v-model="sectorFilter" clearable placeholder="公司类别" style="width: 140px">
          <el-option v-for="s in sectors" :key="s" :label="s" :value="s" />
        </el-select>
        <el-select v-model="days" style="width: 120px" @change="load">
          <el-option :value="7" label="未来 7 天" />
          <el-option :value="14" label="未来 14 天" />
          <el-option :value="30" label="未来 30 天" />
        </el-select>
      </div>
      <el-button :loading="loading || enriching" @click="load">刷新</el-button>
    </div>

    <p class="sub" v-if="data?.note">
      {{ data.note }}
      <span v-if="enriching"> · 正在补价格与类别…</span>
      <span v-if="filtered.length !== items.length"> · 当前显示 {{ filtered.length }} 只</span>
    </p>

    <el-table v-loading="loading" :data="filtered" stripe style="width: 100%" height="640">
      <el-table-column prop="symbol" label="代码" width="100" fixed>
        <template #default="{ row }">
          <span class="mono accent">{{ row.symbol }}</span>
        </template>
      </el-table-column>
      <el-table-column prop="name" label="公司" min-width="180" show-overflow-tooltip />
      <el-table-column label="价格" width="110" align="right">
        <template #default="{ row }">
          <span class="mono">{{ money(row.price) }}</span>
        </template>
      </el-table-column>
      <el-table-column label="涨跌" width="96" align="right">
        <template #default="{ row }">
          <span class="mono" :class="tone(row.change_pct)">{{ pct(row.change_pct) }}</span>
        </template>
      </el-table-column>
      <el-table-column label="公司类别" width="120">
        <template #default="{ row }">
          {{ row.sector_zh || row.sector || '—' }}
        </template>
      </el-table-column>
      <el-table-column label="行业" min-width="140" show-overflow-tooltip>
        <template #default="{ row }">
          {{ row.industry || '—' }}
        </template>
      </el-table-column>
      <el-table-column label="财报日" width="120">
        <template #default="{ row }">
          <span>{{ row.earnings_date || '—' }}</span>
          <span class="sub" v-if="row.days_until != null && row.days_until >= 0">
            · {{ row.days_until === 0 ? '今天' : `${row.days_until} 天后` }}
          </span>
        </template>
      </el-table-column>
      <el-table-column prop="time" label="时段" width="72" />
      <el-table-column label="市值" width="110" align="right">
        <template #default="{ row }">
          <span class="mono">{{ marketCap(row.market_cap) }}</span>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="130" fixed="right">
        <template #default="{ row }">
          <el-button
            size="small"
            type="primary"
            link
            :disabled="row.in_list"
            :loading="!!adding[row.symbol]"
            @click="addToList(row)"
          >
            {{ row.in_list ? '已在列表' : '加入股票列表' }}
          </el-button>
        </template>
      </el-table-column>
    </el-table>
  </div>
</template>

<style scoped>
.page {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.toolbar {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
}
.toolbar-left {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
}
.sub {
  margin: 0;
  color: #8b9bb0;
  font-size: 13px;
}
.mono {
  font-variant-numeric: tabular-nums;
}
.accent {
  color: #7aa2ff;
  font-weight: 600;
}
</style>
