<script setup>
import KeyDecisions from './KeyDecisions.vue'
import KnowledgeGained from './KnowledgeGained.vue'
import SessionTimeline from './SessionTimeline.vue'

defineProps({
  recap: { type: Object, required: true },
})

const emit = defineEmits(['regenerate', 'writeToMemory'])
</script>

<template>
  <div class="space-y-6">
    <div class="bg-[var(--bg-card)] rounded-xl border border-[var(--border-color)] p-5">
      <div class="flex items-center justify-between mb-3">
        <h2 class="text-lg font-bold text-[var(--text-primary)]">{{ recap.date }} 复盘</h2>
        <button @click="emit('regenerate')" class="px-3 py-1.5 rounded-lg text-xs border border-[var(--border-color)] text-[var(--text-secondary)] hover:text-blue-500 hover:border-blue-500/50 transition-colors">重新生成</button>
      </div>
      <p class="text-sm text-[var(--text-primary)] leading-relaxed">{{ recap.summary }}</p>
      <div v-if="recap.model_used" class="mt-2 text-xs text-[var(--text-secondary)]">模型: {{ recap.model_used }}</div>
    </div>

    <div class="grid grid-cols-1 lg:grid-cols-2 gap-6">
      <div class="bg-[var(--bg-card)] rounded-xl border border-[var(--border-color)] p-5">
        <KeyDecisions :items="recap.key_decisions || []" />
        <div v-if="recap.patterns_observed?.length" class="mt-4">
          <h3 class="text-sm font-semibold text-[var(--text-secondary)] uppercase tracking-wider mb-2">工作模式</h3>
          <ul class="space-y-1.5">
            <li v-for="(p, i) in recap.patterns_observed" :key="i" class="text-sm text-[var(--text-primary)] flex items-start gap-2">
              <span class="text-purple-400 mt-0.5 flex-shrink-0">&#9679;</span>
              <span>{{ p }}</span>
            </li>
          </ul>
        </div>
      </div>
      <div class="bg-[var(--bg-card)] rounded-xl border border-[var(--border-color)] p-5">
        <KnowledgeGained :items="recap.knowledge_gained || []" @write-to-memory="(k) => emit('writeToMemory', k)" />
        <div v-if="recap.issues_encountered?.length" class="mt-4">
          <h3 class="text-sm font-semibold text-[var(--text-secondary)] uppercase tracking-wider mb-2">遇到的问题</h3>
          <ul class="space-y-1.5">
            <li v-for="(iss, i) in recap.issues_encountered" :key="i" class="text-sm text-[var(--text-primary)] flex items-start gap-2">
              <span class="text-red-400 mt-0.5 flex-shrink-0">&#9679;</span>
              <span>{{ iss }}</span>
            </li>
          </ul>
        </div>
      </div>
    </div>

    <div v-if="recap.todos_extracted?.length" class="bg-[var(--bg-card)] rounded-xl border border-[var(--border-color)] p-5">
      <h3 class="text-sm font-semibold text-[var(--text-secondary)] uppercase tracking-wider mb-2">待办事项</h3>
      <ul class="space-y-1.5">
        <li v-for="(t, i) in recap.todos_extracted" :key="i" class="text-sm text-[var(--text-primary)] flex items-start gap-2">
          <span class="text-amber-400 mt-0.5 flex-shrink-0">&#9744;</span>
          <span>{{ t }}</span>
        </li>
      </ul>
    </div>

    <div class="grid grid-cols-1 lg:grid-cols-2 gap-6">
      <div class="bg-[var(--bg-card)] rounded-xl border border-[var(--border-color)] p-5">
        <SessionTimeline :sessions="recap.sessions_detail || []" />
      </div>
      <div v-if="recap.stats" class="bg-[var(--bg-card)] rounded-xl border border-[var(--border-color)] p-5">
        <h3 class="text-sm font-semibold text-[var(--text-secondary)] uppercase tracking-wider mb-3">统计</h3>
        <div class="grid grid-cols-3 gap-3 text-center">
          <div>
            <div class="text-2xl font-bold text-[var(--text-primary)]">{{ recap.stats.sessions }}</div>
            <div class="text-xs text-[var(--text-secondary)]">会话</div>
          </div>
          <div>
            <div class="text-2xl font-bold text-[var(--text-primary)]">{{ (recap.stats.tokens / 1000).toFixed(1) }}K</div>
            <div class="text-xs text-[var(--text-secondary)]">Tokens</div>
          </div>
          <div>
            <div class="text-2xl font-bold text-[var(--text-primary)]">{{ recap.stats.files_modified }}</div>
            <div class="text-xs text-[var(--text-secondary)]">文件修改</div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
