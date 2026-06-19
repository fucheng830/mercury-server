<script setup>
import { computed } from 'vue'

const props = defineProps({
  name: { type: String, required: true },
  color: { type: String, default: 'gray' },
})

const MAP = {
  green: { background: '#52C41A', color: '#fff' },
  blue: { background: '#1890FF', color: '#fff' },
  red: { background: '#F5222D', color: '#fff' },
  purple: { background: '#722ED1', color: '#fff' },
  amber: { background: '#FAAD14', color: '#fff' },
  gray: { background: 'rgba(128,128,128,0.16)', color: 'var(--text-secondary)' },
}

const style = computed(() => MAP[props.color] || MAP.gray)
</script>

<template>
  <span
    :style="style"
    class="inline-flex items-center px-1.5 py-px rounded text-xs font-medium leading-tight whitespace-nowrap"
  >{{ name }}</span>
</template>
