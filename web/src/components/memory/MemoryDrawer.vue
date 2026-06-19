<script setup>
import { computed } from 'vue'
import TypeBadge from './TypeBadge.vue'

const props = defineProps({
  open: { type: Boolean, default: false },
  item: { type: Object, default: null },
  typeMap: { type: Object, default: () => ({}) },
})
const emit = defineEmits(['close', 'confirmed', 'rejected'])

const colorOf = (name) => props.typeMap[name]?.color || 'gray'

const stars = computed(() => {
  const n = Math.round(props.item?.importance || 0)
  return '★'.repeat(n) + '☆'.repeat(5 - n)
})

function fmt(ts) {
  if (!ts) return '—'
  return new Date(ts).toLocaleString('zh-CN', { hour12: false })
}
</script>

<template>
  <transition name="drawer">
    <div v-if="open && item" class="fixed inset-0 z-40 flex justify-end">
      <div class="absolute inset-0 bg-black/30" @click="emit('close')"></div>
      <aside
        class="relative h-full w-[400px] max-w-[90vw] bg-[var(--bg-page)] border-l border-[var(--border-color)] shadow-xl flex flex-col"
      >
        <!-- header -->
        <div class="flex items-center gap-2 px-4 h-12 border-b border-[var(--border-color)] flex-shrink-0">
          <TypeBadge :name="item.type" :color="colorOf(item.type)" />
          <span class="text-xs text-[var(--text-secondary)]">{{ item.stage }}</span>
          <span class="ml-auto cursor-pointer text-[var(--text-secondary)] hover:text-[var(--text-primary)]" @click="emit('close')">✕</span>
        </div>

        <!-- body -->
        <div class="flex-1 overflow-auto p-4 space-y-4 text-sm">
          <div>
            <div class="text-[11px] uppercase tracking-wide text-[var(--text-secondary)] mb-1">内容</div>
            <p class="text-[var(--text-primary)] whitespace-pre-wrap leading-relaxed">{{ item.content }}</p>
          </div>
          <div v-if="item.summary">
            <div class="text-[11px] uppercase tracking-wide text-[var(--text-secondary)] mb-1">摘要</div>
            <p class="text-[var(--text-secondary)]">{{ item.summary }}</p>
          </div>

          <div class="grid grid-cols-2 gap-x-4 gap-y-2 text-xs border-t border-[var(--border-color)] pt-3">
            <div><span class="text-[var(--text-secondary)]">项目</span><br>{{ item.project_id || '—' }}</div>
            <div><span class="text-[var(--text-secondary)]">作用域</span><br>{{ item.scope }}</div>
            <div><span class="text-[var(--text-secondary)]">状态</span><br>
              <span class="inline-flex items-center gap-1">
                <span class="w-1.5 h-1.5 rounded-full"
                  :style="{ background: item.status === 'active' ? '#52C41A' : item.status === 'superseded' ? '#8C8C8C' : '#BFBFBF' }"></span>
                {{ item.status }}
              </span>
            </div>
            <div><span class="text-[var(--text-secondary)]">重要性</span><br><span class="text-amber-500">{{ stars }}</span></div>
            <div><span class="text-[var(--text-secondary)]">来源</span><br>{{ item.source || '—' }}</div>
            <div><span class="text-[var(--text-secondary)]">命名空间</span><br>{{ item.namespace }}</div>
            <div><span class="text-[var(--text-secondary)]">创建</span><br>{{ fmt(item.created_at) }}</div>
            <div><span class="text-[var(--text-secondary)]">更新</span><br>{{ fmt(item.updated_at) }}</div>
            <div><span class="text-[var(--text-secondary)]">召回</span><br>{{ item.recall_count ?? 0 }}</div>
            <div v-if="item.rrf_score"><span class="text-[var(--text-secondary)]">RRF</span><br>{{ Number(item.rrf_score).toFixed(4) }}</div>
          </div>

          <div v-if="item.tags?.length">
            <div class="text-[11px] uppercase tracking-wide text-[var(--text-secondary)] mb-1">标签</div>
            <div class="flex flex-wrap gap-1">
              <span v-for="t in item.tags" :key="t" class="px-1.5 py-0.5 rounded text-[11px] bg-[var(--bg-card)] text-[var(--text-secondary)]">{{ t }}</span>
            </div>
          </div>
        </div>

        <!-- footer -->
        <div class="flex items-center gap-2 px-4 h-14 border-t border-[var(--border-color)] flex-shrink-0">
          <template v-if="item.stage === 'candidate'">
            <button @click="emit('rejected', item)" class="px-3 py-1.5 rounded text-xs border border-[var(--border-color)] text-[var(--text-secondary)] hover:text-[var(--text-primary)]">驳回</button>
            <button @click="emit('confirmed', item)" class="px-3 py-1.5 rounded text-xs bg-[#1890FF] text-white hover:opacity-90">确认 → 记忆</button>
          </template>
          <span v-else class="text-xs text-[var(--text-secondary)]">仅候选阶段可确认/驳回</span>
        </div>
      </aside>
    </div>
  </transition>
</template>

<style scoped>
.drawer-enter-active, .drawer-leave-active { transition: opacity .18s ease; }
.drawer-enter-active aside, .drawer-leave-active aside { transition: transform .22s ease; }
.drawer-enter-from, .drawer-leave-to { opacity: 0; }
.drawer-enter-from aside, .drawer-leave-to aside { transform: translateX(100%); }
</style>
