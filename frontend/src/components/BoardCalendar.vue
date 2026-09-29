<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { api } from '../api'

const emit = defineEmits(['open'])

const DEFAULT_KINDS = [
  { key: 'macro', label: '宏观数据', tone: 'macro' },
  { key: 'fed', label: '美联储', tone: 'fed' },
  { key: 'treasury', label: '美债标售', tone: 'treasury' },
  { key: 'witching', label: '三巫日', tone: 'witching' },
  { key: 'earnings', label: '列表财报', tone: 'earnings' },
]

function todayIso() {
  return new Date().toISOString().slice(0, 10)
}

const calendar = ref(null)
const calLoading = ref(false)
const calError = ref('')
const calLoaded = ref(false)

const monthCursor = ref(`${todayIso().slice(0, 7)}-01`)
const selectedDate = ref(todayIso())
/** ym -> { by_date, loading, error, note } */
const earningsByYm = ref({})
const earningsLoading = ref(false)

async function loadCalendar(force = false) {
  if (calLoading.value) return
  if (calLoaded.value && !force) return
  calLoading.value = true
  calError.value = ''
  try {
    const data = await api.marketBoardCalendar()
    calendar.value = data
    calLoaded.value = true
    if (data?.today) {
      if (!monthCursor.value) monthCursor.value = `${data.today.slice(0, 7)}-01`
      if (!selectedDate.value) selectedDate.value = data.today
    }
  } catch (e) {
    calError.value = e.message || '日程加载失败'
    if (!calendar.value) {
      calendar.value = {
        today: todayIso(),
        by_date: {},
        upcoming: [],
        kinds: DEFAULT_KINDS,
        note: '日程加载失败，可点刷新重试',
      }
    }
  } finally {
    calLoading.value = false
  }
}

onMounted(() => {
  loadCalendar()
})

const currentYm = computed(() => {
  const base = monthCursor.value || calendar.value?.today || ''
  return base ? base.slice(0, 7) : ''
})

const legendKinds = computed(() => {
  const base = calendar.value?.kinds?.length ? calendar.value.kinds : DEFAULT_KINDS
  if (base.some((k) => k.key === 'earnings')) return base
  return [...base, { key: 'earnings', label: '列表财报', tone: 'earnings' }]
})

const byDate = computed(() => {
  const base = { ...(calendar.value?.by_date || {}) }
  const ym = currentYm.value
  const earn = earningsByYm.value[ym]?.by_date || {}
  for (const [day, items] of Object.entries(earn)) {
    base[day] = [...(base[day] || []), ...items]
  }
  return base
})

const monthLabel = computed(() => {
  const base = monthCursor.value || calendar.value?.today || ''
  if (!base) return ''
  const [y, m] = base.split('-')
  return `${y}年${Number(m)}月`
})

const monthCells = computed(() => {
  const base = monthCursor.value || calendar.value?.today
  if (!base) return []
  const [y, m] = base.split('-').map(Number)
  if (!y || !m) return []
  const first = new Date(y, m - 1, 1)
  const startPad = (first.getDay() + 6) % 7 // Monday-first
  const daysInMonth = new Date(y, m, 0).getDate()
  const today = calendar.value?.today || todayIso()
  const cells = []
  for (let i = 0; i < startPad; i += 1) cells.push({ empty: true, key: `pad-${i}` })
  for (let d = 1; d <= daysInMonth; d += 1) {
    const iso = `${y}-${String(m).padStart(2, '0')}-${String(d).padStart(2, '0')}`
    const items = byDate.value[iso] || []
    const kinds = [...new Set(items.map((x) => x.kind))]
    cells.push({
      empty: false,
      key: iso,
      date: iso,
      day: d,
      items,
      kinds,
      selected: iso === selectedDate.value,
      today: iso === today,
      has: items.length > 0,
    })
  }
  return cells
})

const selectedItems = computed(() => {
  if (!selectedDate.value) return []
  return byDate.value[selectedDate.value] || []
})

const selectedLabel = computed(() => {
  if (!selectedDate.value) return ''
  const [, m, d] = selectedDate.value.split('-')
  const hit = selectedItems.value[0]
  return `${Number(m)}/${Number(d)}${hit ? ` ${hit.weekday}` : ''}`
})

const earningsNote = computed(() => {
  const ym = currentYm.value
  const pack = earningsByYm.value[ym]
  if (!pack) return ''
  if (pack.error) return pack.error
  if (pack.loading) return '正在加载本月列表财报…'
  const n = pack.count ?? 0
  return n ? `本月列表财报 ${n} 条` : '本月股票列表无财报日'
})

const statusLine = computed(() => {
  if (calLoading.value && !calLoaded.value) return '正在加载宏观 / FOMC / 美债 / 三巫日…'
  if (calLoading.value) return '刷新中…'
  if (calError.value) return calError.value
  return calendar.value?.note || '宏观 / FOMC / 美债 / 三巫日 / 列表财报'
})

async function loadEarningsMonth(ym) {
  if (!ym) return
  const hit = earningsByYm.value[ym]
  if (hit && (hit.loaded || hit.loading)) return
  earningsByYm.value = {
    ...earningsByYm.value,
    [ym]: { ...(hit || {}), loading: true, loaded: false, by_date: {}, error: '' },
  }
  earningsLoading.value = true
  try {
    const data = await api.marketBoardCalendarEarnings({ ym })
    earningsByYm.value = {
      ...earningsByYm.value,
      [ym]: {
        loading: false,
        loaded: true,
        by_date: data.by_date || {},
        count: data.count || 0,
        note: data.note || '',
        error: '',
      },
    }
  } catch (e) {
    earningsByYm.value = {
      ...earningsByYm.value,
      [ym]: {
        loading: false,
        loaded: true,
        by_date: {},
        count: 0,
        error: e.message || '财报加载失败',
      },
    }
  } finally {
    earningsLoading.value = false
  }
}

watch(
  currentYm,
  (ym) => {
    if (ym) loadEarningsMonth(ym)
  },
  { immediate: true },
)

function shiftMonth(delta) {
  const base = monthCursor.value || calendar.value?.today || todayIso()
  const [y, m] = base.split('-').map(Number)
  const dt = new Date(y, m - 1 + delta, 1)
  monthCursor.value = `${dt.getFullYear()}-${String(dt.getMonth() + 1).padStart(2, '0')}-01`
}

function pickDay(cell) {
  if (!cell?.date) return
  selectedDate.value = cell.date
}

/** 格子里用的短标签，一眼扫完当月 */
function shortName(item) {
  if (!item) return ''
  if (item.kind === 'witching') return '三巫日'
  if (item.kind === 'earnings') return item.symbol || String(item.name || '').replace(/\s*财报$/, '')
  if (item.kind === 'treasury') return item.name?.replace('美债·', '') || item.tenor || '美债'
  if (item.key === 'fomc') {
    return item.name?.includes('点阵') ? 'FOMC·点阵' : 'FOMC'
  }
  if (item.key === 'fomc_minutes') return 'FOMC纪要'
  if (item.key === 'claims') return '初请'
  if (item.key === 'adp') return 'ADP'
  if (item.key === 'pce') return '核心PCE'
  return item.name || item.key || ''
}

function hint(item) {
  if (item.status === '今日') return '今天'
  if (item.status === '已过') return '已过'
  if (item.days === 1) return '明天'
  if (item.days != null && item.days > 0) return `还有 ${item.days} 天`
  return item.status || ''
}

function onItem(item) {
  if (item?.open) emit('open', item.open)
}
</script>

<template>
  <section class="panel board-cal" :class="{ 'is-loading': calLoading && !calLoaded }">
    <div class="board-cal-head">
      <div>
        <h2 style="margin: 0">日程日历</h2>
        <p class="sub" style="margin: 4px 0 0">{{ statusLine }}</p>
      </div>
      <div class="board-cal-head-right">
        <div class="board-cal-legend">
          <span v-for="k in legendKinds" :key="k.key" class="leg" :class="`tone-${k.tone || k.key}`">
            {{ k.label }}
          </span>
        </div>
        <button
          class="btn"
          type="button"
          :disabled="calLoading"
          @click="loadCalendar(true)"
        >
          {{ calLoading ? '加载中…' : '刷新' }}
        </button>
      </div>
    </div>

    <div class="board-cal-loading" v-if="calLoading && !calLoaded">
      <span class="board-cal-spinner" />
      <span>日程数据加载中，月历骨架先可用…</span>
    </div>

    <div class="board-cal-next" v-if="calendar?.upcoming?.length">
      <button
        v-for="item in calendar.upcoming"
        :key="item.kind + item.key + item.date + item.name"
        class="board-cal-chip"
        :class="[`tone-${item.kind}`, item.status]"
        type="button"
        @click="selectedDate = item.date; monthCursor = `${item.date.slice(0, 7)}-01`; onItem(item)"
      >
        <b>{{ item.name }}</b>
        <span>{{ item.label }} {{ item.weekday }}</span>
        <em>{{ hint(item) }}</em>
      </button>
    </div>

    <div class="board-cal-witch" v-if="calendar?.next_witching">
      下一三巫日：
      <b>{{ calendar.next_witching.date }}</b>
      （{{ calendar.next_witching.weekday }}）
      <span class="sub">{{ hint(calendar.next_witching) }} · {{ calendar.next_witching.detail }}</span>
    </div>

    <div class="board-cal-nav">
      <button class="btn" type="button" @click="shiftMonth(-1)">‹</button>
      <b>{{ monthLabel }}</b>
      <button class="btn" type="button" @click="shiftMonth(1)">›</button>
    </div>
    <p class="sub board-cal-earn-note" v-if="earningsNote">
      {{ earningsNote }}
      <span v-if="earningsLoading">…</span>
    </p>
    <div class="board-cal-week">
      <span v-for="w in ['一', '二', '三', '四', '五', '六', '日']" :key="w">{{ w }}</span>
    </div>
    <div class="board-cal-grid" :class="{ dimmed: calLoading && !calLoaded }">
      <button
        v-for="cell in monthCells"
        :key="cell.key"
        type="button"
        class="board-cal-cell"
        :class="{
          empty: cell.empty,
          selected: cell.selected,
          today: cell.today,
          has: cell.has,
        }"
        :disabled="cell.empty"
        @click="pickDay(cell)"
      >
        <template v-if="!cell.empty">
          <span class="d">{{ cell.day }}</span>
          <span class="tags" v-if="cell.items?.length">
            <span
              v-for="item in cell.items.slice(0, 4)"
              :key="item.kind + item.key + item.name"
              class="mini-tag"
              :class="`tone-${item.kind}`"
              :title="item.detail || item.name"
            >{{ shortName(item) }}</span>
            <span v-if="cell.items.length > 4" class="mini-more">+{{ cell.items.length - 4 }}</span>
          </span>
        </template>
      </button>
    </div>

    <div class="board-cal-detail" v-if="selectedDate">
      <h3>{{ selectedLabel || selectedDate }}</h3>
      <ul v-if="selectedItems.length">
        <li v-for="item in selectedItems" :key="item.kind + item.key + item.name + (item.detail || '')">
          <button
            type="button"
            class="board-cal-item"
            :class="[`tone-${item.kind}`, { clickable: !!item.open }]"
            @click="onItem(item)"
          >
            <span class="tag">{{ item.name }}</span>
            <span class="meta">{{ item.detail || item.ref || '' }}</span>
            <em>{{ item.status }}</em>
          </button>
        </li>
      </ul>
      <p class="sub" v-else-if="calLoading && !calLoaded">加载中…</p>
      <p class="sub" v-else>这一天暂无日程，后续可在这里加别的日常事件。</p>
    </div>
  </section>
</template>

<style scoped>
.board-cal { margin-bottom: 12px; }
.board-cal-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
  margin-bottom: 10px;
}
.board-cal-head-right {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 8px;
}
.board-cal-loading {
  display: flex;
  align-items: center;
  gap: 10px;
  margin: 0 0 10px;
  padding: 8px 12px;
  border-radius: 8px;
  border: 1px dashed rgba(148, 176, 210, 0.28);
  background: rgba(122, 162, 255, 0.08);
  color: #9bb6ff;
  font-size: 13px;
}
.board-cal-spinner {
  width: 14px;
  height: 14px;
  border: 2px solid rgba(155, 182, 255, 0.25);
  border-top-color: #9bb6ff;
  border-radius: 50%;
  animation: board-cal-spin 0.7s linear infinite;
  flex-shrink: 0;
}
@keyframes board-cal-spin {
  to { transform: rotate(360deg); }
}
.board-cal-grid.dimmed {
  opacity: 0.55;
  pointer-events: none;
}
.board-cal-legend {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.board-cal-legend .leg {
  font-size: 11px;
  padding: 2px 8px;
  border-radius: 999px;
  border: 1px solid rgba(148, 176, 210, 0.25);
  color: var(--faint);
}
.board-cal-legend .tone-macro { border-color: rgba(122, 162, 255, 0.5); color: #9bb6ff; }
.board-cal-legend .tone-fed { border-color: rgba(240, 180, 41, 0.5); color: #f0b429; }
.board-cal-legend .tone-treasury { border-color: rgba(180, 140, 255, 0.5); color: #c4a8ff; }
.board-cal-legend .tone-witching { border-color: rgba(232, 93, 117, 0.5); color: #e85d75; }
.board-cal-legend .tone-earnings { border-color: rgba(62, 207, 142, 0.5); color: #3ecf8e; }

.board-cal-next {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 10px;
}
.board-cal-chip {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 2px;
  padding: 8px 10px;
  border-radius: 10px;
  border: 1px solid rgba(148, 176, 210, 0.2);
  background: rgba(148, 176, 210, 0.06);
  color: var(--text);
  cursor: pointer;
  min-width: 110px;
}
.board-cal-chip b { font-size: 13px; }
.board-cal-chip span,
.board-cal-chip em { font-size: 11px; color: var(--faint); font-style: normal; }
.board-cal-chip.今日 { border-color: rgba(62, 207, 142, 0.45); }
.board-cal-chip.tone-witching { border-color: rgba(232, 93, 117, 0.35); }
.board-cal-chip.tone-treasury { border-color: rgba(180, 140, 255, 0.35); }

.board-cal-witch {
  font-size: 13px;
  margin-bottom: 10px;
  color: var(--text);
}
.board-cal-witch b { color: #e85d75; margin: 0 4px; }
.board-cal-earn-note { margin: 0 0 8px; text-align: center; }

.board-cal-nav {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 16px;
  margin: 8px 0;
}
.board-cal-week {
  display: grid;
  grid-template-columns: repeat(7, 1fr);
  gap: 4px;
  margin-bottom: 4px;
  text-align: center;
  font-size: 11px;
  color: var(--faint);
}
.board-cal-grid {
  display: grid;
  grid-template-columns: repeat(7, 1fr);
  gap: 4px;
}
.board-cal-cell {
  min-height: 72px;
  border: 1px solid rgba(148, 176, 210, 0.12);
  border-radius: 8px;
  background: transparent;
  color: var(--text);
  cursor: pointer;
  display: flex;
  flex-direction: column;
  align-items: stretch;
  justify-content: flex-start;
  gap: 3px;
  padding: 5px 4px 4px;
  text-align: left;
}
.board-cal-cell.empty {
  border-color: transparent;
  cursor: default;
  min-height: 0;
}
.board-cal-cell:disabled { opacity: 0.35; }
.board-cal-cell.today { outline: 1px solid rgba(122, 162, 255, 0.55); }
.board-cal-cell.selected { background: rgba(122, 162, 255, 0.14); border-color: rgba(122, 162, 255, 0.4); }
.board-cal-cell.has .d { font-weight: 600; }
.board-cal-cell .d {
  font-size: 12px;
  line-height: 1;
  padding: 0 2px;
  color: var(--faint);
}
.board-cal-cell .tags {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.board-cal-cell .mini-tag {
  display: block;
  font-size: 10px;
  line-height: 1.25;
  padding: 1px 4px;
  border-radius: 4px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 100%;
  background: rgba(148, 176, 210, 0.1);
  color: var(--text);
}
.board-cal-cell .mini-tag.tone-macro {
  background: rgba(122, 162, 255, 0.16);
  color: #9bb6ff;
}
.board-cal-cell .mini-tag.tone-fed {
  background: rgba(240, 180, 41, 0.16);
  color: #f0b429;
}
.board-cal-cell .mini-tag.tone-treasury {
  background: rgba(180, 140, 255, 0.16);
  color: #c4a8ff;
}
.board-cal-cell .mini-tag.tone-witching {
  background: rgba(232, 93, 117, 0.16);
  color: #e85d75;
}
.board-cal-cell .mini-tag.tone-earnings {
  background: rgba(62, 207, 142, 0.16);
  color: #3ecf8e;
}
.board-cal-cell .mini-more {
  font-size: 10px;
  color: var(--faint);
  padding: 0 2px;
}

.board-cal-detail {
  margin-top: 12px;
  padding-top: 10px;
  border-top: 1px solid rgba(148, 176, 210, 0.15);
}
.board-cal-detail h3 {
  margin: 0 0 8px;
  font-size: 14px;
}
.board-cal-detail ul {
  list-style: none;
  margin: 0;
  padding: 0;
}
.board-cal-detail li + li { margin-top: 6px; }
.board-cal-item {
  width: 100%;
  display: grid;
  grid-template-columns: auto 1fr auto;
  gap: 10px;
  align-items: center;
  text-align: left;
  padding: 8px 10px;
  border-radius: 8px;
  border: 1px solid rgba(148, 176, 210, 0.15);
  background: rgba(148, 176, 210, 0.04);
  color: var(--text);
  cursor: default;
}
.board-cal-item.clickable { cursor: pointer; }
.board-cal-item.clickable:hover { background: rgba(255, 255, 255, 0.04); }
.board-cal-item .tag { font-weight: 600; font-size: 13px; white-space: nowrap; }
.board-cal-item.tone-macro .tag { color: #9bb6ff; }
.board-cal-item.tone-fed .tag { color: #f0b429; }
.board-cal-item.tone-treasury .tag { color: #c4a8ff; }
.board-cal-item.tone-witching .tag { color: #e85d75; }
.board-cal-item.tone-earnings .tag { color: #3ecf8e; }
.board-cal-item .meta { font-size: 12px; color: var(--faint); }
.board-cal-item em {
  font-style: normal;
  font-size: 11px;
  color: var(--faint);
  white-space: nowrap;
}

@media (max-width: 720px) {
  .board-cal-cell { min-height: 64px; }
  .board-cal-cell .mini-tag { font-size: 9px; padding: 1px 3px; }
}
</style>
