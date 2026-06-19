<script setup>
import { ref } from 'vue'

const props = defineProps({
  visible: Boolean,
  entry: Object,
  mode: String,
})

const emit = defineEmits(['close', 'save'])

const text = ref(props.entry?.content || '')
const target = ref(props.entry?.source || 'memory')

function handleSave() {
  if (!text.value.trim()) return
  emit('save', {
    target: target.value,
    action: props.mode === 'edit' ? 'replace' : 'add',
    content: text.value.trim(),
    old_text: props.mode === 'edit' ? props.entry?.content || '' : '',
  })
}
</script>

<template>
  <div v-if="visible" class="fixed inset-0 bg-black/50 z-50 flex items-center justify-center" @click.self="emit('close')">
    <div class="bg-[var(--bg-card)] rounded-xl border border-[var(--border-color)] w-full max-w-lg p-6 shadow-xl">
      <h3 class="text-lg font-semibold text-[var(--text-primary)] mb-4">
        {{ mode === 'edit' ? '编辑记忆' : '新增记忆' }}
      </h3>

      <div v-if="mode === 'new'" class="mb-3">
        <label class="text-xs text-[var(--text-secondary)] mb-1 block">目标</label>
        <select v-model="target" class="w-full rounded-lg border border-[var(--border-color)] bg-[var(--bg-page)] px-3 py-2 text-sm text-[var(--text-primary)]">
          <option value="memory">MEMORY</option>
          <option value="user">USER</option>
        </select>
      </div>

      <textarea
        v-model="text"
        class="w-full h-48 rounded-lg border border-[var(--border-color)] bg-[var(--bg-page)] px-3 py-2 text-sm text-[var(--text-primary)] resize-none focus:outline-none focus:border-blue-500/50"
        placeholder="输入记忆内容..."
      ></textarea>

      <div class="flex justify-end gap-3 mt-4">
        <button @click="emit('close')" class="px-4 py-2 rounded-lg text-sm text-[var(--text-secondary)] hover:bg-[var(--bg-page)] transition-colors">取消</button>
        <button @click="handleSave" class="px-4 py-2 rounded-lg text-sm bg-blue-500 text-white hover:bg-blue-600 transition-colors">保存</button>
      </div>
    </div>
  </div>
</template>
