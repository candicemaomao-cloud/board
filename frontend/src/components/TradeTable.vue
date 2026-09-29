<script setup>
import { money, pct, tone } from '../format'
import { can } from '../auth'

defineProps({
  trades: { type: Array, default: () => [] },
})
defineEmits(['delete'])
</script>

<template>
  <div class="table-wrap">
    <table v-if="trades.length">
      <thead>
        <tr>
          <th>日期</th>
          <th>代码</th>
          <th>方向</th>
          <th>盈亏</th>
          <th>收益率</th>
          <th>标签</th>
          <th>来源</th>
          <th>备注</th>
          <th></th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="t in trades" :key="t.id">
          <td>{{ t.date }}</td>
          <td>{{ t.symbol }}</td>
          <td :class="t.side === 'LONG' ? 'side-long' : 'side-short'">{{ t.side }}</td>
          <td :class="tone(t.pnl_amount)">{{ money(t.pnl_amount) }}</td>
          <td :class="tone(t.pnl_pct)">{{ t.pnl_pct == null ? '—' : pct(t.pnl_pct * 100) }}</td>
          <td>
            <span v-for="tag in t.tags" :key="tag" class="tag">{{ tag }}</span>
            <span v-if="!t.tags.length" class="sub">—</span>
          </td>
          <td>
            <span v-if="t.source === 'import'" class="tag">导入</span>
            <span v-else-if="t.source === 'binance'" class="tag">币安</span>
            <span v-else-if="t.source === 'position'" class="tag">持仓平仓</span>
            <span v-else class="sub">手记</span>
          </td>
          <td>{{ t.notes || '—' }}</td>
          <td>
            <button
              v-if="can('btn.board.trade_delete')"
              class="icon-btn"
              title="删除"
              @click="$emit('delete', t.id)"
            >
              删除
            </button>
          </td>
        </tr>
      </tbody>
    </table>
    <div v-else class="empty">从本周开始记。点右上角「记一笔交易」或「修改总金额」。</div>
  </div>
</template>
