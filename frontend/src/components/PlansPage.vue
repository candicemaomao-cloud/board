<script setup>
import { computed, onMounted, reactive, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '../api'
import { can } from '../auth'
import { isoDate } from '../format'

const props = defineProps({
  kind: { type: String, required: true }, // day | week | month
})

const KIND_LABEL = { day: '日计划', week: '周计划', month: '月计划' }
const label = computed(() => KIND_LABEL[props.kind] || '计划')

const rows = ref([])
const loading = ref(false)
const saving = ref(false)
const dialog = ref(false)
const editingId = ref(null)
const form = reactive({
  title: '',
  content: '',
  plan_date: isoDate(),
})

watch(
  () => props.kind,
  () => load(),
)

function resetForm() {
  editingId.value = null
  form.title = ''
  form.content = ''
  form.plan_date = isoDate()
}

async function load() {
  loading.value = true
  try {
    const data = await api.plans({ kind: props.kind })
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
  form.title = row.title || ''
  form.content = row.content || ''
  form.plan_date = row.plan_date || isoDate()
  dialog.value = true
}

async function save() {
  if (!form.title.trim()) {
    ElMessage.warning('请填写标题')
    return
  }
  saving.value = true
  try {
    const body = {
      kind: props.kind,
      title: form.title.trim(),
      content: form.content.trim() || null,
      plan_date: form.plan_date || null,
    }
    if (editingId.value) {
      await api.updatePlan(editingId.value, body)
      ElMessage.success('已更新')
    } else {
      await api.createPlan(body)
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
    await ElMessageBox.confirm(`删除「${row.title}」？`, '确认', { type: 'warning' })
    await api.deletePlan(row.id)
    ElMessage.success('已删除')
    await load()
  } catch (e) {
    if (e !== 'cancel') ElMessage.error(e.message || '删除失败')
  }
}

onMounted(load)
</script>

<template>
  <p class="goal-lead">{{ label }}：按条记录，可增删改查。只属于当前登录用户。</p>
  <div class="range-row">
    <el-button v-if="can('btn.plans.write')" type="primary" @click="openNew">新增{{ label }}</el-button>
    <el-button :loading="loading" @click="load">刷新</el-button>
  </div>

  <el-table :data="rows" v-loading="loading" style="margin-top: 12px" stripe>
    <el-table-column prop="plan_date" label="日期" width="120">
      <template #default="{ row }">{{ row.plan_date || '—' }}</template>
    </el-table-column>
    <el-table-column prop="title" label="标题" min-width="160" />
    <el-table-column prop="content" label="内容" min-width="240" show-overflow-tooltip />
    <el-table-column label="操作" width="140" fixed="right">
      <template #default="{ row }">
        <el-button v-if="can('btn.plans.write')" link type="primary" @click="openEdit(row)">改</el-button>
        <el-button v-if="can('btn.plans.write')" link type="danger" @click="remove(row)">删</el-button>
      </template>
    </el-table-column>
  </el-table>
  <p class="sub" v-if="!loading && !rows.length" style="margin-top: 12px">还没有{{ label }}，点上方新增。</p>

  <el-dialog v-model="dialog" :title="editingId ? `改${label}` : `新增${label}`" width="560px" destroy-on-close>
    <el-form label-width="72px">
      <el-form-item label="日期">
        <el-date-picker v-model="form.plan_date" type="date" value-format="YYYY-MM-DD" style="width: 100%" />
      </el-form-item>
      <el-form-item label="标题">
        <el-input v-model="form.title" placeholder="例如：本周只做突破" />
      </el-form-item>
      <el-form-item label="内容">
        <el-input
          v-model="form.content"
          type="textarea"
          :rows="8"
          placeholder="偏向 / 只做这些 / 不碰 / 最多亏…"
        />
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="dialog = false">取消</el-button>
      <el-button type="primary" :loading="saving" @click="save">保存</el-button>
    </template>
  </el-dialog>
</template>
