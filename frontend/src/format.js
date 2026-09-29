export function money(n, { signed = true, withSymbol = true } = {}) {
  if (n == null || Number.isNaN(Number(n))) return '—'
  const value = Number(n)
  const abs = Math.abs(value).toLocaleString('en-US', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })
  const unit = withSymbol ? '$' : ''
  if (!signed) return `${unit}${abs}`
  if (value > 0) return `+${unit}${abs}`
  if (value < 0) return `-${unit}${abs}`
  return `${unit}${abs}`
}

export function pct(n, signed = true) {
  if (n == null || Number.isNaN(Number(n))) return '—'
  const value = Number(n)
  const sign = signed && value > 0 ? '+' : ''
  return `${sign}${value.toFixed(2)}%`
}

export function tone(n) {
  if (n > 0) return 'up'
  if (n < 0) return 'down'
  return ''
}

export function isoDate(d = new Date()) {
  const dt = d instanceof Date ? d : new Date(d)
  const y = dt.getFullYear()
  const m = String(dt.getMonth() + 1).padStart(2, '0')
  const day = String(dt.getDate()).padStart(2, '0')
  return `${y}-${m}-${day}`
}

export function compactUsd(n) {
  if (n == null || Number.isNaN(Number(n))) return '—'
  const value = Number(n)
  const abs = Math.abs(value)
  const sign = value < 0 ? '-' : ''
  if (abs >= 1_000_000) return `${sign}$${(abs / 1_000_000).toFixed(abs >= 10_000_000 ? 0 : 2)}M`
  return money(value, { signed: false })
}

export function compactVol(n) {
  if (n == null || Number.isNaN(Number(n))) return '—'
  const abs = Math.abs(Number(n))
  if (abs >= 1_000_000_000) return `${(abs / 1_000_000_000).toFixed(2)}B`
  if (abs >= 1_000_000) return `${(abs / 1_000_000).toFixed(2)}M`
  if (abs >= 1_000) return `${(abs / 1_000).toFixed(1)}K`
  return String(Math.round(abs))
}

export function weekFriday(d = new Date()) {
  const dt = d instanceof Date ? new Date(d) : new Date(d)
  const offset = (5 - dt.getDay() + 7) % 7
  dt.setDate(dt.getDate() + offset)
  return isoDate(dt)
}

export function rangeDates(key) {
  const today = new Date()
  if (key === 'all') return {}
  if (key === '8w') {
    const start = new Date(today)
    start.setDate(start.getDate() - 56)
    return { start: isoDate(start), end: isoDate(today) }
  }
  if (key === '26w') {
    const start = new Date(today)
    start.setDate(start.getDate() - 182)
    return { start: isoDate(start), end: isoDate(today) }
  }
  if (key === 'mtd') {
    return { start: isoDate(new Date(today.getFullYear(), today.getMonth(), 1)), end: isoDate(today) }
  }
  if (key === 'ytd') {
    return { start: isoDate(new Date(today.getFullYear(), 0, 1)), end: isoDate(today) }
  }
  return {}
}
