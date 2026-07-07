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
      query: q, namespace: namespace.value, limit: 20, context_window: 3,
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
  const s = sanitize(text).replace(/\s+/g, ' ').trim()
  return s.length > max ? s.slice(0, max) + '...' : s
}

// Drop unpaired surrogates that would otherwise render as  replacement chars
// (some source JSONLs contain them; JSON disallows them but stores anyway).
function sanitize(text) {
  return String(text || '').replace(
    /[\ud800-\udbff](?![\udc00-\udfff])|(?:[^\ud800-\udbff]|^)[\udc00-\udfff]/g,
    '',
  )
}

function sessionLabel(s) {
  if (!s) return '未知会话'
  const path = s.project_path || s.project_id || ''
  const segs = String(path).replace(/\\/g, '/').split('/').filter(Boolean)
  return segs.slice(-2).join('/') || path || String(s.session_id || '').slice(0, 8) || '会话'
}
</script>

<template>
  <div class="h-full overflow-auto">
    <div class="mx-auto max-w-5xl p-6">
      <!-- Page header (对齐 Dashboard / MemoryView) -->
      <h1 class="text-2xl font-bold text-[var(--text-primary)]">对话搜索</h1>
      <p class="mt-1 text-sm text-[var(--text-secondary)]">搜索历史对话消息，命中消息 + 前后上下文窗口</p>

      <!-- Search bar -->
      <div class="mt-6 flex gap-2">
        <input
          v-model="query"
          @input="onInput"
          @keyup.enter="runSearch"
          class="h-11 flex-1 rounded-lg border border-[var(--border-color)] bg-[var(--bg-card)] px-4 text-sm text-[var(--text-primary)] outline-none focus:border-blue-500/50"
          placeholder="搜索对话消息..."
        />
        <select
          v-model="namespace"
          @change="runSearch"
          class="h-11 rounded-lg border border-[var(--border-color)] bg-[var(--bg-card)] px-3 text-sm text-[var(--text-primary)] outline-none"
        >
          <option value="claude">claude</option>
          <option value="shared">shared</option>
        </select>
      </div>

      <!-- Results -->
      <div class="mt-6">
        <div v-if="loading" class="py-12 text-center text-sm text-[var(--text-secondary)]">搜索中...</div>
        <div v-else-if="error" class="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-600">{{ error }}</div>
        <div v-else-if="!searched" class="py-16 text-center text-sm text-[var(--text-secondary)]">
          输入关键词开始搜索
        </div>
        <template v-else>
          <div class="mb-4 text-sm text-[var(--text-secondary)]">{{ results.length }} 条结果</div>
          <div v-if="results.length === 0" class="py-16 text-center text-sm text-[var(--text-secondary)]">
            无匹配结果
          </div>
          <div v-else class="space-y-3">
            <div
              v-for="hit in results"
              :key="hit.id"
              class="rounded-xl border border-[var(--border-color)] bg-[var(--bg-card)] p-4 transition-colors hover:border-blue-500/30"
            >
              <!-- Session metadata strip -->
              <div class="mb-3 flex items-center gap-2 border-b border-[var(--border-color)] pb-3 text-xs text-[var(--text-secondary)]">
                <span class="font-medium text-[var(--text-primary)]">{{ sessionLabel(hit.session) }}</span>
                <span>·</span>
                <span>{{ relTime(hit.session?.last_ts || hit.created_ts) }}</span>
                <span>·</span>
                <span>{{ hit.session?.message_count ?? '?' }} 条消息</span>
                <span class="ml-auto">RRF {{ Number(hit.rrf_score).toFixed(4) }}</span>
              </div>

              <!-- Context window -->
              <div class="space-y-2">
                <div
                  v-for="m in hit.prev"
                  :key="`p${hit.id}-${m.seq}`"
                  class="flex gap-3 text-sm text-[var(--text-secondary)] opacity-70"
                >
                  <span class="w-12 flex-shrink-0 text-[11px] uppercase">{{ m.role }}</span>
                  <span class="min-w-0">{{ preview(m.content_text, 160) }}</span>
                </div>

                <!-- Hit (强调) -->
                <div class="flex gap-3 rounded-lg border-l-2 border-blue-500 bg-blue-500/5 px-3 py-2">
                  <span class="w-12 flex-shrink-0 text-[11px] font-medium uppercase text-blue-500">{{ hit.role }}</span>
                  <span class="min-w-0 text-[var(--text-primary)]">{{ preview(hit.content_text, 500) }}</span>
                </div>

                <div
                  v-for="m in hit.next"
                  :key="`n${hit.id}-${m.seq}`"
                  class="flex gap-3 text-sm text-[var(--text-secondary)] opacity-70"
                >
                  <span class="w-12 flex-shrink-0 text-[11px] uppercase">{{ m.role }}</span>
                  <span class="min-w-0">{{ preview(m.content_text, 160) }}</span>
                </div>
              </div>
            </div>
          </div>
        </template>
      </div>
    </div>
  </div>
</template>
