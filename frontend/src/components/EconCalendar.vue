<script setup>
defineProps({
  calendar: { type: Object, default: null },
})
const emit = defineEmits(['open'])

function hint(item) {
  if (item.status === '今日') return '今天公布'
  if (item.status === '已公布') return '已公布'
  if (item.days === 1) return '明天'
  return `还有 ${item.days} 天`
}
</script>

<template>
  <section class="panel econ-cal" v-if="calendar?.months?.length">
    <h2>宏观因素数据日历</h2>
    <p class="sub">{{ calendar.source }}。点名称打开对应页面。时间是美东，括号里是北京时间。</p>

    <div class="econ-next" v-if="calendar.next?.length">
      <button
        v-for="item in calendar.next"
        :key="item.key + item.date + item.name"
        class="econ-chip"
        :class="item.status"
        type="button"
        @click="emit('open', item.key)"
      >
        <b>{{ item.name }}</b>
        <span>{{ item.label }} {{ item.weekday }}</span>
        <em>{{ hint(item) }}</em>
      </button>
    </div>

    <div class="econ-months">
      <article v-for="month in calendar.months" :key="month.key" :class="{ current: month.current }">
        <h3>{{ month.label }}</h3>
        <ul>
          <li v-for="item in month.items" :key="item.key + item.date + item.name">
            <button type="button" :class="item.status" @click="emit('open', item.key)">
              <span class="econ-day">{{ item.label }} <small>{{ item.weekday }}</small></span>
              <span class="econ-name">{{ item.name }}</span>
              <span class="econ-ref">{{ item.ref }}</span>
              <span class="econ-time">{{ item.time_et }} ET（{{ item.time_cn }}）</span>
              <em>{{ item.status }}</em>
            </button>
          </li>
        </ul>
      </article>
    </div>
  </section>
</template>
