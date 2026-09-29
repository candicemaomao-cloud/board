// 从股票日志正文里解析「加入日志」写进去的三块：【恐慌指标】【衍生品三层】【行业分析】。
// 同一块出现多次时取最后一次。

const VALUE_RE = /^[+-]?\d/
const FEAR_WORDS = ['极度恐惧', '恐惧', '恐慌', '悲观', '偏恐惧', '加空', '紧张', '偏高', '高']
const GREED_WORDS = ['极度贪婪', '贪婪', '乐观', '偏贪婪', '加多']

export function toneOfLabel(label) {
  const s = String(label || '')
  if (GREED_WORDS.some((w) => s.includes(w))) return 'up'
  if (FEAR_WORDS.some((w) => s.includes(w))) return 'down'
  return ''
}

function num(s) {
  const n = parseFloat(String(s).replace(/[^\d.+-]/g, ''))
  return Number.isFinite(n) ? n : null
}

// "VIX 恐慌指数 14.9 平静 -0.8" -> title / value / label / delta
function parseCardLine(line) {
  const tokens = line.trim().split(/\s+/)
  const i = tokens.findIndex((t) => VALUE_RE.test(t))
  if (i <= 0) return null
  return {
    title: tokens.slice(0, i).join(' '),
    value: tokens[i],
    label: tokens[i + 1] && !VALUE_RE.test(tokens[i + 1]) ? tokens[i + 1] : '',
    delta: tokens.slice(tokens[i + 1] && !VALUE_RE.test(tokens[i + 1]) ? i + 2 : i + 1).join(' '),
  }
}

function splitSections(text) {
  const sections = {}
  let current = null
  for (const raw of String(text || '').split(/\r?\n/)) {
    const m = raw.match(/^\s*【([^】]+)】\s*(.*)$/)
    if (m) {
      const name = m[1].split('·')[0].trim()
      current = { header: m[1].trim(), tail: m[2].trim(), lines: [] }
      sections[name] = current
      continue
    }
    if (current && raw.trim()) current.lines.push(raw)
  }
  return sections
}

const SECTOR_PCR_RE = /^\s*([A-Z]{2,5})\((.+?)\)\s+Put\/Call\s+([\d.]+)\s+Call\s+([\d.]+|—)%\s+Put\s+([\d.]+|—)%/

export function parseJournal(text) {
  const sec = splitSections(text)
  const out = { fear: null, derivatives: null, sector: null }

  const f = sec['恐慌指标']
  if (f) {
    const fear = { date: f.tail, composite: null, compositeLabel: '', cards: [] }
    for (const line of f.lines) {
      const cm = line.match(/^\s*六项等权\s+([\d.]+)\s*（(.*?)）/)
      if (cm) {
        fear.composite = num(cm[1])
        fear.compositeLabel = cm[2]
        continue
      }
      const card = parseCardLine(line)
      if (card) fear.cards.push(card)
    }
    if (fear.composite != null || fear.cards.length) out.fear = fear
  }

  const d = sec['衍生品三层']
  if (d) {
    const der = { cards: [], sectors: [] }
    for (const line of d.lines) {
      const sm = line.match(SECTOR_PCR_RE)
      if (sm) {
        der.sectors.push({
          symbol: sm[1],
          name: sm[2],
          pcr: num(sm[3]),
          call: num(sm[4]),
          put: num(sm[5]),
        })
        continue
      }
      const card = parseCardLine(line)
      if (card) der.cards.push(card)
    }
    if (der.cards.length || der.sectors.length) out.derivatives = der
  }

  const s = sec['行业分析']
  if (s) {
    const period = s.header.includes('·') ? s.header.split('·').slice(1).join('·').trim() : ''
    const rows = []
    for (const line of s.lines) {
      const m = line.match(/^\s*(.+?)\s+([+-]?[\d.]+)%\s*[（(](\d+)\s*支[）)]/)
      if (m) rows.push({ sector: m[1], pct: num(m[2]), count: Number(m[3]) })
    }
    if (rows.length) out.sector = { period, date: s.tail, rows }
  }

  return out
}

export function hasJournalCharts(text) {
  const p = parseJournal(text)
  return !!(p.fear || p.derivatives || p.sector)
}
