<script setup>
import { ref } from 'vue'
import { useSessionsApi } from '../composables/useSessionsApi'

const api = useSessionsApi()

const query = ref('')
const namespace = ref('claude')
const results = ref([])
const loading = ref(false)
const error = ref('')
const searched = ref(false)
let timer = null

async function runSearch() {
  const q = query.value.trim()
  if (!q) {
    results.value = []
    searched.value = false
    return
  }
  loading.value = true
  error.value = ''
  try {
    results.value = await api.searchMessages({
      query: q,
      namespace: namespace.value,
      limit: 20,
      context_window: 3,
    })
    searched.value = true
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

function onInput() {
  clearTimeout(timer)
  timer = setTimeout(runSearch, 300)
}

function relTime(ts) {
  if (!ts) return '-'
  const diff = Date.now() - new Date(ts).getTime()
  const day = 86400000
  if (diff < 3600000) return Math.max(1, Math.floor(diff / 60000)) + ' 分钟前'
  if (diff < day) return Math.floor(diff / 3600000) + ' 小时前'
  if (diff < 7 * day) return Math.floor(diff / day) + ' 天前'
  if (diff < 30 * day) return Math.floor(diff / (7 * day)) + ' 周前'
  return new Date(ts).toLocaleDateString('zh-CN')
}

function preview(text, max = 280) {
  const s = String(text || '').replace(/\s+/g, ' ').trim()
  return s.length > max ? s.slice(0, max) + '...' : s
}

function sessionLabel(s) {
  if (!s) return '未知会话'
  const path = s.project_path || s.project_id || ''
  const segs = String(path).replace(/\\/g, '/').split('/').filter(Boolean)
  return segs.slice(-2).join('/') || path || String(s.session_id || '').slice(0, 8) || '会话'
}

function roleClass(role) {
  return role === 'user' ? 'text-[#1890FF]' : 'text-[var(--text-secondary)]'
}
</script>

<template>
  <div class="flex h-full flex-col overflow-hidden bg-[var(--bg-page)]">
    <!-- sticky 搜索栏 -->
    <div class="flex-shrink-0 border-b border-[var(--border-color)] bg-[var(--bg-sidebar)] px-6 py-4">
      <div class="mx-auto flex max-w-4xl items-center gap-3">
        <input
          v-model="query"
          @input="onInput"
          @keyup.enter="runSearch"
          class="h-11 min-w-0 flex-1 rounded-lg border border-[var(--border-color)] bg-[var(--bg-card)] px-4 text-sm text-[var(--text-primary)] outline-none focus:border-[#1890FF]"
          placeholder="搜索历史对话消息..."
        />
        <select
          v-model="namespace"
          @change="runSearch"
          class="h-11 rounded-lg border border-[var(--border-color)] bg-[var(--bg-card)] px-3 text-sm text-[var(--text-primary)] outline-none"
        >
          <option value="claude">claude</option>
          <option value="shared">shared</option>
        </select>
        <span class="w-12 whitespace-nowrap text-sm text-[var(--text-secondary)]">
          {{ searched ? `${results.length} 条` : '' }}
        </span>
      </div>
    </div>

    <!-- 结果流 -->
    <div class="min-h-0 flex-1 overflow-auto">
      <div class="mx-auto max-w-4xl px-6 py-6">
        <div v-if="loading" class="py-16 text-center text-sm text-[var(--text-secondary)]">搜索中...</div>
        <div v-else-if="error" class="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-600">{{ error }}</div>
        <div v-else-if="!searched" class="py-16 text-center text-sm text-[var(--text-secondary)]">
          输入关键词搜索历史对话消息（命中消息 + 前后上下文）
        </div>
        <div v-else-if="results.length === 0" class="py-16 text-center text-sm text-[var(--text-secondary)]">
          无匹配结果
        </div>
        <div v-else class="space-y-5">
          <div
            v-for="hit in results"
            :key="hit.id"
            class="overflow-hidden rounded-xl border border-[var(--border-color)] bg-[var(--bg-card)] shadow-sm"
          >
            <!-- session 元数据 -->
            <div class="flex items-center gap-2 border-b border-[var(--border-color)] bg-[var(--bg-sidebar)] px-4 py-2 text-[12px] text-[var(--text-secondary)]">
              <span class="font-medium text-[var(--text-primary)]">{{ sessionLabel(hit.session) }}</span>
              <span>·</span>
              <span>{{ relTime(hit.session?.last_ts || hit.created_ts) }}</span>
              <span>·</span>
              <span>{{ hit.session?.message_count ?? '?' }} 条</span>
            </div>

            <!-- 上下文窗口 -->
            <div class="space-y-2 px-4 py-3">
              <div v-for="m in hit.prev" :key="`p${hit.id}-${m.seq}`" class="flex gap-3 text-sm">
                <span class="w-16 flex-shrink-0 text-[11px] uppercase" :class="roleClass(m.role)">{{ m.role }}</span>
                <span class="text-[var(--text-secondary)] opacity-70">{{ preview(m.content_text, 200) }}</span>
              </div>

              <div class="flex gap-3 rounded-lg border-l-2 border-[#1890FF] bg-[#1890FF]/5 px-3 py-2 text-sm">
                <span class="w-16 flex-shrink-0 text-[11px] font-medium uppercase" :class="roleClass(hit.role)">{{ hit.role }}</span>
                <span class="text-[var(--text-primary)]">{{ preview(hit.content_text, 400) }}</span>
              </div>

              <div v-for="m in hit.next" :key="`n${hit.id}-${m.seq}`" class="flex gap-3 text-sm">
                <span class="w-16 flex-shrink-0 text-[11px] uppercase" :class="roleClass(m.role)">{{ m.role }}</span>
                <span class="text-[var(--text-secondary)] opacity-70">{{ preview(m.content_text, 200) }}</span>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
