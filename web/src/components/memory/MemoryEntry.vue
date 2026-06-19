<script setup>
defineProps({
  content: String,
  source: String,
  index: Number,
})

const emit = defineEmits(['edit', 'delete'])
</script>

<template>
  <div class="bg-[var(--bg-card)] rounded-lg border border-[var(--border-color)] p-4 hover:border-blue-500/30 transition-colors">
    <div class="flex items-start justify-between gap-3">
      <p class="text-sm text-[var(--text-primary)] whitespace-pre-wrap flex-1 leading-relaxed">{{ content }}</p>
      <div class="flex items-center gap-1 flex-shrink-0">
        <button
          @click="emit('edit', { content, source, index })"
          class="p-1.5 rounded-md text-[var(--text-secondary)] hover:text-blue-500 hover:bg-blue-500/10 transition-colors"
          title="编辑"
        >
          <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z"/></svg>
        </button>
        <button
          @click="emit('delete', { content, source, index })"
          class="p-1.5 rounded-md text-[var(--text-secondary)] hover:text-red-500 hover:bg-red-500/10 transition-colors"
          title="删除"
        >
          <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 6h18"/><path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6"/><path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"/></svg>
        </button>
      </div>
    </div>
  </div>
</template>
