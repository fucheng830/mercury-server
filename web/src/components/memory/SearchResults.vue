<script setup>
defineProps({
  results: Array,
  loading: Boolean,
  error: String,
})
</script>

<template>
  <div class="mt-4">
    <div v-if="loading" class="text-sm text-[var(--text-secondary)] text-center py-4">搜索中...</div>
    <div v-else-if="error" class="text-sm text-red-500 text-center py-4">{{ error }}</div>
    <div v-else-if="results.length === 0" class="text-sm text-[var(--text-secondary)] text-center py-4">无结果</div>
    <div v-else class="space-y-2">
      <div
        v-for="(r, i) in results"
        :key="i"
        class="bg-[var(--bg-card)] rounded-lg border border-[var(--border-color)] p-3 text-sm text-[var(--text-primary)]"
      >{{ r.content || r.text || JSON.stringify(r) }}</div>
    </div>
  </div>
</template>
