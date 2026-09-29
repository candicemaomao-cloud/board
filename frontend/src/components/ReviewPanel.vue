<script setup>
import { money } from '../format'

defineProps({
  review: { type: Object, required: true },
})
</script>

<template>
  <div class="review-grid">
    <div class="stat">
      <span>获利周 vs 亏损周</span>
      <b>{{ review.win_weeks }} / {{ review.loss_weeks }}</b>
    </div>
    <div class="stat">
      <span>平均盈利周 vs 平均亏损周</span>
      <b>
        <span class="up">{{ money(review.avg_win) }}</span>
        /
        <span class="down">{{ money(review.avg_loss) }}</span>
      </b>
    </div>
    <div class="stat">
      <span>最大单周盈利 / 亏损</span>
      <b>
        <span class="up">{{ money(review.max_win) }}</span>
        /
        <span class="down">{{ money(review.max_loss) }}</span>
      </b>
    </div>
    <div class="stat">
      <span>周均期望</span>
      <b :class="review.expectancy >= 0 ? 'up' : 'down'">{{ money(review.expectancy) }}</b>
    </div>
    <div class="stat">
      <span>连胜 / 连亏（最长）</span>
      <b>{{ review.streaks.longest_win_days }} 周 / {{ review.streaks.longest_loss_days }} 周</b>
    </div>
    <div class="stat">
      <span>当前状态</span>
      <b v-if="review.streaks.current_win_days">连胜 {{ review.streaks.current_win_days }} 周</b>
      <b v-else-if="review.streaks.current_loss_days" class="down">连亏 {{ review.streaks.current_loss_days }} 周</b>
      <b v-else>—</b>
    </div>
  </div>
</template>
