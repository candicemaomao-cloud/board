<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { api } from '../api'

const rows = ref([])
const catalog = ref([])
const loading = ref(false)
const saving = ref(false)
const dialog = ref(false)
const editingId = ref(null)

const form = reactive({
  username: '',
  password: '',
  total_amount: 0,
  role: 'user',
  permissions: [],
  is_active: true,
})

const menuPerms = computed(() => catalog.value.filter((p) => p.group === '菜单'))
const buttonPerms = computed(() => catalog.value.filter((p) => p.group === '按钮'))

function resetForm() {
  editingId.value = null
  form.username = ''
  form.password = ''
  form.total_amount = 0
  form.role = 'user'
  form.permissions = []
  form.is_active = true
}

async function load() {
  loading.value = true
  try {
    const [users, cat] = await Promise.all([api.listUsers(), api.permissionCatalog()])
    rows.value = users.items || []
    catalog.value = cat.items || []
    if (!form.permissions.length && cat.default_user) {
      form.permissions = [...cat.default_user]
    }
  } catch (e) {
    ElMessage.error(e.message || '加载失败')
  } finally {
    loading.value = false
  }
}

function openNew() {
  resetForm()
  const defaults = catalog.value.length
    ? (catalog.value.filter((p) => p.group === '菜单').map((p) => p.code).filter((c) => c !== 'menu.users'))
    : []
  form.permissions = defaults.length ? defaults : []
  dialog.value = true
}

function openEdit(row) {
  editingId.value = row.id
  form.username = row.username
  form.password = ''
  form.total_amount = Number(row.total_amount) || 0
  form.role = row.role
  form.permissions = [...(row.permissions || [])]
  form.is_active = !!row.is_active
  dialog.value = true
}

async function save() {
  saving.value = true
  try {
    if (editingId.value) {
      const body = {
        total_amount: Number(form.total_amount) || 0,
        permissions: form.role === 'admin' ? undefined : form.permissions,
        role: form.role,
        is_active: form.is_active,
      }
      if (form.password) body.password = form.password
      await api.updateUser(editingId.value, body)
      ElMessage.success('已更新')
    } else {
      if (!form.username.trim() || !form.password) {
        ElMessage.warning('请填用户名和密码')
        return
      }
      await api.createUser({
        username: form.username.trim(),
        password: form.password,
        total_amount: Number(form.total_amount) || 0,
        permissions: form.permissions,
        role: form.role,
      })
      ElMessage.success('已创建用户')
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
    await ElMessageBox.confirm(`删除用户「${row.username}」？`, '确认', { type: 'warning' })
    await api.deleteUser(row.id)
    ElMessage.success('已删除')
    await load()
  } catch (e) {
    if (e !== 'cancel') ElMessage.error(e.message || '删除失败')
  }
}

function checkAll(group) {
  const codes = (group === '菜单' ? menuPerms.value : buttonPerms.value).map((p) => p.code)
  const set = new Set(form.permissions)
  codes.forEach((c) => set.add(c))
  form.permissions = [...set]
}

function clearGroup(group) {
  const drop = new Set((group === '菜单' ? menuPerms.value : buttonPerms.value).map((p) => p.code))
  form.permissions = form.permissions.filter((c) => !drop.has(c))
}

onMounted(load)
</script>

<template>
  <p class="goal-lead">用户与权限：可新增用户、修改总金额、勾选菜单/按钮权限。现有持仓已归属 admin。</p>
  <div class="range-row">
    <el-button type="primary" @click="openNew">新增用户</el-button>
    <el-button :loading="loading" @click="load">刷新</el-button>
  </div>

  <el-table :data="rows" v-loading="loading" style="margin-top: 12px" stripe>
    <el-table-column prop="username" label="用户名" width="120" />
    <el-table-column prop="role" label="角色" width="90" />
    <el-table-column label="总金额" width="140">
      <template #default="{ row }">{{ Number(row.total_amount || 0).toLocaleString() }}</template>
    </el-table-column>
    <el-table-column label="权限数" width="90">
      <template #default="{ row }">{{ (row.permissions || []).length }}</template>
    </el-table-column>
    <el-table-column label="状态" width="90">
      <template #default="{ row }">
        <el-tag :type="row.is_active ? 'success' : 'info'" size="small">
          {{ row.is_active ? '启用' : '停用' }}
        </el-tag>
      </template>
    </el-table-column>
    <el-table-column label="操作" min-width="160">
      <template #default="{ row }">
        <el-button link type="primary" @click="openEdit(row)">编辑</el-button>
        <el-button link type="danger" :disabled="row.username === 'admin'" @click="remove(row)">删除</el-button>
      </template>
    </el-table-column>
  </el-table>

  <el-dialog v-model="dialog" :title="editingId ? '编辑用户' : '新增用户'" width="640px" destroy-on-close>
    <el-form label-width="88px">
      <el-form-item label="用户名">
        <el-input v-model="form.username" :disabled="!!editingId" />
      </el-form-item>
      <el-form-item :label="editingId ? '新密码' : '密码'">
        <el-input v-model="form.password" type="password" show-password :placeholder="editingId ? '不改留空' : ''" />
      </el-form-item>
      <el-form-item label="总金额">
        <el-input-number v-model="form.total_amount" :min="0" :step="1000" controls-position="right" style="width: 100%" />
      </el-form-item>
      <el-form-item label="角色">
        <el-select v-model="form.role" style="width: 100%">
          <el-option label="普通用户" value="user" />
          <el-option label="管理员" value="admin" />
        </el-select>
      </el-form-item>
      <el-form-item v-if="editingId" label="状态">
        <el-switch v-model="form.is_active" active-text="启用" inactive-text="停用" />
      </el-form-item>
      <el-form-item v-if="form.role !== 'admin'" label="菜单权限">
        <div style="width: 100%">
          <div style="margin-bottom: 8px">
            <el-button size="small" @click="checkAll('菜单')">全选</el-button>
            <el-button size="small" @click="clearGroup('菜单')">清空</el-button>
          </div>
          <el-checkbox-group v-model="form.permissions">
            <el-checkbox v-for="p in menuPerms" :key="p.code" :label="p.code" :value="p.code">
              {{ p.label }}
            </el-checkbox>
          </el-checkbox-group>
        </div>
      </el-form-item>
      <el-form-item v-if="form.role !== 'admin'" label="按钮权限">
        <div style="width: 100%">
          <div style="margin-bottom: 8px">
            <el-button size="small" @click="checkAll('按钮')">全选</el-button>
            <el-button size="small" @click="clearGroup('按钮')">清空</el-button>
          </div>
          <el-checkbox-group v-model="form.permissions">
            <el-checkbox v-for="p in buttonPerms" :key="p.code" :label="p.code" :value="p.code">
              {{ p.label }}
            </el-checkbox>
          </el-checkbox-group>
        </div>
      </el-form-item>
      <p v-else class="sub">管理员拥有全部菜单与按钮权限。</p>
    </el-form>
    <template #footer>
      <el-button @click="dialog = false">取消</el-button>
      <el-button type="primary" :loading="saving" @click="save">保存</el-button>
    </template>
  </el-dialog>
</template>
