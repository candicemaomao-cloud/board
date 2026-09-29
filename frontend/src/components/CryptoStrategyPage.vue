<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '../api'
import { can } from '../auth'

const emit = defineEmits(['open-backtest'])

const canWrite = computed(() => can('btn.crypto.strategy') || can('btn.crypto.write') || can('menu.cryptoStrategy'))

const rows = ref([])
const coins = ref([])
const catalog = ref([])
const people = ref([])
const loading = ref(false)
const saving = ref(false)
const dialog = ref(false)
const editingId = ref(null)
const groupFilter = ref('全部')

const form = reactive({
  name: '',
  notes: '',
  coin_id: null,
  join: 'and',
  indicators: [],
  timeframe: '1d',
  recipient_ids: [],
  enabled: false,
  allow_push: false,
  interval_sec: 300,
})

const groups = computed(() => {
  const set = new Set((catalog.value || []).map((x) => x.group || '其他'))
  return ['全部', ...set]
})

const catalogShown = computed(() => {
  let list = catalog.value || []
  if (groupFilter.value !== '全部') list = list.filter((x) => x.group === groupFilter.value)
  return list
})

function resetForm() {
  editingId.value = null
  form.name = ''
  form.notes = ''
  form.coin_id = coins.value[0]?.id || null
  form.join = 'and'
  form.indicators = []
  form.timeframe = '1d'
  form.recipient_ids = []
  form.enabled = false
  form.allow_push = false
  form.interval_sec = 300
}

async function load() {
  loading.value = true
  try {
    const [s, c, ind, rec] = await Promise.all([
      api.cryptoStrategies(),
      api.cryptoCoins({ with_quote: false }),
      api.cryptoIndicators(),
      api.recipients(),
    ])
    rows.value = s.items || []
    coins.value = c.items || []
    catalog.value = ind.items || []
    people.value = rec.items || []
  } catch (e) {
    ElMessage.error(e.message || '加载失败')
  } finally {
    loading.value = false
  }
}

function openNew() {
  resetForm()
  dialog.value = true
}

function openEdit(row) {
  editingId.value = row.id
  form.name = row.name || ''
  form.notes = row.notes || ''
  form.coin_id = row.coin_id || null
  form.join = row.join || 'and'
  form.indicators = [...(row.indicators || [])]
  form.timeframe = row.timeframe || '1d'
  form.recipient_ids = [...(row.recipient_ids || [])]
  form.enabled = !!row.enabled
  form.allow_push = !!row.allow_push
  form.interval_sec = row.interval_sec || 300
  dialog.value = true
}

async function save() {
  if (!form.coin_id && !form.name) {
    ElMessage.warning('请选择币种')
    return
  }
  if (!form.indicators.length) {
    ElMessage.warning('请至少选一个指标')
    return
  }
  saving.value = true
  try {
    const body = {
      name: form.name.trim() || null,
      notes: form.notes.trim() || null,
      coin_id: form.coin_id,
      join: form.join,
      indicators: form.indicators,
      timeframe: form.timeframe,
      recipient_ids: form.recipient_ids,
      enabled: form.enabled,
      allow_push: form.allow_push,
      interval_sec: form.interval_sec,
    }
    if (editingId.value) {
      const out = await api.updateCryptoStrategy(editingId.value, body)
      ElMessage.success('已保存')
      if (out?.notify?.ok) ElMessage.success('确认消息已发给推送人')
      else if (out?.notify && !out.notify.ok) ElMessage.warning(out.notify.error || '确认消息发送失败')
    } else {
      const out = await api.createCryptoStrategy(body)
      ElMessage.success('已保存')
      if (out?.notify?.ok) ElMessage.success('确认消息已发给推送人')
      else if (out?.notify && !out.notify.ok) ElMessage.warning(out.notify.error || '确认消息发送失败')
    }
    dialog.value = false
    await load()
  } catch (e) {
    ElMessage.error(e.message || '保存失败')
  } finally {
    saving.value = false
  }
}

async function remove(row) {
  try {
    await ElMessageBox.confirm(`删除策略「${row.name}」？`, '确认', { type: 'warning' })
    await api.deleteCryptoStrategy(row.id)
    ElMessage.success('已删除')
    await load()
  } catch (e) {
    if (e !== 'cancel') ElMessage.error(e.message || '删除失败')
  }
}

async function toggle(row, key) {
  try {
    const out = await api.updateCryptoStrategy(row.id, { [key]: !row[key] })
    await load()
    const n = out?.notify
    if (n) {
      if (n.ok) ElMessage.success(key === 'allow_push' ? '已开启推送，确认消息已发出' : '已开启监听，确认消息已发出')
      else ElMessage.warning(n.error || '开关已改，但确认消息发送失败')
    }
  } catch (e) {
    ElMessage.error(e.message || '更新失败')
  }
}

async function check(row) {
  try {
    const out = await api.checkCryptoStrategy(row.id)
    ElMessage.success(out.last_hit ? '当前命中' : '当前未命中')
    await load()
  } catch (e) {
    ElMessage.error(e.message || '检查失败')
  }
}

async function refreshAll() {
  loading.value = true
  try {
    const res = await api.refreshCryptoStrategies()
    rows.value = res.items || []
    ElMessage.success('已刷新全部策略')
  } catch (e) {
    ElMessage.error(e.message || '刷新失败')
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <p class="goal-lead">
    策略：把技术指标组合起来，开启监听后按周期检查；命中且开启推送时，发到「推送人」。
    打开「监听」或「推送」开关时，会立刻给推送人发一条确认消息。点「回测」可看历史命中开平多表现。手写算法请去「自定义策略」。
  </p>

  <div class="range-row">
    <el-button :loading="loading" @click="refreshAll">全部检查</el-button>
    <el-button v-if="canWrite" type="primary" @click="openNew">新建策略</el-button>
  </div>

  <section class="panel" style="margin-top: 12px" v-loading="loading">
    <el-table :data="rows" stripe style="width: 100%">
      <el-table-column prop="name" label="名称" min-width="140" />
      <el-table-column prop="symbol" label="币" width="80" />
      <el-table-column prop="timeframe" label="周期" width="72" />
      <el-table-column label="组合" width="72">
        <template #default="{ row }">{{ row.join === 'or' ? '或' : '且' }}</template>
      </el-table-column>
      <el-table-column label="指标数" width="72">
        <template #default="{ row }">{{ (row.indicators || []).length }}</template>
      </el-table-column>
      <el-table-column label="状态" width="88">
        <template #default="{ row }">
          <span v-if="row.last_hit === true" class="up">命中</span>
          <span v-else-if="row.last_hit === false" class="sub">未中</span>
          <span v-else class="sub">—</span>
        </template>
      </el-table-column>
      <el-table-column label="监听" width="72">
        <template #default="{ row }">
          <el-switch :model-value="row.enabled" :disabled="!canWrite" @change="toggle(row, 'enabled')" />
        </template>
      </el-table-column>
      <el-table-column label="推送" width="72">
        <template #default="{ row }">
          <el-switch :model-value="row.allow_push" :disabled="!canWrite" @change="toggle(row, 'allow_push')" />
        </template>
      </el-table-column>
      <el-table-column label="操作" width="200" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" @click="check(row)">检查</el-button>
          <el-button link type="primary" @click="emit('open-backtest', row.id)">回测</el-button>
          <el-button v-if="canWrite" link type="primary" @click="openEdit(row)">改</el-button>
          <el-button v-if="canWrite" link type="danger" @click="remove(row)">删</el-button>
        </template>
      </el-table-column>
    </el-table>
    <p class="sub" v-if="!loading && !rows.length" style="margin-top: 12px">还没有策略，点上方新建。</p>
  </section>

  <el-dialog v-model="dialog" :title="editingId ? '改策略' : '新建策略'" width="640px" destroy-on-close>
    <el-form label-width="96px">
      <el-form-item label="名称">
        <el-input v-model="form.name" placeholder="BTC 均线+RSI" />
      </el-form-item>
      <el-form-item label="币种">
        <el-select v-model="form.coin_id" style="width: 100%">
          <el-option v-for="c in coins" :key="c.id" :label="`${c.symbol} · ${c.name}`" :value="c.id" />
        </el-select>
      </el-form-item>
      <el-form-item label="周期">
        <el-select v-model="form.timeframe" style="width: 160px">
          <el-option label="5m" value="5m" />
          <el-option label="30m" value="30m" />
          <el-option label="1h" value="1h" />
          <el-option label="4h" value="4h" />
          <el-option label="日线" value="1d" />
        </el-select>
      </el-form-item>
      <el-form-item label="组合">
        <el-radio-group v-model="form.join">
          <el-radio value="and">全部满足（且）</el-radio>
          <el-radio value="or">任一满足（或）</el-radio>
        </el-radio-group>
      </el-form-item>
      <el-form-item label="指标">
        <div class="pills" style="margin-bottom: 8px; flex-wrap: wrap">
          <button
            v-for="g in groups"
            :key="g"
            class="pill"
            :class="{ active: groupFilter === g }"
            type="button"
            @click="groupFilter = g"
          >
            {{ g }}
          </button>
        </div>
        <el-checkbox-group v-model="form.indicators" class="ind-grid">
          <el-checkbox v-for="ind in catalogShown" :key="ind.id" :value="ind.id" :label="ind.id">
            {{ ind.name }}
          </el-checkbox>
        </el-checkbox-group>
      </el-form-item>
      <el-form-item label="检查间隔">
        <el-select v-model="form.interval_sec" style="width: 160px">
          <el-option :value="60" label="1 分钟" />
          <el-option :value="300" label="5 分钟" />
          <el-option :value="900" label="15 分钟" />
          <el-option :value="1800" label="30 分钟" />
          <el-option :value="3600" label="60 分钟" />
        </el-select>
      </el-form-item>
      <el-form-item label="推送人">
        <el-select v-model="form.recipient_ids" multiple clearable style="width: 100%" placeholder="默认全部就绪渠道">
          <el-option v-for="p in people" :key="p.id" :label="p.name || `推送人 ${p.id}`" :value="p.id" />
        </el-select>
      </el-form-item>
      <el-form-item label="开关">
        <el-checkbox v-model="form.enabled">开启监听</el-checkbox>
        <el-checkbox v-model="form.allow_push">命中推送</el-checkbox>
      </el-form-item>
      <el-form-item label="备注">
        <el-input v-model="form.notes" type="textarea" :rows="2" />
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="dialog = false">取消</el-button>
      <el-button type="primary" :loading="saving" @click="save">保存</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.ind-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 4px 12px;
  max-height: 220px;
  overflow: auto;
  width: 100%;
}
</style>
