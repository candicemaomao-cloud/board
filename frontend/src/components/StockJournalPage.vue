<script setup>
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '../api'
import { can } from '../auth'
import { isoDate } from '../format'
import { hasJournalCharts } from '../journalParse'
import JournalChartsDialog from './JournalChartsDialog.vue'

const STANCES = ['多', '空', '观望']

const rows = ref([])
const loading = ref(false)
const saving = ref(false)
const exporting = ref(false)
const dialog = ref(false)
const editingId = ref(null)
const chartsOpen = ref(false)
const chartsRow = ref(null)

function openCharts(row) {
  chartsRow.value = row
  chartsOpen.value = true
}

const form = reactive({
  log_date: isoDate(),
  stance: '观望',
  content: '',
})

function resetForm() {
  editingId.value = null
  form.log_date = isoDate()
  form.stance = '观望'
  form.content = ''
}

function stanceTone(stance) {
  if (stance === '多') return 'up'
  if (stance === '空') return 'down'
  return 'sub'
}

function sortedRows() {
  return [...(rows.value || [])].sort((a, b) => String(b.log_date || '').localeCompare(String(a.log_date || '')))
}

function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}

function exportStamp() {
  const d = new Date()
  const p = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}`
}

function xmlEscape(s) {
  return String(s ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

function buildTxt(list) {
  const blocks = list.map((r) => {
    const head = `======== ${r.log_date || '—'} · ${r.stance || '观望'} ========`
    return `${head}\n${(r.content || '').trim() || '（空）'}`
  })
  return `股票日志导出\n共 ${list.length} 条\n\n${blocks.join('\n\n')}\n`
}

function buildExcelXml(list) {
  const cells = (vals) =>
    vals
      .map((v) => `<Cell><Data ss:Type="String">${xmlEscape(v)}</Data></Cell>`)
      .join('')
  const header = `<Row>${cells(['日期', '建议', '行情记录'])}</Row>`
  const body = list
    .map((r) => `<Row>${cells([r.log_date || '', r.stance || '', r.content || ''])}</Row>`)
    .join('')
  return `<?xml version="1.0" encoding="UTF-8"?>
<?mso-application progid="Excel.Sheet"?>
<Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet"
 xmlns:ss="urn:schemas-microsoft-com:office:spreadsheet">
 <Worksheet ss:Name="股票日志">
  <Table>
${header}
${body}
  </Table>
 </Worksheet>
</Workbook>`
}

async function exportAs(fmt) {
  const list = sortedRows()
  if (!list.length) {
    ElMessage.warning('没有可导出的日志')
    return
  }
  exporting.value = true
  try {
    const stamp = exportStamp()
    if (fmt === 'txt') {
      const text = buildTxt(list)
      downloadBlob(new Blob([text], { type: 'text/plain;charset=utf-8' }), `股票日志_${stamp}.txt`)
    } else {
      const xml = buildExcelXml(list)
      downloadBlob(
        new Blob([xml], { type: 'application/vnd.ms-excel;charset=utf-8' }),
        `股票日志_${stamp}.xls`,
      )
    }
    ElMessage.success(fmt === 'txt' ? '已导出 TXT' : '已导出 Excel')
  } catch (e) {
    ElMessage.error(e.message || '导出失败')
  } finally {
    exporting.value = false
  }
}

async function load() {
  loading.value = true
  try {
    const data = await api.stockJournals()
    rows.value = data.items || []
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
  form.log_date = row.log_date || isoDate()
  form.stance = row.stance || '观望'
  form.content = row.content || ''
  dialog.value = true
}

async function save() {
  if (!form.log_date) {
    ElMessage.warning('请选择日期')
    return
  }
  saving.value = true
  try {
    const body = {
      log_date: form.log_date,
      stance: form.stance,
      content: form.content.trim() || null,
    }
    if (editingId.value) {
      await api.updateStockJournal(editingId.value, body)
      ElMessage.success('已更新')
    } else {
      await api.createStockJournal(body)
      ElMessage.success('已添加')
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
    await ElMessageBox.confirm(`删除 ${row.log_date} 的日志？`, '确认', { type: 'warning' })
    await api.deleteStockJournal(row.id)
    ElMessage.success('已删除')
    await load()
  } catch (e) {
    if (e !== 'cancel') ElMessage.error(e.message || '删除失败')
  }
}

onMounted(load)
</script>

<template>
  <p class="goal-lead">
    按日记当天行情与建议（空 / 多 / 观望）。存在本机数据库，按登录用户隔离，后续可直接给 LLM 当上下文。
  </p>
  <div class="range-row">
    <el-button v-if="can('btn.stock_journal.write')" type="primary" @click="openNew">写日志</el-button>
    <el-button :loading="loading" @click="load">刷新</el-button>
    <el-dropdown :disabled="!rows.length || exporting" @command="exportAs">
      <el-button :loading="exporting">
        导出
        <span style="margin-left: 4px">▾</span>
      </el-button>
      <template #dropdown>
        <el-dropdown-menu>
          <el-dropdown-item command="txt">导出 TXT</el-dropdown-item>
          <el-dropdown-item command="excel">导出 Excel</el-dropdown-item>
        </el-dropdown-menu>
      </template>
    </el-dropdown>
  </div>

  <el-table :data="rows" v-loading="loading" style="margin-top: 12px" stripe>
    <el-table-column prop="log_date" label="日期" width="120" />
    <el-table-column prop="stance" label="建议" width="100">
      <template #default="{ row }">
        <span :class="stanceTone(row.stance)">{{ row.stance }}</span>
      </template>
    </el-table-column>
    <el-table-column prop="content" label="行情记录" min-width="320" show-overflow-tooltip />
    <el-table-column label="操作" width="180" fixed="right">
      <template #default="{ row }">
        <el-button v-if="hasJournalCharts(row.content)" link type="success" @click="openCharts(row)">图表</el-button>
        <el-button v-if="can('btn.stock_journal.write')" link type="primary" @click="openEdit(row)">改</el-button>
        <el-button v-if="can('btn.stock_journal.write')" link type="danger" @click="remove(row)">删</el-button>
      </template>
    </el-table-column>
  </el-table>
  <p class="sub" v-if="!loading && !rows.length" style="margin-top: 12px">还没有日志，点上方「写日志」。</p>

  <JournalChartsDialog v-model="chartsOpen" :row="chartsRow" />

  <el-dialog v-model="dialog" :title="editingId ? '改日志' : '写日志'" width="560px" destroy-on-close>
    <el-form label-width="72px">
      <el-form-item label="日期">
        <el-date-picker v-model="form.log_date" type="date" value-format="YYYY-MM-DD" style="width: 100%" />
      </el-form-item>
      <el-form-item label="建议">
        <el-radio-group v-model="form.stance">
          <el-radio v-for="s in STANCES" :key="s" :value="s">{{ s }}</el-radio>
        </el-radio-group>
      </el-form-item>
      <el-form-item label="行情">
        <el-input
          v-model="form.content"
          type="textarea"
          :rows="8"
          placeholder="今天大盘/板块/情绪怎么走，为什么建议空/多/观望…"
        />
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="dialog = false">取消</el-button>
      <el-button type="primary" :loading="saving" @click="save">保存</el-button>
    </template>
  </el-dialog>
</template>
