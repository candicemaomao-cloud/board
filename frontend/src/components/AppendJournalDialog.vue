<script setup>
import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from '../api'
import { can } from '../auth'
import { isoDate } from '../format'

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  /** 要追加进日志的正文 */
  snippet: { type: String, default: '' },
  /** 默认选中的日期 YYYY-MM-DD */
  defaultDate: { type: String, default: '' },
  title: { type: String, default: '加入日志' },
})

const emit = defineEmits(['update:modelValue', 'done'])

const logDate = ref(isoDate())
const saving = ref(false)
const loadingExisting = ref(false)
const existing = ref(null)

const visible = computed({
  get: () => props.modelValue,
  set: (v) => emit('update:modelValue', v),
})

const preview = computed(() => (props.snippet || '').trim())
const canWrite = computed(() => can('btn.stock_journal.write'))

watch(
  () => props.modelValue,
  async (open) => {
    if (!open) return
    logDate.value = props.defaultDate || isoDate()
    await loadExisting()
  },
)

watch(logDate, () => {
  if (props.modelValue) loadExisting()
})

async function loadExisting() {
  existing.value = null
  if (!logDate.value) return
  loadingExisting.value = true
  try {
    const data = await api.stockJournals({
      date_from: logDate.value,
      date_to: logDate.value,
    })
    existing.value = (data.items || [])[0] || null
  } catch {
    existing.value = null
  } finally {
    loadingExisting.value = false
  }
}

async function save() {
  if (!canWrite.value) {
    ElMessage.warning('没有写日志权限')
    return
  }
  if (!logDate.value) {
    ElMessage.warning('请选择日期')
    return
  }
  if (!preview.value) {
    ElMessage.warning('没有可写入的内容')
    return
  }
  saving.value = true
  try {
    const row = await api.appendStockJournal({
      log_date: logDate.value,
      snippet: preview.value,
    })
    ElMessage.success(existing.value ? `已追加到 ${logDate.value}` : `已新建 ${logDate.value} 日志`)
    visible.value = false
    emit('done', row)
  } catch (e) {
    ElMessage.error(e.message || '写入失败')
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <el-dialog v-model="visible" :title="title" width="560px" destroy-on-close>
    <el-form label-width="72px">
      <el-form-item label="日期">
        <el-date-picker
          v-model="logDate"
          type="date"
          value-format="YYYY-MM-DD"
          style="width: 100%"
          :clearable="false"
        />
      </el-form-item>
      <el-form-item label="将写入">
        <pre class="journal-snippet">{{ preview || '（空）' }}</pre>
      </el-form-item>
      <el-form-item label="当日已有" v-if="loadingExisting || existing">
        <p class="sub" v-if="loadingExisting" style="margin: 0">查询中…</p>
        <template v-else-if="existing">
          <p class="sub" style="margin: 0 0 6px">
            建议：{{ existing.stance }} · 保存后会追加在原文后面
          </p>
          <pre class="journal-snippet muted">{{ existing.content || '（原文为空）' }}</pre>
        </template>
      </el-form-item>
      <p class="sub" v-else style="margin: 0 0 8px 72px">该日还没有日志，保存会新建一条（建议默认「观望」）。</p>
    </el-form>
    <template #footer>
      <el-button @click="visible = false">取消</el-button>
      <el-button type="primary" :loading="saving" :disabled="!canWrite || !preview" @click="save">
        写入日志
      </el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.journal-snippet {
  margin: 0;
  width: 100%;
  max-height: 220px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-word;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 12px;
  line-height: 1.5;
  padding: 10px 12px;
  border-radius: 8px;
  background: rgba(148, 176, 210, 0.08);
  border: 1px solid rgba(148, 176, 210, 0.18);
  color: var(--text, #e9f0f7);
}
.journal-snippet.muted {
  opacity: 0.75;
  max-height: 140px;
}
</style>
