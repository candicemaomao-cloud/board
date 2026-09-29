/** 登录态与权限：localStorage 存 token。 */

const TOKEN_KEY = 'pnl_board_token'
const USER_KEY = 'pnl_board_user'

import { computed, ref } from 'vue'

const token = ref(localStorage.getItem(TOKEN_KEY) || '')
const user = ref(null)

try {
  const raw = localStorage.getItem(USER_KEY)
  if (raw) user.value = JSON.parse(raw)
} catch {
  user.value = null
}

export function getToken() {
  return token.value
}

export function setSession(accessToken, userInfo) {
  token.value = accessToken || ''
  user.value = userInfo || null
  if (accessToken) localStorage.setItem(TOKEN_KEY, accessToken)
  else localStorage.removeItem(TOKEN_KEY)
  if (userInfo) localStorage.setItem(USER_KEY, JSON.stringify(userInfo))
  else localStorage.removeItem(USER_KEY)
}

/** 更新当前登录用户的部分字段（如总金额），并写回 localStorage。 */
export function patchCurrentUser(partial) {
  if (!user.value) return
  user.value = { ...user.value, ...partial }
  localStorage.setItem(USER_KEY, JSON.stringify(user.value))
}

export function clearSession() {
  setSession('', null)
}

export function can(code) {
  if (!code) return true
  const u = user.value
  if (!u) return false
  if (u.role === 'admin') return true
  const list = u.permissions || []
  return list.includes(code)
}

export function canAny(...codes) {
  return codes.some((c) => can(c))
}

export const authState = {
  token,
  user,
  isLoggedIn: computed(() => !!token.value && !!user.value),
  can,
  canAny,
}
