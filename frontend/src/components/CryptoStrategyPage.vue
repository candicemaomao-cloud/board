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

const smartIndicators = ['rsi_cross_up_30', 'rsi_cross_down_70', 'macd_golden', 'macd_death', 'atr_spike', 'vol_high']
const monitorPresets = [
  { key: 'smart', name: '智能综合监控', description: '监控 1 小时级别的动能转折、趋势切换与放量波动；命中后再用日线历史形态复核。', timeframe: '1h', join: 'or', indicators: smartIndicators, interval_sec: 300 },
  { key: 'rebound', name: '短线超跌观察', description: '捕捉超卖和下轨破位，提醒等待止跌确认。', timeframe: '1h', join: 'or', indicators: ['rsi_lt30', 'stoch_oversold', 'bb_below_lower'], interval_sec: 300 },
  { key: 'trend', name: '中期趋势共振', description: '均线、动量和资金同向时提醒，用历史形态复核。', timeframe: '4h', join: 'and', indicators: ['align_bull', 'rsi_gt50', 'obv_up'], interval_sec: 900 },
  { key: 'risk', name: '长期转弱风险', description: '趋势、动量或资金流转弱时提醒复核仓位。', timeframe: '1d', join: 'or', indicators: ['macd_death', 'rsi_cross_down_50', 'cmf_neg'], interval_sec: 1800 },
]
const form = reactive({
  name: '', notes: '', coin_id: null, join: 'or', indicators: [...smartIndicators], timeframe: '1h', analysis_timeframe: '1d',
  recipient_ids: [], enabled: true, allow_push: true, interval_sec: 300,
  short_bars: 7, medium_bars: 14, long_bars: 30, horizon: 7,
})

const groups = computed(() => ['全部', ...new Set((catalog.value || []).map((x) => x.group || '其他'))])
const catalogShown = computed(() => groupFilter.value === '全部' ? catalog.value : catalog.value.filter((x) => x.group === groupFilter.value))
const selectedIndicatorNames = computed(() => catalog.value.filter((x) => form.indicators.includes(x.id)).map((x) => x.name))
const lifeGroup = computed(() => people.value.find((item) => String(item.name || '').includes('生活管家群')))
const selectedCoin = computed(() => coins.value.find((item) => item.id === form.coin_id))
const eventWindows = computed(() => [
  { key: 'short_bars', label: '短线', bars: form.short_bars, total: form.short_bars + form.horizon, purpose: '捕捉快速变化' },
  { key: 'medium_bars', label: '中期', bars: form.medium_bars, total: form.medium_bars + form.horizon, purpose: '确认趋势延续' },
  { key: 'long_bars', label: '长线', bars: form.long_bars, total: form.long_bars + form.horizon, purpose: '复核主要方向' },
])

function strategyNote() {
  return `[策略参数] 短线${form.short_bars}根；中期${form.medium_bars}根；长线${form.long_bars}根；结果${form.horizon}根；分析周期${form.analysis_timeframe}；刷新=监控新命中。`
}
function parseStrategyNote(notes = '') {
  const match = notes.match(/\[策略参数\]\s*短线(\d+)根；中期(\d+)根；长线(\d+)根；结果(\d+)根；(?:分析周期([^；]+)；)?刷新=监控新命中。?/)
  return { short: Number(match?.[1] || 7), medium: Number(match?.[2] || 14), long: Number(match?.[3] || 30), horizon: Number(match?.[4] || 7), analysisTimeframe: match?.[5] || '1d', notes: notes.replace(/\[策略参数\][^\n]*/, '').trim() }
}
function resetForm() {
  editingId.value = null
  Object.assign(form, {
    name: '智能多周期策略', notes: '', coin_id: coins.value[0]?.id || null, join: 'or', indicators: [...smartIndicators], timeframe: '1h', analysis_timeframe: '1d',
    recipient_ids: lifeGroup.value ? [lifeGroup.value.id] : [], enabled: true, allow_push: true, interval_sec: 300,
    short_bars: 7, medium_bars: 14, long_bars: 30, horizon: 7,
  })
}
async function load() {
  loading.value = true
  try {
    const [s, c, ind, rec] = await Promise.all([api.cryptoStrategies(), api.cryptoCoins({ with_quote: false }), api.cryptoIndicators(), api.recipients()])
    rows.value = s.items || []
    coins.value = c.items || []
    catalog.value = ind.items || []
    people.value = rec.items || []
  } catch (e) { ElMessage.error(e.message || '加载失败') } finally { loading.value = false }
}
function openNew() { resetForm(); dialog.value = true }
function applyPreset(preset) {
  resetForm()
  form.name = preset.name
  form.notes = preset.description
  form.timeframe = preset.timeframe
  form.join = preset.join
  form.indicators = [...preset.indicators]
  form.interval_sec = preset.interval_sec
  dialog.value = true
}
function openEdit(row) {
  const parsed = parseStrategyNote(row.notes || '')
  editingId.value = row.id
  Object.assign(form, {
    name: row.name || '', notes: parsed.notes, coin_id: row.coin_id || null, join: row.join || 'or', indicators: [...(row.indicators || [])],
    timeframe: row.timeframe || '1h', analysis_timeframe: parsed.analysisTimeframe, recipient_ids: [...(row.recipient_ids || [])], enabled: !!row.enabled,
    allow_push: !!row.allow_push, interval_sec: row.interval_sec || 300,
    short_bars: parsed.short, medium_bars: parsed.medium, long_bars: parsed.long, horizon: parsed.horizon,
  })
  dialog.value = true
}
async function save() {
  if (!form.coin_id) return ElMessage.warning('请选择币种')
  if (!form.indicators.length) return ElMessage.warning('请至少选一个监控指标')
  if (!(form.short_bars < form.medium_bars && form.medium_bars < form.long_bars)) return ElMessage.warning('切片长度应满足短线 < 中期 < 长线')
  saving.value = true
  try {
    const body = {
      name: form.name.trim() || `${selectedCoin.value?.symbol || ''} 多周期策略`,
      notes: `${strategyNote()}${form.notes.trim() ? `\n${form.notes.trim()}` : ''}`,
      coin_id: form.coin_id, join: form.join, indicators: form.indicators, timeframe: form.timeframe,
      recipient_ids: form.recipient_ids, enabled: form.enabled, allow_push: form.allow_push, interval_sec: form.interval_sec,
    }
    const out = editingId.value ? await api.updateCryptoStrategy(editingId.value, body) : await api.createCryptoStrategy(body)
    ElMessage.success('指标策略已保存')
    if (out?.notify?.ok) ElMessage.success('启用确认已发送到生活管家群')
    else if (out?.notify && !out.notify.ok) ElMessage.warning(out.notify.error || '策略已保存，但确认消息发送失败')
    dialog.value = false
    await load()
  } catch (e) { ElMessage.error(e.message || '保存失败') } finally { saving.value = false }
}
async function remove(row) {
  try {
    await ElMessageBox.confirm(`删除策略「${row.name}」？`, '确认', { type: 'warning' })
    await api.deleteCryptoStrategy(row.id)
    ElMessage.success('已删除')
    await load()
  } catch (e) { if (e !== 'cancel') ElMessage.error(e.message || '删除失败') }
}
async function toggle(row, key) {
  try {
    const out = await api.updateCryptoStrategy(row.id, { [key]: !row[key] })
    await load()
    if (out?.notify?.ok) ElMessage.success('已启用并发送确认消息')
    else if (out?.notify && !out.notify.ok) ElMessage.warning(out.notify.error || '开关已更新，但消息发送失败')
  } catch (e) { ElMessage.error(e.message || '更新失败') }
}
async function check(row) {
  try {
    const out = await api.checkCryptoStrategy(row.id)
    ElMessage.success(out.last_hit ? '当前命中，结论已刷新' : '当前未命中')
    await load()
  } catch (e) { ElMessage.error(e.message || '检查失败') }
}
async function refreshAll() {
  loading.value = true
  try {
    const res = await api.refreshCryptoStrategies()
    rows.value = res.items || []
    ElMessage.success('已刷新全部策略')
  } catch (e) { ElMessage.error(e.message || '刷新失败') } finally { loading.value = false }
}
function conclusion(row) {
  if (row.last_hit === true) return { label: '出现监控信号，立即复核历史形态', tone: 'hit' }
  if (row.last_hit === false) return { label: '暂无新信号，保持原策略', tone: 'quiet' }
  return { label: '等待首次检查', tone: 'pending' }
}
onMounted(load)
</script>

<template>
  <div class="strategy-page">
    <header class="page-intro">
      <div><span class="eyebrow">策略与监控闭环</span><h2>指标策略</h2><p>监控负责发现突发点，短、中、长历史形态负责复核，结论通过后再形成买卖参考。</p></div>
      <el-button v-if="canWrite" type="primary" @click="openNew">新建指标策略</el-button>
    </header>
    <section class="workflow-grid">
      <article><span class="step">1</span><div><h3>策略设置</h3><p>选择币种和历史分析周期；默认日线 7、14、30 根代表 7、14、30 天，结果窗口可选 3、7、14 天。</p></div></article>
      <article><span class="step">2</span><div><h3>智能监控</h3><p>默认每 5 分钟检查超跌、趋势、量能和转弱风险。监控只发现事件，不直接等同买卖信号。</p></div></article>
      <article><span class="step">3</span><div><h3>综合结论</h3><p>命中后复核三阶段方向、历史概率、价格区间和风险条件，输出观察、等待或可执行结论。</p></div></article>
      <article><span class="step">4</span><div><h3>刷新与推送</h3><p>新命中触发结论刷新，重复事件不反复推送；默认发送到 TG-BOT 的生活管家群。</p></div></article>
    </section>
    <section class="smart-default">
      <div><span class="recommended">推荐</span><b>小白智能波段方案</b><p>日线复核 7/14/30 天 · 观察后续 7 天 · 1 小时事件监控 · 每 5 分钟检查 · 命中后刷新 · 生活管家群推送</p></div>
      <el-button v-if="canWrite" @click="applyPreset(monitorPresets[0])">使用默认设置</el-button>
    </section>
    <section class="preset-row">
      <button v-for="preset in monitorPresets.slice(1)" :key="preset.key" type="button" @click="canWrite && applyPreset(preset)"><b>{{ preset.name }}</b><span>{{ preset.timeframe }} · {{ preset.join === 'and' ? '全部满足' : '任一满足' }}</span><p>{{ preset.description }}</p></button>
    </section>
    <div class="action-row"><span>已配置 {{ rows.length }} 个策略</span><el-button :loading="loading" @click="refreshAll">立即检查全部</el-button></div>
    <section class="strategy-list" v-loading="loading">
      <article v-for="row in rows" :key="row.id">
        <div class="strategy-title"><div><b>{{ row.name }}</b><span>{{ row.symbol }} · {{ row.timeframe }} · {{ (row.indicators || []).length }} 个指标</span></div><em :class="conclusion(row).tone">{{ conclusion(row).label }}</em></div>
        <div class="result-line"><div><span>最新价格</span><b>{{ row.last_price || '—' }}</b></div><div><span>最新检查</span><b>{{ row.last_checked_at || '尚未检查' }}</b></div><div><span>刷新规则</span><b>监控新命中</b></div><div><span>推送渠道</span><b>{{ row.allow_push ? '生活管家群' : '未开启' }}</b></div></div>
        <p class="detail">{{ row.last_detail || '首次检查后，这里会显示命中指标与综合结论入口。' }}</p>
        <div class="row-actions"><el-switch :model-value="row.enabled" :disabled="!canWrite" active-text="监听" @change="toggle(row, 'enabled')" /><el-switch :model-value="row.allow_push" :disabled="!canWrite" active-text="推送" @change="toggle(row, 'allow_push')" /><span></span><el-button link type="primary" @click="check(row)">检查</el-button><el-button link type="primary" @click="emit('open-backtest', row.id)">历史信号</el-button><el-button v-if="canWrite" link type="primary" @click="openEdit(row)">编辑</el-button><el-button v-if="canWrite" link type="danger" @click="remove(row)">删除</el-button></div>
      </article>
      <p v-if="!loading && !rows.length" class="empty">还没有策略，建议从“默认智能设置”开始。</p>
    </section>

    <el-dialog v-model="dialog" :title="editingId ? '编辑指标策略' : '新建指标策略'" width="760px" destroy-on-close>
      <div class="editor-section"><b>1. 策略设置</b><span>三个切片都以最新已收盘 K 线为终点，分别复核不同时间尺度。</span></div>
      <el-form label-width="100px">
        <el-form-item label="策略名称"><el-input v-model="form.name" /></el-form-item>
        <el-form-item label="币种"><el-select v-model="form.coin_id" style="width:100%"><el-option v-for="c in coins" :key="c.id" :label="`${c.symbol} · ${c.name}`" :value="c.id" /></el-select></el-form-item>
        <el-form-item label="历史分析周期"><div class="period-field"><el-select v-model="form.analysis_timeframe"><el-option label="15 分钟" value="15m" /><el-option label="1 小时" value="1h" /><el-option label="4 小时" value="4h" /><el-option label="1 天（推荐）" value="1d" /><el-option label="1 周" value="1w" /><el-option label="1 月" value="1M" /></el-select><small>每根 K 线代表的时间。选择“1 天”时，7/14/30 根就是 7/14/30 天，后续 7 根就是随后 7 天。</small></div></el-form-item>
        <el-form-item label="三阶段切片"><div class="stage-editor"><label v-for="item in eventWindows" :key="item.label"><span>{{ item.label }} · {{ item.purpose }}</span><el-input-number v-model="form[item.key]" :min="3" :max="120" /><small>事件总长 {{ item.total }} 根</small></label></div></el-form-item>
        <el-form-item label="预期结果"><el-radio-group v-model="form.horizon"><el-radio-button :value="3">3 根</el-radio-button><el-radio-button :value="7">7 根</el-radio-button><el-radio-button :value="14">14 根</el-radio-button></el-radio-group></el-form-item>
      </el-form>
      <div class="editor-section"><b>2. 智能监控</b><span>推荐配置已按波段策略预设，小白无需修改高级指标。</span></div>
      <el-form label-width="100px">
        <el-form-item label="智能方案"><div class="expert-preset"><b>平衡型事件监控（推荐）</b><p>用 1 小时 K 线捕捉 RSI 超卖反弹/超买回落、MACD 金叉/死叉、ATR 突增和放量。它只负责叫醒策略，最终方向仍由日线三阶段历史复核决定。</p></div></el-form-item>
        <el-form-item label="监控周期"><div class="period-field"><el-select v-model="form.timeframe"><el-option label="15 分钟（更敏感）" value="15m" /><el-option label="1 小时（推荐）" value="1h" /><el-option label="4 小时（更稳健）" value="4h" /><el-option label="1 天（低频）" value="1d" /></el-select><small>决定指标按哪种 K 线计算，与上面的历史分析周期相互独立。</small></div></el-form-item>
        <el-form-item label="触发关系"><el-radio-group v-model="form.join"><el-radio value="or">任一满足（推荐）</el-radio><el-radio value="and">全部满足</el-radio></el-radio-group></el-form-item>
        <el-form-item label="检查频率"><div class="period-field"><el-select v-model="form.interval_sec"><el-option :value="60" label="1 分钟（高频）" /><el-option :value="300" label="5 分钟（推荐）" /><el-option :value="900" label="15 分钟（稳健）" /><el-option :value="1800" label="30 分钟（低频）" /></el-select><small>推荐每 5 分钟检查 1 小时指标。系统只在出现新命中时刷新和推送，不会每 5 分钟重复发消息。</small></div></el-form-item>
        <el-form-item label="高级设置"><details class="advanced-settings" open><summary>查看或修改 · 已选 {{ form.indicators.length }} 个监控指标</summary><div class="indicator-guidance"><b>专家推荐：保留这 6 个事件指标</b><p>RSI 与 MACD 负责发现方向转折，ATR 只识别波动风险，成交量只确认市场参与度。它们用于“任一命中即唤醒复核”，不是 6 票投票，也不会直接相加成涨跌概率。</p><div><span>方向转折 4 项</span><span>波动风险 1 项</span><span>量能确认 1 项</span></div><button type="button" @click="form.indicators = [...smartIndicators]">恢复专家推荐</button></div><div class="selected-summary"><b>当前已选 {{ form.indicators.length }}/6</b><span>{{ selectedIndicatorNames.join('、') || '尚未选择指标' }}</span></div><div class="indicator-picker"><div class="pills"><button v-for="g in groups" :key="g" class="pill" :class="{ active: groupFilter === g }" type="button" @click="groupFilter = g">{{ g }}</button></div><el-checkbox-group v-model="form.indicators" class="ind-grid"><el-checkbox v-for="ind in catalogShown" :key="ind.id" :label="ind.id">{{ ind.name }}</el-checkbox></el-checkbox-group></div></details></el-form-item>
      </el-form>
      <div class="editor-section"><b>3. 结论与刷新</b><span>监控出现新命中后刷新结论；买卖建议仍需通过历史形态和风险门槛。</span></div>
      <div class="fixed-rules"><div><span>结论口径</span><b>监控信号 + 三阶段历史复核 + 价格与风险</b></div><div><span>刷新规则</span><b>监控出现新命中时</b></div></div>
      <div class="editor-section"><b>4. 推送设置</b><span>开启监听或推送后，系统会发送一条启用确认消息。</span></div>
      <el-form label-width="100px"><el-form-item label="推送到"><el-select v-model="form.recipient_ids" multiple style="width:100%"><el-option v-for="p in people" :key="p.id" :label="p.name || `推送人 ${p.id}`" :value="p.id" /></el-select></el-form-item><el-form-item label="开关"><el-checkbox v-model="form.enabled">开启监听</el-checkbox><el-checkbox v-model="form.allow_push">新命中推送结论</el-checkbox></el-form-item><el-form-item label="备注"><el-input v-model="form.notes" type="textarea" :rows="2" /></el-form-item></el-form>
      <template #footer><el-button @click="dialog = false">取消</el-button><el-button type="primary" :loading="saving" @click="save">保存指标策略</el-button></template>
    </el-dialog>
  </div>
</template>

<style scoped>
.strategy-page{display:grid;gap:14px}.page-intro{display:flex;justify-content:space-between;align-items:center;gap:20px;border-bottom:1px solid var(--line);padding:4px 0 16px}.page-intro h2{margin:4px 0;font-size:22px}.page-intro p,.workflow-grid p,.smart-default p,.preset-row p{margin:0;color:var(--muted);line-height:1.6}.eyebrow{color:var(--primary);font-size:11px}.workflow-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));border:1px solid var(--line);background:var(--panel)}.workflow-grid article{display:flex;gap:11px;min-height:112px;padding:16px;border-right:1px solid var(--line)}.workflow-grid article:last-child{border-right:0}.workflow-grid h3{margin:1px 0 7px;font-size:14px}.workflow-grid p{font-size:11px}.step{display:grid;place-items:center;flex:0 0 24px;height:24px;border:1px solid var(--primary);color:var(--primary);font-weight:700}.smart-default{display:flex;justify-content:space-between;align-items:center;gap:16px;padding:14px 16px;border:1px solid #38517c;background:#101827}.smart-default div{display:grid;grid-template-columns:auto auto;gap:5px 9px;align-items:center}.smart-default p{grid-column:1/-1;font-size:11px}.recommended{padding:2px 6px;background:var(--primary);color:white;font-size:10px}.preset-row{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px}.preset-row button{display:grid;grid-template-columns:1fr auto;gap:7px;padding:13px;text-align:left;border:1px solid var(--line);background:var(--panel);color:inherit;cursor:pointer}.preset-row button:hover{border-color:var(--primary)}.preset-row span,.preset-row p{font-size:11px}.preset-row p{grid-column:1/-1}.action-row{display:flex;justify-content:space-between;align-items:center}.action-row span{color:var(--muted);font-size:12px}.strategy-list{display:grid;gap:10px}.strategy-list>article{border:1px solid var(--line);background:var(--panel);padding:15px}.strategy-title{display:flex;justify-content:space-between;gap:12px}.strategy-title>div{display:grid;gap:4px}.strategy-title span,.detail{color:var(--muted);font-size:11px}.strategy-title em{font-style:normal;font-size:11px}.strategy-title em.hit{color:#3ddc97}.strategy-title em.quiet{color:var(--muted)}.strategy-title em.pending{color:#f4c46b}.result-line{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));margin-top:13px;border-block:1px solid var(--line)}.result-line div{display:grid;gap:5px;padding:10px 12px;border-right:1px solid var(--line)}.result-line div:last-child{border-right:0}.result-line span{color:var(--muted);font-size:10px}.result-line b{font-size:12px;overflow-wrap:anywhere}.detail{margin:10px 0}.row-actions{display:flex;align-items:center;gap:12px}.row-actions>span{flex:1}.empty{color:var(--muted);text-align:center;padding:30px}.editor-section{display:flex;align-items:baseline;gap:10px;margin:12px 0;padding-top:12px;border-top:1px solid var(--line)}.editor-section:first-child{margin-top:0;padding-top:0;border-top:0}.editor-section span{color:var(--muted);font-size:11px}.dual-field{display:grid;grid-template-columns:2fr 1fr;gap:10px;width:100%}.stage-editor{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px;width:100%}.stage-editor label{display:grid;gap:6px;padding:10px;border:1px solid var(--line)}.stage-editor span,.stage-editor small{font-size:10px;color:var(--muted)}.indicator-picker{width:100%}.pills{display:flex;flex-wrap:wrap;gap:5px;margin-bottom:8px}.pill{border:1px solid var(--line);background:transparent;color:var(--muted);padding:4px 8px;cursor:pointer}.pill.active{border-color:var(--primary);color:var(--primary)}.ind-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:4px 12px;max-height:180px;overflow:auto;width:100%}.fixed-rules{display:grid;grid-template-columns:1fr 1fr;gap:10px}.fixed-rules div{display:grid;gap:5px;padding:12px;border:1px solid var(--line)}.fixed-rules span{color:var(--muted);font-size:10px}.fixed-rules b{font-size:12px}@media(max-width:960px){.workflow-grid{grid-template-columns:1fr 1fr}.workflow-grid article:nth-child(2){border-right:0}.preset-row,.result-line{grid-template-columns:1fr}.result-line div{border-right:0;border-bottom:1px solid var(--line)}.stage-editor{grid-template-columns:1fr}}
.period-field{display:grid;grid-template-columns:minmax(180px,240px) 1fr;gap:12px;align-items:center;width:100%}.period-field small{color:var(--muted);font-size:11px;line-height:1.55}.expert-preset{padding:12px;border-left:3px solid var(--primary);background:var(--bg-elev);width:100%}.expert-preset p{margin:5px 0 0;color:var(--muted);font-size:11px;line-height:1.6}.advanced-settings{width:100%;border:1px solid var(--line);padding:10px 12px}.advanced-settings summary{cursor:pointer;color:var(--primary);font-size:12px}.advanced-settings[open] summary{margin-bottom:10px}@media(max-width:960px){.period-field{grid-template-columns:1fr}}
.indicator-guidance{display:grid;gap:8px;margin-bottom:12px;padding:12px;border-radius:6px;background:rgba(84,135,220,.1)}.indicator-guidance p{margin:0;color:var(--muted);font-size:12px;line-height:1.65}.indicator-guidance>div{display:flex;flex-wrap:wrap;gap:8px}.indicator-guidance span{padding:4px 8px;border-radius:4px;background:rgba(84,135,220,.12);font-size:11px}.indicator-guidance button{justify-self:start;border:0;background:transparent;color:var(--primary);cursor:pointer}.selected-summary{display:grid;gap:4px;margin-bottom:12px;padding:10px 12px;border-radius:6px;background:rgba(61,220,151,.07)}.selected-summary span{color:var(--muted);font-size:12px;line-height:1.5}.ind-grid :deep(.el-checkbox){min-height:30px}.ind-grid :deep(.el-checkbox__label){font-size:13px}
</style>
