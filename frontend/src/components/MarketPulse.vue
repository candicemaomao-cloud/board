<script setup>
import NewsAgentChat from './NewsAgentChat.vue'

const props = defineProps({
  market: { type: Object, default: null },     
  loading: { type: Boolean, default: false },
})
console.log('market', props.market)
function sentimentTone(level) {
  if (level === 'severe_negative' || level === 'mild_negative') return 'down'
  if (level === 'mild_positive' || level === 'very_positive') return 'up'
  return ''
}

function fomcHint(next) {
  if (!next) return '日程待更新'
  if (next.status === '进行中') return '会议进行中'
  if (next.days_until === 0) return '今天开会'
  if (next.days_until < 0) return '已结束'
  return `还有 ${next.days_until} 天`
}
</script>

<template>
 <div>
  <div class="grid-2 news-grid">
    <section class="panel">
      <h2>宏观因素 · FOMC</h2>
      <div v-if="loading && !market" class="empty">正在翻译官方信息…</div>
      <template v-else-if="market">
        <div class="fomc-next" v-if="market.fomc?.next">
          <div class="label">下一次议息</div>
          <div class="value">{{ market.fomc.next.label }} {{ market.fomc.next.year }}</div>
          <div class="sub">
            {{ fomcHint(market.fomc.next) }}
            <span v-if="market.fomc.next.sep"> · 含经济预测 / 点阵图</span>
          </div>
        </div>
        <div class="fomc-chips">
          <span
            v-for="m in market.fomc?.upcoming || []"
            :key="m.start"
            class="chip"
            :class="{ now: m.status === '进行中' }"
          >
            {{ m.label }}{{ m.sep ? '*' : '' }}
          </span>
        </div>
        <ul class="news-list">
          <li v-for="item in market.fed_releases" :key="item.url">
            <a :href="item.url" target="_blank" rel="noopener noreferrer">
              <span class="kind">{{ item.kind }}</span>
              <div class="news-copy">
                <strong>{{ item.title }}</strong>
                <small v-if="item.title_en && item.title_en !== item.title">{{ item.title_en }}</small>
              </div>
              <em>{{ item.ago }}</em>
            </a>
          </li>
        </ul>
        <h3 class="news-sub">官员讲话</h3>
        <ul class="news-list compact">
          <li v-for="item in (market.fed_speeches || []).slice(0, 5)" :key="item.url">
            <a :href="item.url" target="_blank" rel="noopener noreferrer">
              <div class="news-copy">
                <strong>{{ item.title }}</strong>
                <small v-if="item.title_en && item.title_en !== item.title">{{ item.title_en }}</small>
              </div>
              <em>{{ item.ago }}</em>
            </a>
          </li>
        </ul>
      </template>
    </section>

    <section class="panel">
      <h2>市场新闻 · 分类</h2>
      <p class="sub">按标题/摘要里的关键词粗分类、粗判情绪，不是精确分类/真正的NLP情感分析；数据源本身不带这些标签，只能大致看个方向，反讽和辟谣类新闻容易判错。</p>
      <div v-if="loading && !market" class="empty">正在翻译财经新闻…</div>
      <template v-else-if="market?.news_by_topic?.length">
        <div class="sentiment-summary" v-if="market.news_sentiment">
          今日新闻情绪：
          <b :class="market.news_sentiment.avg_score > 0 ? 'up' : market.news_sentiment.avg_score < 0 ? 'down' : ''">
            {{ market.news_sentiment.overall }}
          </b>
          （{{ market.news_sentiment.sample_size }} 条中：强空{{ market.news_sentiment.counts.severe_negative }} ·
          偏空{{ market.news_sentiment.counts.mild_negative }} · 中性{{ market.news_sentiment.counts.neutral }} ·
          偏多{{ market.news_sentiment.counts.mild_positive }} · 强多{{ market.news_sentiment.counts.very_positive }}）
        </div>
        <template v-for="group in market.news_by_topic" :key="group.topic">
          <h3 class="news-sub">{{ group.topic }}</h3>
          <ul class="news-list">
            <li v-for="item in group.items" :key="item.url">
              <a :href="item.url" target="_blank" rel="noopener noreferrer">
                <span class="kind dim">{{ item.source }}</span>
                <div class="news-copy">
                  <strong>
                    {{ item.title }}
                    <span
                      v-if="item.sentiment && item.sentiment.level !== 'neutral'"
                      class="sentiment-badge"
                      :class="sentimentTone(item.sentiment.level)"
                    >{{ item.sentiment.label }}</span>
                  </strong>
                  <small v-if="item.title_en && item.title_en !== item.title">{{ item.title_en }}</small>
                </div>
                <em>{{ item.ago }}</em>
              </a>
            </li>
          </ul>
        </template>
      </template>
      <div v-else class="empty">新闻源暂时不可用，稍后自动重试。</div>
    </section>
  </div>
  <NewsAgentChat />
 </div>
</template>
