<script setup>
import { ref, onMounted, watch, computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { DEFAULT_SOURCE, apiPath, routePath } from '../utils/source'
import { useLatestRequest } from '../composables/useLatestRequest'

const props = defineProps({
  source: { type: String, default: DEFAULT_SOURCE },
  projectPath: { type: String, default: '' },
  syncUrl: { type: Boolean, default: true },
  showProject: { type: Boolean, default: true },
  initiallyActive: { type: Boolean, default: true },
})

const emit = defineEmits(['search-active'])

const items = ref([])
const total = ref(0)
const page = ref(1)
const pages = ref(0)
const search = ref('')
const loading = ref(false)
const searchTimeout = ref(null)
const blurTimeout = ref(null)
const active = ref(props.initiallyActive)
const hasLoaded = ref(false)
const historyRequests = useLatestRequest()

const route = useRoute()
const router = useRouter()

// Restore search from URL query on mount (only when syncUrl is true)
if (props.syncUrl) {
  const initialSearch = route.query.q || ''
  search.value = initialSearch
}

const expandedItems = ref(new Set())
const clickTimer = ref(null)
const searchInput = ref(null)

function clearSearch() {
  search.value = ''
  page.value = 1
  if (props.syncUrl) {
    router.replace({
      path: routePath(props.source, '/history'),
      query: {},
    })
  }
  fetchHistory()
  searchInput.value?.focus()
}

const visiblePages = computed(() => {
  const maxVisible = 5
  const p = page.value
  const t = pages.value
  if (t <= maxVisible) return Array.from({ length: t }, (_, i) => i + 1)

  const start = Math.max(1, p - 2)
  const end = Math.min(t, start + maxVisible - 1)
  const pagesArr = []
  if (start > 1) { pagesArr.push(1); if (start > 2) pagesArr.push('...') }
  for (let i = start; i <= end; i++) pagesArr.push(i)
  if (end < t) { if (end < t - 1) pagesArr.push('...'); pagesArr.push(t) }
  return pagesArr
})

async function fetchHistory() {
  const request = historyRequests.createRequest({
    source: props.source,
    projectPath: props.projectPath,
  })
  const { source: requestSource, projectPath: requestProjectPath } = request.snapshot
  const isCurrent = () => request.isCurrent(
    snapshot => snapshot.source === props.source && snapshot.projectPath === props.projectPath
  )
  loading.value = true
  const params = new URLSearchParams({
    page: page.value,
    limit: 50,
    ...(search.value ? { search: search.value } : {}),
    ...(requestProjectPath ? { project: requestProjectPath } : {}),
  })
  try {
    const res = await fetch(`${apiPath(requestSource, '/history')}?${params}`)
    if (!res.ok) return
    const data = await res.json()
    if (!isCurrent()) return

    items.value = data.items
    total.value = data.total
    pages.value = data.pages
    hasLoaded.value = true
  } catch {
    // Keep existing results visible if a newer search/source change is in flight.
  } finally {
    if (isCurrent()) {
      loading.value = false
    }
  }
}

function onSearchInput() {
  clearTimeout(searchTimeout.value)
  clearTimeout(blurTimeout.value)
  searchTimeout.value = setTimeout(() => {
    page.value = 1
    if (props.syncUrl) {
      router.replace({
        path: routePath(props.source, '/history'),
        query: search.value ? { q: search.value } : {},
      })
    }
    fetchHistory()
  }, 300)
}

function onFocus() {
  clearTimeout(blurTimeout.value)
  if (!active.value) {
    active.value = true
    emit('search-active', true)
  }
  if (!hasLoaded.value) {
    fetchHistory()
  }
}

function onBlur() {
  clearTimeout(blurTimeout.value)
  blurTimeout.value = setTimeout(() => {
    if (!search.value) {
      active.value = false
      emit('search-active', false)
    }
  }, 200)
}

function handleClick(item) {
  clearTimeout(clickTimer.value)
  clickTimer.value = setTimeout(() => {
    const key = item.timestamp
    if (expandedItems.value.has(key)) {
      expandedItems.value.delete(key)
    } else {
      expandedItems.value.add(key)
    }
    expandedItems.value = new Set(expandedItems.value)
  }, 250)
}

function handleDblClick(item) {
  clearTimeout(clickTimer.value)
  navigateToConversation(item)
}

function navigateToConversation(item) {
  const projectId = item.project_id
  const sessionId = item.sessionId
  if (!projectId || !sessionId) return

  const source = props.projectPath ? 'project' : 'history'
  const query = {
    msgTimestamp: String(item.timestamp),
    source,
  }
  if (search.value) {
    query.q = search.value
  }
  router.push({
    path: routePath(props.source, `/projects/${projectId}/sessions/${sessionId}`),
    query,
  })
}

function resetHistoryScope() {
  const shouldRefetch = active.value || hasLoaded.value
  historyRequests.cancelRequests()
  loading.value = false
  hasLoaded.value = false
  expandedItems.value = new Set()
  items.value = []
  total.value = 0
  pages.value = 0
  if (!shouldRefetch) return

  if (page.value !== 1) {
    page.value = 1
    return
  }
  fetchHistory()
}

function formatFullTime(ts) {
  if (!ts) return ''
  const d = new Date(ts)
  return d.toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  })
}

function formatTime(ts) {
  if (!ts) return ''
  const d = new Date(ts)
  return d.toLocaleString('zh-CN', {
    hour: '2-digit',
    minute: '2-digit',
  })
}

function formatDate(ts) {
  if (!ts) return ''
  const d = new Date(ts)
  const today = new Date()
  const yesterday = new Date(today)
  yesterday.setDate(yesterday.getDate() - 1)

  if (d.toDateString() === today.toDateString()) {
    return 'Today'
  } else if (d.toDateString() === yesterday.toDateString()) {
    return 'Yesterday'
  }
  return d.toLocaleDateString('zh-CN', { month: 'short', day: 'numeric' })
}

function formatProject(p) {
  if (!p) return ''
  return p.split('/').slice(-2).join('/')
}

const groupedItems = computed(() => {
  const groups = {}
  for (const item of items.value) {
    const dateKey = formatDate(item.timestamp)
    if (!groups[dateKey]) {
      groups[dateKey] = []
    }
    groups[dateKey].push(item)
  }
  return groups
})

onMounted(() => {
  if (props.initiallyActive) {
    fetchHistory()
  }
})
watch(page, fetchHistory)
watch(() => [props.source, props.projectPath], resetHistoryScope)
</script>

<template>
  <!-- Search -->
  <div class="mb-6">
    <div class="relative group">
      <svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" class="absolute left-3.5 top-1/2 -translate-y-1/2 text-[var(--text-secondary)] group-focus-within:text-blue-500 transition-colors"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/></svg>
      <input
        ref="searchInput"
        v-model="search"
        @input="onSearchInput"
        @focus="onFocus"
        @blur="onBlur"
        @keydown.escape="clearSearch"
        placeholder="Search commands..."
        class="w-full bg-[var(--bg-card)] border border-[var(--border-color)] rounded-xl pl-10 pr-10 py-2.5 text-sm text-[var(--text-primary)] placeholder-[var(--text-secondary)] focus:outline-none focus:border-blue-500/50 focus:ring-2 focus:ring-blue-500/10 transition-all"
      />
      <button
        v-if="search"
        @click="clearSearch"
        class="absolute right-3 top-1/2 -translate-y-1/2 text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition-colors p-0.5 rounded hover:bg-[var(--bg-assistant)]"
        title="Clear search"
      >
        <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 6 6 18"/><path d="m6 6 12 12"/></svg>
      </button>
    </div>
    <div v-if="!loading && active && hasLoaded && total > 0" class="mt-2 text-xs text-[var(--text-secondary)]">
      {{ total }} result{{ total !== 1 ? 's' : '' }}
    </div>
  </div>

  <!-- Results area: show when active (focused or has search content) -->
  <template v-if="active">
    <div @mousedown.prevent>
    <div v-if="loading" class="py-12 text-center">
      <div class="inline-flex items-center gap-2 text-[var(--text-secondary)] text-sm">
        <svg class="animate-spin h-4 w-4" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24"><circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"/><path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"/></svg>
        Loading...
      </div>
    </div>

    <div v-else-if="hasLoaded && items.length === 0" class="py-12 text-center">
      <div class="text-[var(--text-secondary)]">
        <svg xmlns="http://www.w3.org/2000/svg" width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" class="mx-auto mb-3 opacity-40"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/><path d="M8 11h6"/></svg>
        <p class="text-sm">No results found</p>
        <p v-if="search" class="text-xs mt-1">Try a different search term</p>
      </div>
    </div>

    <div v-else class="space-y-8">
      <div v-for="(groupItems, dateLabel) in groupedItems" :key="dateLabel">
        <!-- Date header -->
        <div class="flex items-center gap-3 mb-3">
          <span class="text-xs font-semibold text-[var(--text-secondary)] uppercase tracking-wider bg-[var(--bg-card)] px-2.5 py-1 rounded-md">{{ dateLabel }}</span>
          <div class="flex-1 h-px bg-[var(--border-color)]"></div>
          <span class="text-xs text-[var(--text-secondary)] tabular-nums">{{ groupItems.length }}</span>
        </div>

        <!-- Timeline items -->
        <div class="relative pl-7">
          <div class="absolute left-[9px] top-2 bottom-2 w-px bg-gradient-to-b from-[var(--border-color)] via-[var(--border-color)] to-transparent"></div>

          <div
            v-for="(item, idx) in groupItems"
            :key="item.timestamp"
            class="relative pb-3 last:pb-0"
          >
            <div
              class="absolute -left-7 top-3.5 w-[18px] h-[18px] rounded-full flex items-center justify-center"
              :class="expandedItems.has(item.timestamp) ? 'bg-blue-500' : 'bg-[var(--bg-page)] border-2 border-[var(--border-color)]'"
            >
              <svg v-if="expandedItems.has(item.timestamp)" xmlns="http://www.w3.org/2000/svg" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="m6 9 6 6 6-6"/></svg>
            </div>

            <div
              data-history-item
              class="rounded-lg px-4 py-3 transition-all cursor-pointer border"
              :class="expandedItems.has(item.timestamp)
                ? 'bg-blue-500/5 border-blue-500/20 shadow-sm'
                : 'bg-[var(--bg-card)]/40 border-transparent hover:border-[var(--border-color)] hover:bg-[var(--bg-card)]/70'"
              @click="handleClick(item)"
              @dblclick="handleDblClick(item)"
            >
              <div class="flex items-start justify-between gap-3">
                <div class="flex-1 min-w-0">
                  <p class="text-sm text-[var(--text-primary)] break-words whitespace-pre-wrap leading-relaxed">{{ item.display }}</p>
                  <div v-if="showProject" class="flex items-center gap-2 mt-2 flex-wrap">
                    <span
                      v-if="item.project"
                      class="inline-flex items-center gap-1 text-xs text-[var(--text-secondary)] bg-[var(--bg-assistant)] px-2 py-0.5 rounded-full"
                    >
                      <svg xmlns="http://www.w3.org/2000/svg" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z"/></svg>
                      {{ formatProject(item.project) }}
                    </span>
                    <span v-if="item.sessionId" class="text-xs text-[var(--text-secondary)] opacity-60">
                      {{ item.sessionId.slice(0, 8) }}
                    </span>
                  </div>
                </div>
                <span class="text-xs text-[var(--text-secondary)] flex-shrink-0 whitespace-nowrap tabular-nums mt-0.5">{{ formatTime(item.timestamp) }}</span>
              </div>
              <div v-if="expandedItems.has(item.timestamp)" class="mt-3 pt-3 border-t border-[var(--border-color)]">
                <div class="grid grid-cols-1 sm:grid-cols-3 gap-2">
                  <div class="bg-[var(--bg-page)] rounded-md px-3 py-2">
                    <div class="text-[10px] uppercase tracking-wider text-[var(--text-secondary)] mb-0.5">Path</div>
                    <div class="text-xs text-[var(--text-primary)] truncate" :title="item.project">{{ formatProject(item.project) }}</div>
                  </div>
                  <div class="bg-[var(--bg-page)] rounded-md px-3 py-2">
                    <div class="text-[10px] uppercase tracking-wider text-[var(--text-secondary)] mb-0.5">Time</div>
                    <div class="text-xs text-[var(--text-primary)]">{{ formatFullTime(item.timestamp) }}</div>
                  </div>
                  <div class="bg-[var(--bg-page)] rounded-md px-3 py-2">
                    <div class="text-[10px] uppercase tracking-wider text-[var(--text-secondary)] mb-0.5">Session</div>
                    <div class="text-xs text-[var(--text-primary)] font-mono">{{ item.sessionId?.slice(0, 12) }}...</div>
                  </div>
                </div>
                <div class="mt-2 text-xs text-[var(--text-secondary)] flex items-center gap-1">
                  <svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M15 3h6v6"/><path d="M10 14 21 3"/><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/></svg>
                  Double-click to open conversation
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- Pagination -->
    <div v-if="pages > 1" class="flex items-center justify-center gap-1.5 mt-8">
      <button
        @click="page = 1"
        :disabled="page <= 1"
        class="px-2 py-1.5 rounded-lg text-sm text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:bg-[var(--bg-card)] disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
        title="First page"
      >
        <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m11 17-5-5 5-5"/><path d="m18 17-5-5 5-5"/></svg>
      </button>
      <button
        @click="page = Math.max(1, page - 1)"
        :disabled="page <= 1"
        class="px-2 py-1.5 rounded-lg text-sm text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:bg-[var(--bg-card)] disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
        title="Previous page"
      >
        <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m15 18-6-6 6-6"/></svg>
      </button>

      <template v-for="p in visiblePages" :key="p">
        <span v-if="p === '...'" class="px-1 text-[var(--text-secondary)] text-xs">...</span>
        <button
          v-else
          @click="page = p"
          class="min-w-[32px] h-8 rounded-lg text-sm transition-colors"
          :class="p === page
            ? 'bg-blue-500 text-white font-medium'
            : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:bg-[var(--bg-card)]'"
        >
          {{ p }}
        </button>
      </template>

      <button
        @click="page = Math.min(pages, page + 1)"
        :disabled="page >= pages"
        class="px-2 py-1.5 rounded-lg text-sm text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:bg-[var(--bg-card)] disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
        title="Next page"
      >
        <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m9 18 6-6-6-6"/></svg>
      </button>
      <button
        @click="page = pages"
        :disabled="page >= pages"
        class="px-2 py-1.5 rounded-lg text-sm text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:bg-[var(--bg-card)] disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
        title="Last page"
      >
        <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m13 17 5-5-5-5"/><path d="m6 17 5-5-5-5"/></svg>
      </button>
    </div>
    </div>
  </template>
</template>
