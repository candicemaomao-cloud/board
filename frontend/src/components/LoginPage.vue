<script setup>
import { reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from '../api'
import { setSession } from '../auth'

const emit = defineEmits(['ok'])
const loading = ref(false)
const form = reactive({ username: 'admin', password: '' })

async function submit() {
  if (!form.username.trim() || !form.password) {
    ElMessage.warning('请输入用户名和密码')
    return
  }
  loading.value = true
  try {
    const data = await api.login({ username: form.username.trim(), password: form.password })
    setSession(data.access_token, data.user)
    ElMessage.success(`欢迎，${data.user.username}`)
    emit('ok', data.user)
  } catch (e) {
    ElMessage.error(e.message || '登录失败')
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="login-wrap">
    <el-card class="login-card" shadow="hover">
      <h1>P&L Board</h1>
      <p class="sub">登录后按权限访问菜单与操作</p>
      <el-form label-position="top" @submit.prevent="submit">
        <el-form-item label="用户名">
          <el-input v-model="form.username" autocomplete="username" @keydown.enter="submit" />
        </el-form-item>
        <el-form-item label="密码">
          <el-input
            v-model="form.password"
            type="password"
            show-password
            autocomplete="current-password"
            @keydown.enter="submit"
          />
        </el-form-item>
        <el-button type="primary" :loading="loading" style="width: 100%" @click="submit">登录</el-button>
      </el-form>
      <p class="hint">默认管理员：admin / 123456</p>
    </el-card>
  </div>
</template>

<style scoped>
.login-wrap {
  min-height: 100vh;
  display: grid;
  place-items: center;
  background: radial-gradient(1200px 600px at 20% 0%, #1a2740, #0b1220 55%);
  padding: 24px;
}
.login-card {
  width: min(400px, 100%);
  border-radius: 12px;
}
.login-card h1 {
  margin: 0 0 4px;
  font-size: 22px;
}
.sub {
  margin: 0 0 16px;
  color: var(--el-text-color-secondary);
  font-size: 13px;
}
.hint {
  margin: 14px 0 0;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
</style>
