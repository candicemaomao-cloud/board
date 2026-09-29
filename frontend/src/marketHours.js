export function armQuoteTimer(loadFn, market, { liveMs = 30_000, always = false } = {}) {
  if (always || market?.live || market == null) {
    return setInterval(loadFn, liveMs)
  }
  if (!market?.next_open) return null
  const wait = Math.min(Math.max(market.next_open * 1000 - Date.now(), 5000), 6 * 3600 * 1000)
  return setTimeout(loadFn, wait)
}

export function clearQuoteTimer(id) {
  if (!id) return
  clearInterval(id)
  clearTimeout(id)
}
