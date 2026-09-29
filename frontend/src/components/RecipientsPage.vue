<script setup>
import { computed, onMounted, reactive, ref } from 'vue'
import { api } from '../api'

const rows = ref([])
const search = ref('')
const screen = ref('list')
const editingId = ref(null)
const loading = ref(false)
const saving = ref(false)
const testing = ref(false)
const error = ref('')
const ok = ref('')
const listOk = ref('')
const listError = ref('')

const form = reactive({
  name: '',
  notes: '',
  telegram: false,
  whatsapp: false,
  wx: false,
  telegram_bot_token: '',
  telegram_chat_id: '',
  whatsapp_phone: '',
  whatsapp_apikey: '',
  wx_webhook: '',
  wx_sendkey: '',
  has_telegram_token: false,
  telegram_token_ok: true,
  has_whatsapp_apikey: false,
  has_wx_webhook: false,
  has_wx_sendkey: false,
})

/** 密钥字段：只有键盘输入/粘贴才算改过（不用 input，避免自动填充误标脏） */
const secretDirty = reactive({
  telegram_bot_token: false,
  whatsapp_apikey: false,
  wx_webhook: false,
  wx_sendkey: false,
})

function markSecretDirty(key) {
  secretDirty[key] = true
}

function unlockSecret(event) {
  const el = event?.target
  if (el && el.readOnly) {
    el.readOnly = false
    // 清空可能被自动填入的垃圾，避免误提交
    const key = el.dataset?.secretKey
    if (key && form[key] && !secretDirty[key]) {
      form[key] = ''
    }
  }
}

const filtered = computed(() => {
  const q = search.value.trim()
  if (!q) return rows.value
  return rows.value.filter((row) => String(row.name || '').toLowerCase().includes(q.toLowerCase()))
})

function resetForm() {
  editingId.value = null
  error.value = ''
  ok.value = ''
  form.name = ''
  form.notes = ''
  form.telegram = false
  form.whatsapp = false
  form.wx = false
  form.telegram_bot_token = ''
  form.telegram_chat_id = ''
  form.whatsapp_phone = ''
  form.whatsapp_apikey = ''
  form.wx_webhook = ''
  form.wx_sendkey = ''
  form.has_telegram_token = false
  form.telegram_token_ok = true
  form.has_whatsapp_apikey = false
  form.has_wx_webhook = false
  form.has_wx_sendkey = false
  secretDirty.telegram_bot_token = false
  secretDirty.whatsapp_apikey = false
  secretDirty.wx_webhook = false
  secretDirty.wx_sendkey = false
}

function fillForm(row) {
  form.name = row.name || ''
  form.notes = row.notes || ''
  form.telegram = !!row.telegram
  form.whatsapp = !!row.whatsapp
  form.wx = !!row.wx
  form.telegram_bot_token = ''
  form.telegram_chat_id = row.telegram_chat_id || ''
  form.whatsapp_phone = row.whatsapp_phone || ''
  form.whatsapp_apikey = ''
  form.wx_webhook = ''
  form.wx_sendkey = ''
  form.has_telegram_token = !!row.has_telegram_token
  form.telegram_token_ok = row.telegram_token_ok !== false
  form.has_whatsapp_apikey = !!row.has_whatsapp_apikey
  form.has_wx_webhook = !!row.has_wx_webhook
  form.has_wx_sendkey = !!row.has_wx_sendkey
  secretDirty.telegram_bot_token = false
  secretDirty.whatsapp_apikey = false
  secretDirty.wx_webhook = false
  secretDirty.wx_sendkey = false
}

function backToList() {
  screen.value = 'list'
  resetForm()
}

function openNew() {
  listOk.value = ''
  listError.value = ''
  resetForm()
  screen.value = 'edit'
}

function editRow(row) {
  listOk.value = ''
  listError.value = ''
  editingId.value = row.id
  error.value = ''
  ok.value = ''
  fillForm(row)
  screen.value = 'edit'
}

function payload() {
  const body = {
    name: form.name.trim(),
    notes: form.notes.trim() || null,
    telegram: !!form.telegram,
    whatsapp: !!form.whatsapp,
    wx: !!form.wx,
    telegram_chat_id: form.telegram_chat_id.trim(),
    whatsapp_phone: form.whatsapp_phone.trim(),
  }
  // 已保存过的密钥：必须用户亲手改过才提交，避免自动填充覆盖
  const secrets = [
    ['telegram_bot_token', form.has_telegram_token],
    ['whatsapp_apikey', form.has_whatsapp_apikey],
    ['wx_webhook', form.has_wx_webhook],
    ['wx_sendkey', form.has_wx_sendkey],
  ]
  for (const [key, hasSaved] of secrets) {
    const value = String(form[key] || '').trim()
    if (!value) continue
    if (hasSaved && !secretDirty[key]) continue
    body[key] = value
  }
  return body
}

async function load() {
  loading.value = !rows.value.length
  listError.value = ''
  try {
    const data = await api.recipients()
    rows.value = data.items || []
  } catch (e) {
    listError.value = e.message
  } finally {
    loading.value = false
  }
}

async function save() {
  error.value = ''
  ok.value = ''
  if (!form.name.trim()) {
    error.value = '请填写名字'
    return
  }
  saving.value = true
  try {
    const wasEdit = !!editingId.value
    const saved = wasEdit
      ? await api.updateRecipient(editingId.value, payload())
      : await api.createRecipient(payload())
    await load()
    const name = saved.name
    backToList()
    listOk.value = wasEdit ? `「${name}」已更新` : `「${name}」已保存`
  } catch (e) {
    error.value = e.message
  } finally {
    saving.value = false
  }
}

async function testSend() {
  error.value = ''
  ok.value = ''
  if (!editingId.value) {
    error.value = '先保存这个人，再发测试'
    return
  }
  if (form.telegram && (!form.telegram_token_ok || !form.has_telegram_token) && !secretDirty.telegram_bot_token) {
    error.value = '请先粘贴完整的 Telegram Bot Token（形如 123456789:AAH…），再点更新或测试'
    return
  }
  testing.value = true
  try {
    // 有改动才先保存；密钥仅在 dirty 时才会进 payload
    const saved = await api.updateRecipient(editingId.value, payload())
    form.has_telegram_token = !!saved.has_telegram_token
    form.telegram_token_ok = saved.telegram_token_ok !== false
    await api.testRecipient(editingId.value)
    ok.value = '测试消息已发出，去手机上看一眼。'
    await load()
  } catch (e) {
    error.value = e.message
  } finally {
    testing.value = false
  }
}

async function remove(row) {
  if (!confirm(`删除推送人「${row.name}」？`)) return
  listError.value = ''
  listOk.value = ''
  try {
    await api.deleteRecipient(row.id)
    if (editingId.value === row.id) backToList()
    rows.value = rows.value.filter((item) => item.id !== row.id)
    listOk.value = `「${row.name}」已删除`
  } catch (e) {
    listError.value = e.message
  }
}

onMounted(load)
</script>

<template>
  <template v-if="screen === 'edit'">
    <div class="range-row">
      <button class="btn" type="button" @click="backToList">返回列表</button>
    </div>
    <p class="goal-lead">填名字，勾要发的渠道，把密钥填上。一个人可以同时开 WhatsApp、Telegram、微信。</p>
    <section class="panel">
      <div class="formula-head" style="margin-bottom: 10px">
        <h2 style="margin: 0">{{ editingId ? '改推送人' : '新推送人' }}</h2>
      </div>
      <div class="form-grid" style="margin-top: 10px">
        <div class="field">
          <label>名字</label>
          <input v-model="form.name" placeholder="例如 自己 / 搭档" />
        </div>
        <div class="field">
          <label>备注</label>
          <input v-model="form.notes" placeholder="可选" />
        </div>
        <div class="field full">
          <label>渠道</label>
          <div class="check-row" style="flex-wrap: wrap">
            <label class="check"><input v-model="form.telegram" type="checkbox" /> Telegram</label>
            <label class="check"><input v-model="form.whatsapp" type="checkbox" /> WhatsApp</label>
            <label class="check"><input v-model="form.wx" type="checkbox" /> 微信</label>
          </div>
        </div>
      </div>

      <div class="form-grid" style="margin-top: 10px" v-if="form.telegram">
        <div class="field">
          <label>
            Telegram Bot Token
            <template v-if="form.has_telegram_token && form.telegram_token_ok">（已保存，留空不改）</template>
            <template v-else-if="form.has_telegram_token && !form.telegram_token_ok">（已失效，请重新粘贴）</template>
          </label>
          <input
            v-model="form.telegram_bot_token"
            type="password"
            data-secret-key="telegram_bot_token"
            autocomplete="new-password"
            readonly
            :placeholder="
              form.has_telegram_token && form.telegram_token_ok
                ? '已保存，留空不改'
                : '从 @BotFather 复制完整 token，形如 123456789:AAH…'
            "
            @focus="unlockSecret"
            @keydown="markSecretDirty('telegram_bot_token')"
            @paste="markSecretDirty('telegram_bot_token')"
          />
          <p class="error" v-if="form.has_telegram_token && !form.telegram_token_ok" style="margin-top: 6px">
            库里的 Token 已无效（常见原因：浏览器自动填充覆盖）。必须重新粘贴完整 Token。
          </p>
        </div>
        <div class="field">
          <label>Telegram Chat ID</label>
          <input v-model="form.telegram_chat_id" placeholder="例如 123456789" autocomplete="off" />
        </div>
        <p class="sub field full">
          跟 @BotFather 建一个机器人拿到 token。先给这个机器人发一句任意话，再打开
          <code>https://api.telegram.org/bot&lt;token&gt;/getUpdates</code>
          看 <code>chat.id</code>。
        </p>
      </div>

      <div class="form-grid" style="margin-top: 10px" v-if="form.whatsapp">
        <div class="field">
          <label>WhatsApp 手机号（国际区号）</label>
          <input v-model="form.whatsapp_phone" placeholder="86138xxxxxxxx" />
        </div>
        <div class="field">
          <label>CallMeBot Apikey{{ form.has_whatsapp_apikey ? '（已保存，留空不改）' : '' }}</label>
          <input
            v-model="form.whatsapp_apikey"
            type="password"
            data-secret-key="whatsapp_apikey"
            autocomplete="new-password"
            readonly
            :placeholder="form.has_whatsapp_apikey ? '已保存，留空不改' : '机器人回复的 apikey'"
            @focus="unlockSecret"
            @keydown="markSecretDirty('whatsapp_apikey')"
            @paste="markSecretDirty('whatsapp_apikey')"
          />
        </div>
        <p class="sub field full">
          把 <code>+34 644 21 89 76</code> 加到通讯录，给它发
          <code>I allow callmebot to send me messages</code>，它会回 apikey。号码用国际区号，不要加 +。
        </p>
      </div>

      <div class="form-grid" style="margin-top: 10px" v-if="form.wx">
        <div class="field">
          <label>企业微信 Webhook{{ form.has_wx_webhook ? '（已保存，留空不改）' : '' }}</label>
          <input
            v-model="form.wx_webhook"
            type="password"
            data-secret-key="wx_webhook"
            autocomplete="new-password"
            readonly
            :placeholder="form.has_wx_webhook ? '已保存，留空不改' : 'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=…'"
            @focus="unlockSecret"
            @keydown="markSecretDirty('wx_webhook')"
            @paste="markSecretDirty('wx_webhook')"
          />
        </div>
        <div class="field">
          <label>Server酱 SendKey{{ form.has_wx_sendkey ? '（已保存，留空不改）' : '' }}</label>
          <input
            v-model="form.wx_sendkey"
            type="password"
            data-secret-key="wx_sendkey"
            autocomplete="new-password"
            readonly
            :placeholder="form.has_wx_sendkey ? '已保存，留空不改' : 'SCT 开头的 key，推到个人微信'"
            @focus="unlockSecret"
            @keydown="markSecretDirty('wx_sendkey')"
            @paste="markSecretDirty('wx_sendkey')"
          />
        </div>
        <p class="sub field full">
          企业微信：群里添加机器人，复制 webhook。个人微信：用
          <a href="https://sct.ftqq.com" target="_blank" rel="noreferrer">Server酱</a>
          绑定微信拿 SendKey。两个填一个即可，都填会都发。
        </p>
      </div>

      <div class="modal-actions" style="margin-top: 12px; justify-content: flex-start">
        <button class="btn btn-primary" type="button" :disabled="saving" @click="save">
          {{ saving ? '保存中…' : editingId ? '更新' : '保存' }}
        </button>
        <button class="btn" type="button" :disabled="testing || !editingId" @click="testSend">
          {{ testing ? '发送中…' : '发一条测试' }}
        </button>
      </div>
      <div class="error" v-if="error">{{ error }}</div>
      <p class="ok-msg" v-else-if="ok">{{ ok }}</p>
    </section>
  </template>

  <template v-else>
    <p class="goal-lead">这些人会收到列表里命中的推送。点添加组一个人，把 WhatsApp / Telegram / 微信填上。</p>
    <div class="range-row">
      <div class="watch-edit" style="flex: 1; margin: 0">
        <input v-model="search" placeholder="搜索名字" />
      </div>
      <button class="btn btn-primary" type="button" @click="openNew">添加</button>
    </div>
    <div class="error" v-if="listError">{{ listError }}</div>
    <p class="ok-msg" v-else-if="listOk">{{ listOk }}</p>
    <p class="sub" v-else-if="loading">加载中…</p>

    <section class="panel" style="margin-top: 12px">
      <div class="table-wrap" v-if="filtered.length">
        <table>
          <thead>
            <tr>
              <th>名字</th>
              <th>渠道</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="row in filtered" :key="row.id">
              <td>
                <b>{{ row.name }}</b>
                <div class="sub" v-if="row.notes">{{ row.notes }}</div>
              </td>
              <td>{{ (row.channels || []).join('、') || '未接通' }}</td>
              <td class="pos-actions">
                <button class="icon-btn" type="button" @click="editRow(row)">改</button>
                <button class="icon-btn" type="button" @click="remove(row)">删除</button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <div v-else class="empty">
        {{ search.trim() ? '没有叫这个名字的推送人。' : '还没有推送人。点右上角添加。' }}
      </div>
    </section>
  </template>
</template>
