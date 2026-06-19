<script setup>
import { ref, computed } from 'vue'

const props = defineProps({
  dates: { type: Array, default: () => [] },
})
const emit = defineEmits(['selectDate'])

const currentMonth = ref(new Date().toISOString().substring(0, 7))

const dateMap = computed(() => {
  const m = {}
  for (const d of props.dates) {
    m[d.date] = d.session_count
  }
  return m
})

const calendarDays = computed(() => {
  const [year, month] = currentMonth.value.split('-').map(Number)
  const firstDay = new Date(year, month - 1, 1)
  const lastDay = new Date(year, month, 0)
  const startWeekday = (firstDay.getDay() + 6) % 7
  const days = []
  for (let i = 0; i < startWeekday; i++) days.push(null)
  for (let d = 1; d <= lastDay.getDate(); d++) {
    const dateStr = `${year}-${String(month).padStart(2, '0')}-${String(d).padStart(2, '0')}`
    days.push({ day: d, date: dateStr, count: dateMap.value[dateStr] || 0 })
  }
  return days
})

const monthLabel = computed(() => {
  const [y, m] = currentMonth.value.split('-')
  return `${y} 年 ${parseInt(m)} 月`
})

function prevMonth() {
  const [y, m] = currentMonth.value.split('-').map(Number)
  const d = new Date(y, m - 2, 1)
  currentMonth.value = d.toISOString().substring(0, 7)
}

function nextMonth() {
  const [y, m] = currentMonth.value.split('-').map(Number)
  const d = new Date(y, m, 1)
  currentMonth.value = d.toISOString().substring(0, 7)
}

function cellClass(count) {
  if (!count) return 'bg-[var(--bg-card)]'
  if (count >= 5) return 'bg-blue-500/40'
  if (count >= 3) return 'bg-blue-500/25'
  return 'bg-blue-500/10'
}
</script>

<template>
  <div>
    <div class="flex items-center justify-between mb-3">
      <button @click="prevMonth" class="p-1 rounded hover:bg-[var(--bg-card)] text-[var(--text-secondary)]">&lt;</button>
      <span class="text-sm font-semibold text-[var(--text-primary)]">{{ monthLabel }}</span>
      <button @click="nextMonth" class="p-1 rounded hover:bg-[var(--bg-card)] text-[var(--text-secondary)]">&gt;</button>
    </div>
    <div class="grid grid-cols-7 gap-1 text-center text-xs">
      <div v-for="d in ['一','二','三','四','五','六','日']" :key="d" class="text-[var(--text-secondary)] py-1">{{ d }}</div>
      <div v-for="(cell, i) in calendarDays" :key="i">
        <div v-if="cell" @click="emit('selectDate', cell.date)"
          class="py-1.5 rounded cursor-pointer transition-colors text-sm"
          :class="[cellClass(cell.count), cell.count ? 'text-[var(--text-primary)] font-medium' : 'text-[var(--text-secondary)]']">
          {{ cell.day }}
        </div>
      </div>
    </div>
  </div>
</template>
