<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from '../api'
import { can } from '../auth'

const emit = defineEmits(['open-backtest'])

const canWrite = computed(
  () =>
    can('btn.crypto.strategy') ||
    can('btn.crypto.write') ||
    can('menu.cryptoCustomStrategy') ||
    can('menu.cryptoStrategy'),
)

const rows = ref([])
const coins = ref([])
const people = ref([])
const specs = ref([])
const defaults = ref({})
const loading = ref(false)
const saving = ref(false)
const dialog = ref(false)
const editingId = ref(null)

const form = reactive({
  name: '',
  notes: '',
  watch_symbols: [],
  timeframe: '5m',
  recipient_ids: [],
  enabled: false,
  allow_push: false,
  interval_sec: 60,
  params: {},
})

function coinBinance(c) {
  return (c.binance_symbol || `${c.symbol}USDT`).toUpperCase().replace(/[^A-Z0-9]/g, '')
}

async function load() {
  loading.value = true
  try {
    const [list, c, rec, cat] = await Promise.all([
      api.cryptoCustomStrategies(),
      api.cryptoCoins({ with_quote: false }),
      api.recipients(),
      api.cryptoCustomCatalog(),
    ])
    rows.value = list.items || []
    coins.value = c.items || []
    people.value = rec.items || []
    specs.value = cat.param_specs || []
    defaults.value = cat.defaults || {}
  } catch (e) {
    ElMessage.error(e.message || '加载失败')
  } finally {
    loading.value = false
  }
}

function openEdit(row) {
  editingId.value = row.id
  form.name = row.name || row.strategy_name || ''
  form.notes = row.notes || ''
  form.watch_symbols = [...(row.watch_symbols || [])]
  form.timeframe = row.timeframe || row.params?.timeframe || '5m'
  form.recipient_ids = [...(row.recipient_ids || [])]
  form.enabled = !!row.enabled
  form.allow_push = !!row.allow_push
  form.interval_sec = row.interval_sec || 60
  const p = { ...(defaults.value || {}), ...(row.params || {}) }
  delete p.watch_symbols
  form.timeframe = p.timeframe || form.timeframe || '5m'
  form.params = p
  dialog.value = true
}

async function save() {
  if (!form.watch_symbols.length) {
    ElMessage.warning('请至少选一个监听币种')
    return
  }
  saving.value = true
  try {
    await api.updateCryptoCustomStrategy(editingId.value, {
      name: form.name.trim() || null,
      notes: form.notes.trim() || null,
      watch_symbols: form.watch_symbols,
      params: { ...form.params, timeframe: form.timeframe },
      recipient_ids: form.recipient_ids,
      enabled: form.enabled,
      allow_push: form.allow_push,
      interval_sec: form.interval_sec,
    })
    ElMessage.success('已保存')
    dialog.value = false
    await load()
  } catch (e) {
    ElMessage.error(e.message || '保存失败')
  } finally {
    saving.value = false
  }
}

async function toggle(row, key) {
  if (!canWrite.value) return
  try {
    const out = await api.updateCryptoCustomStrategy(row.id, { [key]: !row[key] })
    Object.assign(row, out)
    ElMessage.success(
      key === 'enabled' ? (row.enabled ? '已开监听' : '已关监听') : row.allow_push ? '已开推送' : '已关推送',
    )
  } catch (e) {
    ElMessage.error(e.message || '更新失败')
  }
}

async function check(row) {
  try {
    const out = await api.checkCryptoCustomStrategy(row.id)
    Object.assign(row, out)
    ElMessage.success(out.last_action || out.last_direction || '已检查')
  } catch (e) {
    ElMessage.error(e.message || '检查失败')
  }
}

const pushingId = ref(null)
async function testPush(row) {
  pushingId.value = row.id
  try {
    const out = await api.testPushCryptoCustomStrategy(row.id)
    Object.assign(row, out)
    ElMessage.success(out.push_test?.ok ? '测试推送已发出，请查手机' : '已请求推送')
  } catch (e) {
    ElMessage.error(e.message || '测试推送失败')
  } finally {
    pushingId.value = null
  }
}

function openBacktest(row) {
  emit('open-backtest', row.id)
}

function watchesLabel(row) {
  const list = row.watch_symbols || []
  if (!list.length) return '—'
  if (list.length <= 2) return list.join('、')
  return `${list.slice(0, 2).join('、')} 等${list.length}个`
}

const activeSpecs = computed(() => specs.value.filter(s => !s.strategies || s.strategies.includes(rows.value.find(r => r.id === editingId.value)?.strategy_key || 'deriv')))
const boolSpecs = computed(() => activeSpecs.value.filter((s) => s.type === 'bool'))
const fieldSpecs = computed(() =>
  activeSpecs.value.filter((s) => s.type !== 'bool' && s.key !== 'watch_symbols' && s.key !== 'timeframe'),
)
const OPTION_LABEL = { breakout: '突破（导数+放量）', pullback: '回踩趋势线' }

const TF_LABEL = { '5m': '5 分钟', '30m': '30 分钟', '1h': '1 小时', '1d': '日线' }

onMounted(load)
</script>

<template>
  <p class="goal-lead">
    自定义策略列表：内置「导数策略」与「导数高级策略」。高级版使用平滑导数、高周期趋势及 ATR 跟踪止损，可跨日持仓。点「修改」配置参数、监听币种和推送。
    推送只在买入 / 卖出 / 止盈事件时发，观望状态不会推。
  </p>

  <div class="range-row" style="margin-bottom: 12px">
    <el-button :loading="loading" @click="load">刷新</el-button>
  </div>

  <section class="panel" v-loading="loading">
    <el-table :data="rows" stripe style="width: 100%">
      <el-table-column prop="name" label="策略" min-width="120" />
      <el-table-column label="周期" width="88">
        <template #default="{ row }">{{ TF_LABEL[row.timeframe] || row.timeframe || '5m' }}</template>
      </el-table-column>
      <el-table-column label="文件" width="160">
        <template #default="{ row }">
          <span class="mono sub">{{ row.file || '—' }}</span>
        </template>
      </el-table-column>
      <el-table-column label="监听币" min-width="140">
        <template #default="{ row }">{{ watchesLabel(row) }}</template>
      </el-table-column>
      <el-table-column label="方向" width="72">
        <template #default="{ row }">{{ row.last_direction || '—' }}</template>
      </el-table-column>
      <el-table-column label="最近动作" min-width="140">
        <template #default="{ row }">
          <span class="sub">{{ row.last_action || '—' }}</span>
          <div v-if="row.last_error" class="sub" style="color: var(--el-color-danger)">{{ row.last_error }}</div>
        </template>
      </el-table-column>
      <el-table-column label="上次推送" min-width="150">
        <template #default="{ row }">
          <span class="mono sub">{{ row.last_notified_asof || '—' }}</span>
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
      <el-table-column label="操作" width="260" fixed="right">
        <template #default="{ row }">
          <el-button link type="primary" @click="check(row)">检查</el-button>
          <el-button
            v-if="canWrite"
            link
            type="primary"
            :loading="pushingId === row.id"
            @click="testPush(row)"
          >测推送</el-button>
          <el-button link type="primary" @click="openBacktest(row)">回测</el-button>
          <el-button v-if="canWrite" link type="primary" @click="openEdit(row)">修改</el-button>
        </template>
      </el-table-column>
    </el-table>
    <p class="sub" v-if="!loading && !rows.length" style="margin-top: 12px">暂无策略，刷新一下会自动挂上导数策略。</p>
  </section>

  <el-dialog v-model="dialog" title="修改策略" width="720px" destroy-on-close>
    <el-form label-width="120px">
      <el-form-item label="名称">
        <el-input v-model="form.name" />
      </el-form-item>
      <el-form-item label="K线周期">
        <el-select v-model="form.timeframe" style="width: 160px">
          <el-option value="5m" label="5 分钟" />
          <el-option value="30m" label="30 分钟" />
          <el-option value="1h" label="1 小时" />
          <el-option value="1d" label="日线" />
        </el-select>
      </el-form-item>
      <el-form-item label="监听币种">
        <el-select v-model="form.watch_symbols" multiple filterable style="width: 100%" placeholder="从币列表选">
          <el-option v-for="c in coins" :key="c.id" :label="`${c.symbol} · ${c.name}`" :value="coinBinance(c)" />
        </el-select>
      </el-form-item>
      <el-form-item label="推送人">
        <el-select v-model="form.recipient_ids" multiple style="width: 100%" placeholder="不选则发给全部已接通推送人">
          <el-option v-for="p in people" :key="p.id" :label="p.name" :value="p.id" />
        </el-select>
      </el-form-item>
      <el-form-item label="检查间隔">
        <el-select v-model="form.interval_sec" style="width: 160px">
          <el-option :value="30" label="30 秒" />
          <el-option :value="60" label="1 分钟" />
          <el-option :value="300" label="5 分钟" />
          <el-option :value="900" label="15 分钟" />
        </el-select>
      </el-form-item>
      <el-form-item label="开关">
        <el-checkbox v-model="form.enabled">监听买入/卖出</el-checkbox>
        <el-checkbox v-model="form.allow_push">推送手机</el-checkbox>
      </el-form-item>
      <el-divider content-position="left">策略参数</el-divider>
      <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 8px 12px">
        <el-form-item v-for="s in fieldSpecs" :key="s.key" :label="s.label" label-width="140px">
          <el-input-number
            v-if="s.type === 'int' || s.type === 'float'"
            v-model="form.params[s.key]"
            :min="s.min"
            :max="s.max"
            :step="s.type === 'float' ? 0.1 : 1"
            controls-position="right"
            style="width: 100%"
          />
          <el-select v-else-if="s.type === 'enum'" v-model="form.params[s.key]" style="width: 100%">
            <el-option v-for="o in s.options" :key="o" :label="OPTION_LABEL[o] || o" :value="o" />
          </el-select>
          <el-input v-else v-model="form.params[s.key]" />
        </el-form-item>
      </div>
      <el-form-item label="选项">
        <el-checkbox v-for="s in boolSpecs" :key="s.key" v-model="form.params[s.key]" style="margin-right: 12px">
          {{ s.label }}
        </el-checkbox>
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
