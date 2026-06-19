<script setup>
defineProps({ items: { type: Array, default: () => [] } })
</script>

<template>
  <div v-if="items.length">
    <h3 class="text-sm font-semibold text-[var(--text-secondary)] uppercase tracking-wider mb-2">关键决策</h3>
    <ul class="space-y-1.5">
      <li v-for="(d, i) in items" :key="i" class="text-sm text-[var(--text-primary)] flex items-start gap-2">
        <span class="text-blue-500 mt-0.5 flex-shrink-0">&#9679;</span>
        <span>{{ d }}</span>
      </li>
    </ul>
  </div>
</template>
