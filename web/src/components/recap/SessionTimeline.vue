<script setup>
defineProps({ sessions: { type: Array, default: () => [] } })

function formatTime(ts) {
  if (!ts) return ''
  return ts.split('T')[1]?.substring(0, 5) || ts.substring(11, 16)
}
</script>

<template>
  <div v-if="sessions.length">
    <h3 class="text-sm font-semibold text-[var(--text-secondary)] uppercase tracking-wider mb-2">会话时间线</h3>
    <div class="space-y-1">
      <div v-for="(s, i) in sessions" :key="i" class="flex items-center gap-3 text-sm py-1.5">
        <span class="text-[var(--text-secondary)] font-mono text-xs w-12">{{ formatTime(s.start_time) }}</span>
        <div class="flex-1 h-px bg-[var(--border-color)] relative">
          <div class="absolute left-0 top-1/2 -translate-y-1/2 w-2 h-2 rounded-full bg-blue-500"></div>
        </div>
        <span class="text-[var(--text-secondary)] font-mono text-xs w-12">{{ formatTime(s.end_time) }}</span>
        <span class="text-[var(--text-primary)] truncate">{{ s.project }}</span>
      </div>
    </div>
  </div>
</template>
