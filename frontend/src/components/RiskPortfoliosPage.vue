<script setup>
import { computed, onMounted, ref } from 'vue'
import { api } from '../api'

const emit = defineEmits(['create', 'edit'])

const rows = ref([])
const loading = ref(false)
const toggling = ref(null)
const scanning = ref(null)
const error = ref('')
const ok = ref('')
const search = ref('')
const filter = ref('all') // all | on | off | high

const filtered = computed(() => {
  let list = rows.value
  const q = search.value.trim().toUpperCase()
  if (filter.value === 'on') list = list.filter((r) => r.enabled)
  if (filter.value === 'off') list = list.filter((r) => !r.enabled)
  if (filter.value === 'high') {
    list = list.filter((r) => r.crisis_level === '高危' || r.crisis_level === '警惕')
  }
  if (q) {
    list = list.filter(
      (r) =>
        String(r.name || '').toUpperCase().includes(q) ||
        (r.legs || []).some((l) => String(l.symbol || '').toUpperCase().includes(q)),
    )
  }
  return list
})

const onCount = computed(() => rows.value.filter((r) => r.enabled).length)

function legsLabel(row) {
  const legs = row.legs || []
  if (!legs.length) return '—'
  return legs
    .map((l) => {
      const side = l.side === 'short' ? '空' : '多'
      return `${l.symbol}(${side})`
    })
    .join(' · ')
}

function budgetPrices(row) {
  const legs = row.budget?.legs
  if (Array.isArray(legs) && legs.length) {
    return legs
      .filter((l) => l.start_price != null || l.price != null)
      .map((l) => {
        const a = l.start_price != null ? Number(l.start_price).toFixed(2) : '—'
        const b = l.price != null ? Number(l.price).toFixed(2) : '—'
        return `${l.symbol} ${a}→${b}`
      })
  }
  // fallback：腿上存的 entry_price
  const raw = Array.isArray(row.legs) ? row.legs : []
  const lines = raw
    .filter((l) => l.entry_price != null && Number(l.entry_price) > 0)
    .map((l) => `${String(l.symbol || '').toUpperCase()} ${Number(l.entry_price).toFixed(2)}`)
  return lines
}

function levelClass(level) {
  if (level === '高危') return 'error'
  if (level === '警惕') return ''
  return 'sub'
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const data = await api.riskPortfolios()
    rows.value = data.items || []
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

function openNew() {
  ok.value = ''
  error.value = ''
  emit('create')
}

function openEdit(row) {
  ok.value = ''
  error.value = ''
  emit('edit', row)
}

async function toggle(row) {
  toggling.value = row.id
  ok.value = ''
  error.value = ''
  try {
    const next = !row.enabled
    const data = await api.setRiskPortfolioEnabled(row.id, next)
    const idx = rows.value.findIndex((r) => r.id === row.id)
    if (idx >= 0) rows.value[idx] = data
    ok.value = next
      ? `已开启「${row.name}」：将每日自动扫描危机系数${data.scan_error ? `（本次扫描：${data.scan_error}）` : ''}`
      : `已关闭「${row.name}」`
  } catch (e) {
    error.value = e.message
  } finally {
    toggling.value = null
  }
}

async function scanNow(row) {
  scanning.value = row.id
  ok.value = ''
  error.value = ''
  try {
    const data = await api.scanRiskPortfolio(row.id)
    const idx = rows.value.findIndex((r) => r.id === row.id)
    if (idx >= 0) rows.value[idx] = data
    ok.value = `「${row.name}」危机系数 ${data.crisis_coefficient}（${data.crisis_level}）`
  } catch (e) {
    error.value = e.message
  } finally {
    scanning.value = null
  }
}

async function remove(row) {
  if (!confirm(`删除风险组合「${row.name}」？`)) return
  error.value = ''
  try {
    await api.deleteRiskPortfolio(row.id)
    rows.value = rows.value.filter((r) => r.id !== row.id)
    ok.value = '已删除'
  } catch (e) {
    error.value = e.message
  }
}

onMounted(load)
</script>

<template>
  <p class="goal-lead">
    风险组合列表：保存仓位后可开启每日收盘危机系数扫描。
    含成本价浮亏、路径风险、对冲失效、VIX；持仓价列为建仓价→现价。
  </p>

  <section class="panel" style="margin-top: 12px">
    <div class="range-row" style="margin-bottom: 10px">
      <input v-model="search" class="strategy-select" style="width: 180px" placeholder="搜名称/代码" />
      <div class="pills">
        <button class="pill" type="button" :class="{ active: filter === 'all' }" @click="filter = 'all'">全部</button>
        <button class="pill" type="button" :class="{ active: filter === 'on' }" @click="filter = 'on'">已开启</button>
        <button class="pill" type="button" :class="{ active: filter === 'off' }" @click="filter = 'off'">已关闭</button>
        <button class="pill" type="button" :class="{ active: filter === 'high' }" @click="filter = 'high'">警惕+</button>
      </div>
      <button class="btn" type="button" :disabled="loading" @click="load">{{ loading ? '刷新中…' : '刷新' }}</button>
      <button class="btn btn-primary" type="button" @click="openNew">新增组合</button>
    </div>

    <p class="sub">
      已开启 {{ onCount }} 个 · 每个开启组合在美股收盘后每天自动扫描一次危机系数（可手动「立即扫描」）。
    </p>
    <div class="error" v-if="error" style="margin-top: 8px">{{ error }}</div>
    <p class="sub" v-else-if="ok" style="margin-top: 8px; color: #3d9a5f">{{ ok }}</p>

    <div class="table-wrap" style="margin-top: 10px">
      <table>
        <thead>
          <tr>
            <th>名称</th>
            <th>标的</th>
            <th>亏损底线</th>
            <th>危机系数</th>
            <th>等级</th>
            <th>VIX</th>
            <th>持仓价</th>
            <th>上次扫描</th>
            <th>开启</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
          <tr v-if="!filtered.length">
            <td colspan="10" class="sub">还没有风险组合。点「新增组合」去测算并保存。</td>
          </tr>
          <tr v-for="row in filtered" :key="row.id">
            <td>
              <b>{{ row.name }}</b>
              <div class="sub" v-if="row.notes">{{ row.notes }}</div>
            </td>
            <td class="sub" style="max-width: 220px">{{ legsLabel(row) }}</td>
            <td class="mono">{{ ((row.loss_limit || 0) * 100).toFixed(0) }}%</td>
            <td class="mono">
              {{ row.crisis_coefficient != null ? Number(row.crisis_coefficient).toFixed(1) : '—' }}
            </td>
            <td :class="levelClass(row.crisis_level)">{{ row.crisis_level || '—' }}</td>
            <td class="mono">
              <template v-if="row.vix?.ok">
                {{ Number(row.vix.level).toFixed(1) }}
                <div class="sub">{{ row.vix.regime }} · +{{ Number(row.vix.vix_score || 0).toFixed(1) }}分</div>
              </template>
              <span v-else class="sub">—</span>
            </td>
            <td class="mono">
              <template v-if="budgetPrices(row).length">
                <div v-for="(line, i) in budgetPrices(row)" :key="i">{{ line }}</div>
              </template>
              <span v-else class="sub">—</span>
            </td>
            <td class="sub">
              {{ row.last_run_date || '—' }}
              <span v-if="row.last_error" class="error"><br />{{ row.last_error }}</span>
            </td>
            <td>
              <button
                class="btn"
                type="button"
                :disabled="toggling === row.id"
                @click="toggle(row)"
              >
                {{ toggling === row.id ? '…' : row.enabled ? '关闭' : '开启' }}
              </button>
            </td>
            <td style="white-space: nowrap">
              <button class="btn" type="button" @click="openEdit(row)">编辑</button>
              <button
                class="btn"
                type="button"
                :disabled="scanning === row.id"
                @click="scanNow(row)"
              >
                {{ scanning === row.id ? '扫描中…' : '立即扫描' }}
              </button>
              <button class="btn" type="button" @click="remove(row)">删</button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>
