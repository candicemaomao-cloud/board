<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from '../api'

const props = defineProps({
  scope: { type: String, required: true },
})

const people = ref([])
const push = ref(null)
const pushForm = ref({ recipient_ids: [], interval_sec: 300, min_score: 6, x_accounts: '' })
const pushSaving = ref(false)
const pushRunning = ref(false)
const pushTesting = ref(false)
const candidates = ref([])
const scanThreshold = ref(null)

const intervalOptions = [
  { value: 180, label: '3 分钟' },
  { value: 300, label: '5 分钟' },
  { value: 600, label: '10 分钟' },
  { value: 900, label: '15 分钟' },
]

const sourceList = computed(() => Object.entries(push.value?.sources || {}).map(([name, v]) => ({ name, ...v })))

function applyPush(cfg) {
  push.value = cfg
  pushForm.value = {
    recipient_ids: [...(cfg.recipient_ids || [])],
    interval_sec: cfg.interval_sec || 300,
    min_score: cfg.min_score || 6,
    x_accounts: (cfg.x_accounts || []).join(', '),
  }
}

function fmtTime(value) {
  if (!value) return '—'
  const d = typeof value === 'number' ? new Date(value * 1000) : new Date(value)
  return d.toLocaleString('zh-CN', { hour12: false, month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
}

async function loadPush() {
  try {
    const [cfg, rec] = await Promise.all([api.newsPush(props.scope), api.recipients()])
    applyPush(cfg)
    people.value = rec.items || []
  } catch (e) {
    ElMessage.error(e.message || '推送配置加载失败')
  }
}

async function savePush(extra = {}, { silent = false } = {}) {
  pushSaving.value = true
  try {
    applyPush(await api.saveNewsPush(props.scope, { ...pushForm.value, ...extra }))
    if (!silent) ElMessage.success('已保存')
    return true
  } catch (e) {
    ElMessage.error(e.message || '保存失败')
    if (push.value) push.value = { ...push.value }
    return false
  } finally {
    pushSaving.value = false
  }
}

function formDirty() {
  const saved = push.value || {}
  const a = [...(saved.recipient_ids || [])].sort().join(',')
  const b = [...(pushForm.value.recipient_ids || [])].sort().join(',')
  return (
    a !== b ||
    saved.interval_sec !== pushForm.value.interval_sec ||
    saved.min_score !== pushForm.value.min_score ||
    (saved.x_accounts || []).join(', ') !== pushForm.value.x_accounts.trim()
  )
}

async function togglePolling(on) {
  await savePush({ enabled: on })
}

async function runScan(doPush) {
  if (formDirty() && !(await savePush({}, { silent: true }))) return
  pushRunning.value = true
  try {
    const res = await api.runNewsPush(props.scope, doPush)
    applyPush(res.config)
    candidates.value = res.candidates || []
    scanThreshold.value = res.threshold
    if (doPush) {
      const n = (res.pushed || []).length
      ElMessage[n ? 'success' : 'info'](n ? `已推送 ${n} 条大新闻` : '暂无新的大新闻')
    }
  } catch (e) {
    ElMessage.error(e.message || '扫描失败')
  } finally {
    pushRunning.value = false
  }
}

async function testPush() {
  if (!pushForm.value.recipient_ids.length) {
    ElMessage.warning('请先选推送人')
    return
  }
  pushTesting.value = true
  try {
    if (formDirty()) await savePush({}, { silent: true })
    const res = await api.testNewsPush(props.scope, { recipient_ids: pushForm.value.recipient_ids })
    if (res.ok) ElMessage.success(`测试已发送：${(res.channels || []).join('、')}`)
    else ElMessage.error(res.error || '测试发送失败')
  } catch (e) {
    ElMessage.error(e.message || '测试发送失败')
  } finally {
    pushTesting.value = false
  }
}

onMounted(loadPush)
</script>

<template>
<section class="panel push-panel" style="margin-top: 12px" v-if="push">
  <div class="push-head">
    <div>
      <h2 style="margin: 0">{{ scope === 'stock' ? '美股' : '币圈' }}重大新闻推送</h2>
      <p class="sub" style="margin: 4px 0 0">
        定时扫描 X、{{ (push.feeds || []).join('、') }} 近 90 分钟的新闻，按关键词、金额、涨跌幅、多源同报打分<template v-if="scope === 'stock'">（标题提到「股票列表」里的代码额外加分）</template>，达到阈值就推给所选推送人（Telegram 等），同一事件只推一次。
      </p>
    </div>
    <div class="push-switch">
      <span class="sub">轮询</span>
      <el-switch
        :model-value="push.enabled"
        :loading="pushSaving"
        active-text="开"
        inactive-text="关"
        inline-prompt
        @change="togglePolling"
      />
    </div>
  </div>

  <el-form label-width="84px" class="push-form" @submit.prevent>
    <el-form-item label="推送人">
      <el-select v-model="pushForm.recipient_ids" multiple clearable placeholder="选择要推送的人" style="width: 100%">
        <el-option v-for="p in people" :key="p.id" :label="`${p.name}${p.channels?.length ? '（' + p.channels.join('/') + '）' : '（未就绪）'}`" :value="p.id" />
      </el-select>
    </el-form-item>
    <el-form-item label="频率">
      <el-select v-model="pushForm.interval_sec" style="width: 140px">
        <el-option v-for="o in intervalOptions" :key="o.value" :label="o.label" :value="o.value" />
      </el-select>
      <span class="sub" style="margin: 0 8px 0 16px">灵敏度</span>
      <el-select v-model="pushForm.min_score" style="width: 180px">
        <el-option v-for="o in push.score_levels" :key="o.value" :label="`${o.label}（≥${o.value} 分）`" :value="o.value" />
      </el-select>
    </el-form-item>
    <el-form-item label="X 账号">
      <el-input
        v-model="pushForm.x_accounts"
        type="textarea"
        :rows="2"
        placeholder="账号用逗号分隔；也可直接填 RSSHub 等完整 RSS 地址"
      />
    </el-form-item>
    <el-form-item>
      <el-button type="primary" :loading="pushSaving" @click="savePush()">保存设置</el-button>
      <el-button :loading="pushTesting" @click="testPush">发测试消息</el-button>
      <el-button :loading="pushRunning" @click="runScan(false)">预览扫描</el-button>
      <el-button type="warning" plain :loading="pushRunning" @click="runScan(true)">立即扫描并推送</el-button>
    </el-form-item>
  </el-form>

  <div class="push-status">
    <span>状态：<b :class="push.enabled ? 'up' : 'muted'">{{ push.enabled ? '轮询中' : '已关闭' }}</b></span>
    <span class="sub">上次扫描 {{ fmtTime(push.last_checked_at) }}</span>
    <span class="sub" v-if="push.enabled && push.next_check_ts">下次约 {{ fmtTime(push.next_check_ts) }}</span>
    <span class="down" v-if="push.last_error">{{ push.last_error }}</span>
  </div>
  <div class="source-chips" v-if="sourceList.length">
    <span v-for="s in sourceList" :key="s.name" class="source-chip" :class="s.ok ? 'ok' : 'bad'" :title="s.error || ''">
      {{ s.name }} {{ s.ok ? s.count : '×' }}
    </span>
  </div>

  <template v-if="candidates.length">
    <h3 class="push-sub">本次扫描（阈值 ≥{{ scanThreshold }} 分）</h3>
    <el-table :data="candidates" size="small" stripe>
      <el-table-column label="分" width="56">
        <template #default="{ row }">
          <b :class="row.score >= scanThreshold ? 'down' : 'muted'">{{ row.score }}</b>
        </template>
      </el-table-column>
      <el-table-column label="标题" min-width="300">
        <template #default="{ row }">
          <a v-if="row.url" :href="row.url" target="_blank" rel="noreferrer" class="news-link">{{ row.title_en }}</a>
          <span v-else>{{ row.title_en }}</span>
          <span v-if="row.pushed_before" class="tag-chip">已推过</span>
          <div class="sub" v-if="row.reasons?.length">命中：{{ row.reasons.join('、') }}</div>
        </template>
      </el-table-column>
      <el-table-column label="来源" width="180">
        <template #default="{ row }">{{ (row.sources || []).join('、') }}</template>
      </el-table-column>
      <el-table-column label="时间" width="110">
        <template #default="{ row }">{{ fmtTime(row.ts) }}</template>
      </el-table-column>
    </el-table>
  </template>

  <h3 class="push-sub">推送记录</h3>
  <el-table v-if="push.history?.length" :data="push.history" size="small" stripe>
    <el-table-column label="推送时间" width="110">
      <template #default="{ row }">{{ fmtTime(row.at) }}</template>
    </el-table-column>
    <el-table-column label="新闻" min-width="300">
      <template #default="{ row }">
        <a v-if="row.url" :href="row.url" target="_blank" rel="noreferrer" class="news-link">{{ row.title || row.title_en }}</a>
        <span v-else>{{ row.title || row.title_en }}</span>
        <div class="sub" v-if="row.title_en && row.title_en !== row.title">{{ row.title_en }}</div>
      </template>
    </el-table-column>
    <el-table-column label="分" width="56" prop="score" />
    <el-table-column label="结果" width="140">
      <template #default="{ row }">
        <span v-if="row.ok" class="up">已送达</span>
        <span v-else class="down" :title="row.error">失败</span>
      </template>
    </el-table-column>
  </el-table>
  <p v-else class="sub">还没有推送记录。</p>
</section>
</template>

<style scoped>
.news-link {
  color: var(--accent, #7aa2ff);
  text-decoration: none;
}
.news-link:hover {
  text-decoration: underline;
}
.tag-chip {
  display: inline-block;
  margin: 0 4px 4px 6px;
  padding: 1px 6px;
  font-size: 12px;
  border: 1px solid rgba(148, 176, 210, 0.25);
  border-radius: 4px;
  color: #8b9bb0;
}
.muted {
  color: #8b9bb0;
}
.push-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 16px;
  margin-bottom: 12px;
}
.push-switch {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}
.push-form {
  max-width: 820px;
}
.push-status {
  display: flex;
  flex-wrap: wrap;
  gap: 14px;
  align-items: baseline;
  margin: 4px 0 8px;
}
.source-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 8px;
}
.source-chip {
  font-size: 12px;
  padding: 2px 8px;
  border-radius: 4px;
  border: 1px solid rgba(148, 176, 210, 0.25);
}
.source-chip.ok {
  color: #5fd4ae;
  border-color: rgba(95, 212, 174, 0.35);
}
.source-chip.bad {
  color: #ff6b7a;
  border-color: rgba(255, 107, 122, 0.35);
}
.push-sub {
  margin: 14px 0 8px;
  font-size: 14px;
}
</style>
