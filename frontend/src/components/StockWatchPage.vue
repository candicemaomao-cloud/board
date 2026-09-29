<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { api } from '../api'
import { money } from '../format'

const INTERVALS = [
  { sec: 30, label: '30秒' },
  { sec: 60, label: '1分钟' },
  { sec: 300, label: '5分钟' },
  { sec: 900, label: '15分钟' },
]

const rows = ref([])
const people = ref([])
const peopleLoading = ref(false)
const peopleError = ref('')
const loading = ref(false)
const saving = ref(false)
const testing = ref(false)
const toggling = ref(null)
const error = ref('')
const ok = ref('')
const listOk = ref('')
const listError = ref('')
const search = ref('')
const filter = ref('all')
const screen = ref('list')
const editingId = ref(null)

const STRATEGIES = [
  { key: 'mu_dip', label: '局部底/顶 μ' },
  { key: 'deriv', label: '导数策略' },
]

const form = reactive({
  symbol: '',
  notes: '',
  strategy: 'mu_dip',
  monitor: false,
  allow_push: false,
  recipient_id: '',
  interval_sec: 60,
  budget: 10000,
  hedge_symbol: 'SPY',
  allow_trend: false,
  allow_short: false,
  day_filter: true,
  vol_adapt: true,
  integral: true,
  hedge: false,
  gradient: true,
  vol_confirm: false,
  vol_z_th: 1,
  kelly: false,
  fml_stop_loss_atr_mult: 0.5,
  fml_daily_atr_n: 14,
  fml_volume_high_pct: 90,
  fml_vwap_trend_lookback: 6,
  fml_allow_short: false,
  fml_use_shape_filter: false,
  fml_shape_confidence_threshold: 40,
  fml_close_no_trade_minutes: 0,
  fml_market_symbol: 'SPY',
})

const filtered = computed(() => {
  const q = search.value.trim().toUpperCase()
  let list = rows.value
  if (filter.value === 'on') list = list.filter((r) => r.monitor)
  if (filter.value === 'push') list = list.filter((r) => r.allow_push)
  if (q) list = list.filter((r) => String(r.symbol || '').toUpperCase().includes(q))
  return list
})

function resetForm() {
  editingId.value = null
  error.value = ''
  ok.value = ''
  form.symbol = ''
  form.notes = ''
  form.strategy = 'mu_dip'
  form.monitor = false
  form.allow_push = false
  form.recipient_id = ''
  form.interval_sec = 60
  form.budget = 10000
  form.hedge_symbol = 'SPY'
  form.allow_trend = false
  form.allow_short = false
  form.day_filter = true
  form.vol_adapt = true
  form.integral = true
  form.hedge = false
  form.gradient = true
  form.vol_confirm = false
  form.vol_z_th = 1
  form.kelly = false
  form.fml_stop_loss_atr_mult = 0.5
  form.fml_daily_atr_n = 14
  form.fml_volume_high_pct = 90
  form.fml_vwap_trend_lookback = 6
  form.fml_allow_short = false
  form.fml_use_shape_filter = false
  form.fml_shape_confidence_threshold = 40
  form.fml_close_no_trade_minutes = 0
  form.fml_market_symbol = 'SPY'
}

function backToList() {
  screen.value = 'list'
  resetForm()
}

async function loadPeople() {
  peopleLoading.value = true
  peopleError.value = ''
  try {
    const data = await api.recipients()
    people.value = data.items || []
  } catch (e) {
    people.value = []
    peopleError.value = e.message || '推送人加载失败'
  } finally {
    peopleLoading.value = false
  }
}

function pickDefaultRecipient() {
  if (form.recipient_id !== '' && form.recipient_id != null) return
  if (people.value.length === 1) form.recipient_id = people.value[0].id
}

async function openNew() {
  listError.value = ''
  resetForm()
  screen.value = 'edit'
  await loadPeople()
  pickDefaultRecipient()
}

function applyRecipientFromRow(row) {
  const rid = Number((row.recipient_ids || [])[0])
  form.recipient_id = people.value.some((p) => p.id === rid) ? rid : ''
}

async function editRow(row) {
  listError.value = ''
  editingId.value = row.id
  error.value = ''
  ok.value = ''
  form.symbol = row.symbol
  form.notes = row.notes || ''
  form.strategy = row.strategy || 'mu_dip'
  form.monitor = !!row.monitor
  form.allow_push = !!row.allow_push
  form.interval_sec = Number(row.interval_sec) || 60
  form.budget = Number(row.budget) || 10000
  form.hedge_symbol = row.hedge_symbol || 'SPY'
  form.allow_trend = !!row.allow_trend
  form.allow_short = !!row.allow_short
  form.day_filter = row.day_filter !== false
  form.vol_adapt = row.vol_adapt !== false
  form.integral = row.integral !== false
  form.hedge = !!row.hedge
  form.gradient = row.gradient !== false
  form.vol_confirm = !!row.vol_confirm
  form.vol_z_th = Number(row.vol_z_th) || 1
  form.kelly = !!row.kelly
  form.fml_stop_loss_atr_mult = Number(row.fml_stop_loss_atr_mult) || 0.5
  form.fml_daily_atr_n = Number(row.fml_daily_atr_n) || 14
  form.fml_volume_high_pct = Number(row.fml_volume_high_pct) || 90
  form.fml_vwap_trend_lookback = Number(row.fml_vwap_trend_lookback) || 6
  form.fml_allow_short = !!row.fml_allow_short
  form.fml_use_shape_filter = !!row.fml_use_shape_filter
  form.fml_shape_confidence_threshold = Number(row.fml_shape_confidence_threshold) || 40
  form.fml_close_no_trade_minutes = Number(row.fml_close_no_trade_minutes) || 0
  form.fml_market_symbol = row.fml_market_symbol || 'SPY'
  screen.value = 'edit'
  await loadPeople()
  applyRecipientFromRow(row)
}

function payload() {
  const rid = Number(form.recipient_id)
  return {
    symbol: form.symbol.trim().toUpperCase(),
    notes: form.notes.trim() || null,
    strategy: form.strategy || 'mu_dip',
    monitor: !!form.monitor,
    allow_push: !!form.allow_push,
    recipient_ids: rid ? [rid] : [],
    interval_sec: Number(form.interval_sec) || 60,
    budget: Number(form.budget) || 10000,
    hedge_symbol: (form.hedge_symbol || 'SPY').trim().toUpperCase(),
    allow_trend: !!form.allow_trend,
    allow_short: !!form.allow_short,
    day_filter: !!form.day_filter,
    vol_adapt: !!form.vol_adapt,
    integral: !!form.integral,
    hedge: !!form.hedge,
    gradient: !!form.gradient,
    vol_confirm: !!form.vol_confirm,
    vol_z_th: Number(form.vol_z_th) || 1,
    kelly: !!form.kelly,
    fml_stop_loss_atr_mult: Number(form.fml_stop_loss_atr_mult) || 0.5,
    fml_daily_atr_n: Number(form.fml_daily_atr_n) || 14,
    fml_volume_high_pct: Number(form.fml_volume_high_pct) || 90,
    fml_vwap_trend_lookback: Number(form.fml_vwap_trend_lookback) || 6,
    fml_allow_short: !!form.fml_allow_short,
    fml_use_shape_filter: !!form.fml_use_shape_filter,
    fml_shape_confidence_threshold: Number(form.fml_shape_confidence_threshold) || 40,
    fml_close_no_trade_minutes: Number(form.fml_close_no_trade_minutes) || 0,
    fml_market_symbol: (form.fml_market_symbol || 'SPY').trim().toUpperCase(),
  }
}

async function load() {
  loading.value = true
  listError.value = ''
  try {
    rows.value = (await api.stockWatches()) || []
  } catch (e) {
    listError.value = e.message || '加载失败'
    rows.value = []
  } finally {
    loading.value = false
  }
  await loadPeople()
}

async function save(andTest = false) {
  if (!form.symbol.trim()) {
    error.value = '请填股票代码'
    return
  }
  if (!form.recipient_id) {
    error.value = '请选择推送人（测试和推手机都要选人）'
    return
  }
  saving.value = true
  error.value = ''
  ok.value = ''
  try {
    const body = payload()
    let saved
    if (editingId.value) saved = await api.updateStockWatch(editingId.value, body)
    else saved = await api.createStockWatch(body)
    editingId.value = saved.id
    ok.value = '已保存'
    await load()
    if (andTest) await testPush(saved.id, true)
    else screen.value = 'list'
  } catch (e) {
    error.value = e.message || '保存失败'
  } finally {
    saving.value = false
  }
}

async function testPush(id, fromSave = false) {
  testing.value = true
  if (!fromSave) listError.value = ''
  else error.value = ''
  try {
    const res = await api.testStockWatchPush(id)
    const ch = (res.push_test?.channels || []).join('、') || '已发出'
    const msg = `测试已发到 ${ch}。${res.symbol} @ $${res.last_price}，${res.last_action || ''}`
    if (fromSave) {
      ok.value = msg
      screen.value = 'list'
    } else {
      listOk.value = msg
      const row = rows.value.find((r) => r.id === id)
      if (row) Object.assign(row, res)
    }
  } catch (e) {
    const msg = e.message || '测试推送失败'
    if (fromSave) error.value = msg
    else listError.value = msg
  } finally {
    testing.value = false
  }
}

async function remove(row) {
  if (!confirm(`删除 ${row.symbol}？`)) return
  try {
    await api.deleteStockWatch(row.id)
    await load()
  } catch (e) {
    listError.value = e.message || '删除失败'
  }
}

async function toggleMonitor(row) {
  toggling.value = `m-${row.id}`
  try {
    const next = !row.monitor
    const updated = await api.setStockWatchMonitor(row.id, next)
    Object.assign(row, updated)
  } catch (e) {
    listError.value = e.message || '切换失败'
  } finally {
    toggling.value = null
  }
}

async function togglePush(row) {
  toggling.value = `p-${row.id}`
  try {
    const next = !row.allow_push
    const updated = await api.setStockWatchPush(row.id, next)
    Object.assign(row, updated)
  } catch (e) {
    listError.value = e.message || '切换失败'
  } finally {
    toggling.value = null
  }
}

async function refresh(row) {
  toggling.value = `r-${row.id}`
  try {
    const updated = await api.refreshStockWatch(row.id)
    Object.assign(row, updated)
    if (updated.error) listError.value = updated.error
  } catch (e) {
    listError.value = e.message || '刷新失败'
  } finally {
    toggling.value = null
  }
}

function px(n) {
  if (n == null || n === '') return '—'
  return money(n, { signed: false, withSymbol: false })
}

function flags(row) {
  if (row.strategy === 'deriv') return '导数' + (row.fml_allow_short ? '·多空' : '·只多')
  const bits = []
  if (row.allow_trend) bits.push('顺势')
  if (row.allow_short) bits.push('做空')
  if (row.day_filter) bits.push('当天')
  if (row.vol_adapt) bits.push('波动')
  if (row.integral) bits.push('积分')
  if (row.hedge) bits.push('对冲')
  if (row.gradient) bits.push('梯度')
  if (row.vol_confirm) bits.push('量Z')
  if (row.kelly) bits.push('凯利')
  return bits.length ? bits.join(' · ') : '默认'
}

onMounted(load)
</script>

<template>
  <template v-if="screen === 'edit'">
    <div class="range-row">
      <button class="btn" type="button" @click="backToList">返回列表</button>
    </div>
    <p class="goal-lead">
      按股票代码保存一份日内勾选配置。打开「监听价格」后后台按间隔拉信号；打开「推手机」且出现买卖信号时，推给所选推送人。
    </p>
    <section class="panel">
      <h2 style="margin: 0 0 10px">{{ editingId ? '改股票' : '加股票' }}</h2>
      <div class="form-grid" style="margin-top: 10px">
        <div class="field">
          <label>股票代码</label>
          <input v-model="form.symbol" placeholder="MU / TSLA / NVDA" @keydown.enter.prevent="save" />
        </div>
        <div class="field">
          <label>策略</label>
          <select v-model="form.strategy">
            <option v-for="s in STRATEGIES" :key="s.key" :value="s.key">{{ s.label }}</option>
          </select>
        </div>
        <div class="field">
          <label>每天本金</label>
          <input v-model.number="form.budget" type="number" min="200" step="100" />
        </div>
        <div class="field">
          <label>推送人</label>
          <select v-model="form.recipient_id" :disabled="peopleLoading">
            <option value="">
              {{ peopleLoading ? '加载推送人中…' : people.length ? '请选择' : '还没有推送人' }}
            </option>
            <option v-for="p in people" :key="p.id" :value="p.id">
              {{ p.name }}{{ p.channels?.length ? ` · ${p.channels.join('/')}` : '' }}
            </option>
          </select>
          <div class="error sub" v-if="peopleError">{{ peopleError }}</div>
          <div class="sub" v-else-if="!peopleLoading && !people.length">去「推送 → 推送人」先添加。</div>
          <div class="sub" v-else-if="people.length">保存后可点「保存并测试推送」，手机会收到代码、现价和算法说明。</div>
        </div>
        <div class="field">
          <label>计算间隔</label>
          <select v-model.number="form.interval_sec">
            <option v-for="item in INTERVALS" :key="item.sec" :value="item.sec">{{ item.label }}</option>
          </select>
        </div>
        <div class="field">
          <label>备注</label>
          <input v-model="form.notes" placeholder="可选" />
        </div>
        <div class="field">
          <label>对冲标的</label>
          <input v-model="form.hedge_symbol" placeholder="SPY" :disabled="!form.hedge" />
        </div>
      </div>

      <div class="range-row" style="margin-top: 12px">
        <label class="check"><input v-model="form.monitor" type="checkbox" /> 监听价格（后台按间隔算信号）</label>
        <label class="check"><input v-model="form.allow_push" type="checkbox" /> 推手机（有买卖信号才发）</label>
      </div>

      <div class="range-row" style="margin-top: 8px" v-if="form.strategy === 'mu_dip'">
        <label class="check"><input v-model="form.allow_trend" type="checkbox" /> 顺势突破</label>
        <label class="check"><input v-model="form.allow_short" type="checkbox" /> 允许做空</label>
        <label class="check"><input v-model="form.day_filter" type="checkbox" /> 当天过滤</label>
        <label class="check"><input v-model="form.vol_adapt" type="checkbox" /> 波动率出场</label>
        <label class="check"><input v-model="form.integral" type="checkbox" /> 积分闸门</label>
        <label class="check"><input v-model="form.hedge" type="checkbox" /> 偏导对冲</label>
        <label class="check"><input v-model="form.gradient" type="checkbox" /> 梯度调仓</label>
        <label class="check">
          <input v-model="form.vol_confirm" type="checkbox" />
          量能确认（近3根最高 Z ≥
          <input
            v-model.number="form.vol_z_th"
            type="number"
            min="0.5"
            max="3"
            step="0.1"
            style="width: 52px; margin: 0 4px"
            :disabled="!form.vol_confirm"
          />
          ）
        </label>
        <label class="check"><input v-model="form.kelly" type="checkbox" /> 半凯利</label>
      </div>
      <div class="range-row" style="margin-top: 8px" v-else>
        <label class="check">
          止损 =
          <input
            v-model.number="form.fml_stop_loss_atr_mult"
            type="number"
            min="0.1"
            max="5"
            step="0.1"
            style="width: 52px; margin: 0 4px"
          />
          × 日线
          <input
            v-model.number="form.fml_daily_atr_n"
            type="number"
            min="5"
            max="60"
            style="width: 48px; margin: 0 4px"
          />
          期 ATR
        </label>
        <label class="check">
          放量分位 ≥
          <input
            v-model.number="form.fml_volume_high_pct"
            type="number"
            min="50"
            max="99"
            style="width: 52px; margin: 0 4px"
          />
        </label>
        <label class="check">
          VWAP 趋势回看
          <input
            v-model.number="form.fml_vwap_trend_lookback"
            type="number"
            min="2"
            max="30"
            style="width: 48px; margin: 0 4px"
          />
          根
        </label>
        <label class="check"><input v-model="form.fml_allow_short" type="checkbox" /> 允许做空</label>
      </div>
      <div class="range-row" style="margin-top: 8px" v-if="form.strategy === 'deriv'">
        <label class="check">
          <input v-model="form.fml_use_shape_filter" type="checkbox" />
          形态过滤（先预测今天最可能是哪种VWAP轨道形态，按形态限制开多/开空）
        </label>
        <template v-if="form.fml_use_shape_filter">
          <label class="check">
            置信度阈值 ≥
            <input
              v-model.number="form.fml_shape_confidence_threshold"
              type="number"
              min="10"
              max="90"
              style="width: 48px; margin: 0 4px"
            />
            %
          </label>
          <label class="check">
            大盘参照
            <input v-model="form.fml_market_symbol" type="text" placeholder="SPY" style="width: 70px; margin: 0 4px" />
          </label>
        </template>
        <label class="check">
          收盘前
          <input
            v-model.number="form.fml_close_no_trade_minutes"
            type="number"
            min="0"
            max="120"
            style="width: 48px; margin: 0 4px"
          />
          分钟不开新仓（0=不限制）
        </label>
      </div>
      <p class="sub" style="margin-top: 4px" v-if="form.strategy !== 'mu_dip'">
        导数策略：VWAP 趋势向上 + 放量确认 => 买；{{ form.fml_allow_short ? 'VWAP 趋势向下 + 放量确认（且今天出现过站上再跌破 SMA）=> 卖/做空；' : '' }}二阶导数真反转+放量 => 提示止盈/离场。{{ form.fml_allow_short ? '' : '当前只做多不做空。' }}
      </p>

      <div class="modal-actions" style="margin-top: 12px; justify-content: flex-start; gap: 8px">
        <button class="btn btn-primary" type="button" :disabled="saving || testing" @click="save(false)">
          {{ saving ? '保存中…' : editingId ? '更新' : '保存' }}
        </button>
        <button
          class="btn"
          type="button"
          :disabled="saving || testing || !form.recipient_id"
          @click="save(true)"
        >
          {{ testing ? '发送中…' : '保存并测试推送' }}
        </button>
      </div>
      <div class="error" v-if="error">{{ error }}</div>
      <div class="ok-msg" v-else-if="ok">{{ ok }}</div>
    </section>
  </template>

  <template v-else>
    <p class="goal-lead">
      股票列表：按代码增删改查。每只票单独勾日内选项；可开监听价格，有信号再推手机。
    </p>
    <div class="range-row">
      <div class="watch-edit" style="flex: 1; margin: 0">
        <input v-model="search" placeholder="搜索代码" />
      </div>
      <button class="btn" type="button" :disabled="loading" @click="load">
        {{ loading ? '加载中…' : '刷新' }}
      </button>
      <button class="btn btn-primary" type="button" @click="openNew">添加</button>
    </div>
    <div class="pills" style="margin-top: 8px">
      <button class="pill" type="button" :class="{ active: filter === 'all' }" @click="filter = 'all'">
        全部 {{ rows.length }}
      </button>
      <button class="pill" type="button" :class="{ active: filter === 'on' }" @click="filter = 'on'">
        监听中 {{ rows.filter((r) => r.monitor).length }}
      </button>
      <button class="pill" type="button" :class="{ active: filter === 'push' }" @click="filter = 'push'">
        推手机 {{ rows.filter((r) => r.allow_push).length }}
      </button>
    </div>
    <div class="error" v-if="listError" style="margin-top: 8px">{{ listError }}</div>
    <div class="ok-msg" v-if="listOk" style="margin-top: 8px">{{ listOk }}</div>

    <section class="panel" style="margin-top: 12px">
      <div class="table-wrap" v-if="filtered.length">
        <table>
          <thead>
            <tr>
              <th>代码</th>
              <th>监听</th>
              <th>推手机</th>
              <th>现价</th>
              <th>动作</th>
              <th>勾选</th>
              <th>推送人</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in filtered" :key="row.id">
              <td>
                <b>{{ row.symbol }}</b>
                <div class="sub" v-if="row.notes">{{ row.notes }}</div>
                <div class="sub" v-if="row.last_error" style="color: var(--down, #f66)">{{ row.last_error }}</div>
              </td>
              <td>
                <button
                  class="btn"
                  type="button"
                  :disabled="toggling === `m-${row.id}`"
                  @click="toggleMonitor(row)"
                >
                  {{ row.monitor ? '开' : '关' }}
                </button>
              </td>
              <td>
                <button
                  class="btn"
                  type="button"
                  :disabled="toggling === `p-${row.id}`"
                  @click="togglePush(row)"
                >
                  {{ row.allow_push ? '开' : '关' }}
                </button>
              </td>
              <td>
                {{ px(row.last_price) }}
                <div class="sub" v-if="row.last_asof">{{ row.last_asof }} ET</div>
              </td>
              <td>{{ row.last_action || '—' }}</td>
              <td class="sub">{{ flags(row) }}</td>
              <td class="sub">{{ (row.recipient_names || []).join('、') || '—' }}</td>
              <td>
                <button class="btn" type="button" :disabled="toggling === `r-${row.id}`" @click="refresh(row)">
                  立刻算
                </button>
                <button
                  class="btn"
                  type="button"
                  :disabled="testing || !(row.recipient_ids || []).length"
                  @click="testPush(row.id)"
                >
                  测试推送
                </button>
                <button class="btn" type="button" @click="editRow(row)">改</button>
                <button class="btn" type="button" @click="remove(row)">删</button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <p class="sub" v-else-if="!loading">还没有股票。点「添加」写入代码和勾选项。</p>
    </section>
  </template>
</template>
