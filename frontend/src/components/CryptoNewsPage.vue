<script setup>
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from '../api'
import NewsPushPanel from './NewsPushPanel.vue'

const loading = ref(false)
const data = ref(null)
const tab = ref('daily')

const list = computed(() => {
  if (tab.value === 'weekly') return data.value?.weekly_digest || []
  if (tab.value === 'events') return data.value?.events || []
  if (tab.value === 'all') return data.value?.items || []
  return data.value?.daily_digest || []
})

const summary = computed(() => data.value?.sentiment || null)

function sentimentLabel(row) {
  return row?.sentiment?.label || '中性'
}

function sentimentClass(row) {
  const level = row?.sentiment?.level
  if (level === 'bullish') return 'up'
  if (level === 'bearish') return 'down'
  return 'muted'
}

function overallClass(label) {
  if (label === '偏多') return 'up'
  if (label === '偏空') return 'down'
  return 'muted'
}

async function load() {
  loading.value = true
  try {
    data.value = await api.cryptoNews({ limit: 50 })
  } catch (e) {
    ElMessage.error(e.message || '新闻加载失败')
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <p class="goal-lead">
    币圈新闻已翻译为中文，并标注情绪（偏多 / 偏空 / 中性）与重大事件标签。
  </p>

  <div class="range-row">
    <div class="pills">
      <button class="pill" :class="{ active: tab === 'daily' }" type="button" @click="tab = 'daily'">每日摘要</button>
      <button class="pill" :class="{ active: tab === 'weekly' }" type="button" @click="tab = 'weekly'">每周摘要</button>
      <button class="pill" :class="{ active: tab === 'events' }" type="button" @click="tab = 'events'">重大事件</button>
      <button class="pill" :class="{ active: tab === 'all' }" type="button" @click="tab = 'all'">全部</button>
    </div>
    <el-button :loading="loading" @click="load">刷新</el-button>
  </div>

  <section class="panel news-summary" style="margin-top: 12px" v-if="summary">
    <span class="sub">整体情绪</span>
    <span class="news-overall" :class="overallClass(summary.overall)">{{ summary.overall }}</span>
    <span class="sub">
      · 偏多 {{ summary.counts?.bullish || 0 }}
      / 中性 {{ summary.counts?.neutral || 0 }}
      / 偏空 {{ summary.counts?.bearish || 0 }}
    </span>
  </section>

  <section class="panel" style="margin-top: 12px" v-loading="loading">
    <el-table :data="list" stripe style="width: 100%">
      <el-table-column label="标题" min-width="280">
        <template #default="{ row }">
          <a v-if="row.url" :href="row.url" target="_blank" rel="noreferrer" class="news-link">{{ row.title }}</a>
          <span v-else>{{ row.title }}</span>
          <div class="sub" v-if="row.summary && tab === 'all'">{{ row.summary }}</div>
        </template>
      </el-table-column>
      <el-table-column label="情绪" width="88">
        <template #default="{ row }">
          <span class="sent-tag" :class="sentimentClass(row)">{{ sentimentLabel(row) }}</span>
        </template>
      </el-table-column>
      <el-table-column label="标签" width="180">
        <template #default="{ row }">
          <span v-for="t in row.tags || []" :key="t" class="tag-chip">{{ t }}</span>
        </template>
      </el-table-column>
      <el-table-column prop="source" label="来源" width="120" />
      <el-table-column prop="date" label="时间" width="150" />
    </el-table>
    <p class="sub" v-if="!loading && !list.length" style="margin-top: 12px">暂无新闻。</p>
  </section>

  <NewsPushPanel scope="crypto" />
</template>

<style scoped>
.news-link {
  color: var(--accent, #7aa2ff);
  text-decoration: none;
}
.news-link:hover {
  text-decoration: underline;
}
.tag-chip {
  display: inline-block;
  margin: 0 4px 4px 0;
  padding: 1px 6px;
  font-size: 12px;
  border: 1px solid rgba(148, 176, 210, 0.25);
  border-radius: 4px;
  color: #8b9bb0;
}
.news-summary {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 12px 16px;
}
.news-overall {
  font-size: 18px;
  font-weight: 650;
}
.sent-tag {
  font-weight: 600;
}
.muted {
  color: #8b9bb0;
}
</style>
