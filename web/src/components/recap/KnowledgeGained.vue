<script setup>
defineProps({ items: { type: Array, default: () => [] } })
const emit = defineEmits(['writeToMemory'])

function importanceClass(imp) {
  return imp === 'high' ? 'text-red-400' : imp === 'medium' ? 'text-amber-400' : 'text-green-400'
}
</script>

<template>
  <div v-if="items.length">
    <h3 class="text-sm font-semibold text-[var(--text-secondary)] uppercase tracking-wider mb-2">新学知识</h3>
    <div class="space-y-2">
      <div v-for="(k, i) in items" :key="i" class="flex items-start gap-2 bg-[var(--bg-card)] rounded-lg p-3 border border-[var(--border-color)]">
        <input type="checkbox" class="mt-1 flex-shrink-0 accent-blue-500" :checked="k.importance === 'high'" @change="$emit('writeToMemory', k)" />
        <div class="flex-1">
          <p class="text-sm text-[var(--text-primary)]">{{ k.content || k }}</p>
          <span :class="importanceClass(k.importance)" class="text-xs">{{ k.importance || 'low' }}</span>
        </div>
      </div>
    </div>
  </div>
</template>
