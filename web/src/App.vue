<script setup>
import { ref, onMounted, computed, watch } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useTheme } from './composables/useTheme'
import { DEFAULT_SOURCE, routePath, sourceFromRoute } from './utils/source'

const router = useRouter()
const route = useRoute()
const { isDark, toggleTheme, initTheme } = useTheme()
const sidebarOpen = ref(true)

const fallbackSources = [
  { id: 'claude', name: 'Claude', available: true },
  { id: 'codex', name: 'Codex', available: true },
]
const sources = ref([...fallbackSources])
const activeSource = computed(() => sourceFromRoute(route))

// Memory stage counts for sidebar badges
const counts = ref({ memory: 0, candidate: 0, observation: 0 })

const WORKSPACE = ['/memory', '/candidates', '/observations', '/graph']

const icon = {
  memory: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2a8 8 0 0 0-8 8c0 3.4 2.1 6.3 5 7.5V20h6v-2.5c2.9-1.2 5-4.1 5-7.5a8 8 0 0 0-8-8Z"/><path d="M9 14h6"/></svg>',
  candidate: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2v8"/><path d="m4.5 10.5 1.5 1.5"/><path d="m18 12 1.5-1.5"/><path d="M12 14a4 4 0 0 0-4-4"/><circle cx="12" cy="8" r="1"/></svg>',
  observation: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7Z"/><circle cx="12" cy="12" r="3"/></svg>',
  graph: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="3"/><circle cx="4" cy="6" r="2"/><circle cx="20" cy="18" r="2"/><line x1="6" x2="10" y1="7" y2="11"/><line x1="14" x2="18" y1="13" y2="17"/></svg>',
  activity: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 12h-4l-3 9L9 3l-3 9H2"/></svg>',
  ops: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2Z"/><circle cx="12" cy="12" r="3"/></svg>',
}

const groups = computed(() => [
  {
    label: '主资源',
    items: [
      { path: '/memory', label: '记忆', icon: icon.memory, count: counts.value.memory, workspace: true },
      { path: '/candidates', label: '候选', icon: icon.candidate, count: counts.value.candidate, workspace: true },
      { path: '/observations', label: '观察', icon: icon.observation, count: counts.value.observation, workspace: true },
    ],
  },
  {
    label: null,
    items: [
      { path: '/graph', label: '知识图谱', icon: icon.graph, workspace: true },
      { path: '/history', label: '活动', icon: icon.activity, source: true },
      { path: '/', label: '运维', icon: icon.ops, source: true },
    ],
  },
])

onMounted(async () => {
  initTheme()
  await fetchSources()
  restoreStoredSource()
  fetchCounts()
})

async function fetchSources() {
  try {
    const res = await fetch('/api/sources')
    if (!res.ok) return
    sources.value = await res.json()
  } catch {
    sources.value = [...fallbackSources]
  }
}

async function fetchCounts() {
  try {
    const res = await fetch('/api/memory/stats')
    if (!res.ok) return
    const s = await res.json()
    counts.value = {
      memory: s.memory?.count || 0,
      candidate: s.candidate?.count || 0,
      observation: s.observation?.count || 0,
    }
  } catch { /* ignore */ }
}

function toggleSidebar() { sidebarOpen.value = !sidebarOpen.value }

function navPath(item) {
  return item.workspace ? item.path : routePath(activeSource.value, item.path)
}

function go(item) { router.push(navPath(item)) }

function isActive(item) {
  if (item.workspace) {
    return route.path === item.path || route.path.startsWith(item.path + '/')
  }
  const target = routePath(activeSource.value, item.path)
  if (item.path === '/') return route.path === target
  return route.path === target || route.path.startsWith(target + '/')
}

function legacyPathFromRoute() {
  if (!route.params.source) return route.path
  const prefix = `/sources/${route.params.source}`
  return route.path.startsWith(prefix) ? route.path.slice(prefix.length) || '/' : route.path
}

function onSourceChange(event) {
  const nextSource = event.target.value
  localStorage.setItem('active_source', nextSource)
  const currentPath = legacyPathFromRoute()
  const nextPath = currentPath === '/plans' && nextSource !== DEFAULT_SOURCE
    ? routePath(nextSource, '/')
    : routePath(nextSource, currentPath)
  if (nextPath !== route.path) router.push({ path: nextPath, query: route.query })
}

function isWorkspaceRoute() {
  return WORKSPACE.some(p => route.path === p || route.path.startsWith(p + '/'))
}

function restoreStoredSource() {
  const storedSource = localStorage.getItem('active_source')
  const canUseStoredSource = sources.value.some(s => s.id === storedSource && s.available)
  if (isWorkspaceRoute()) {
    localStorage.setItem('active_source', activeSource.value)
    return
  }
  if (!route.params.source && route.path !== '/plans' && canUseStoredSource && storedSource !== activeSource.value) {
    const nextPath = routePath(storedSource, route.path)
    if (nextPath !== route.path) {
      router.replace({ path: nextPath, query: route.query })
      return
    }
  }
  localStorage.setItem('active_source', activeSource.value)
}

watch(activeSource, source => { localStorage.setItem('active_source', source) })
</script>

<template>
  <div class="flex h-screen overflow-hidden bg-[var(--bg-page)]">
    <!-- Sidebar -->
    <aside
      :class="[sidebarOpen ? 'w-[210px]' : 'w-12']"
      class="flex-shrink-0 bg-[var(--bg-sidebar)] border-r border-[var(--border-color)] transition-all duration-200 flex flex-col"
    >
      <!-- Logo -->
      <div class="h-12 flex items-center px-3 border-b border-[var(--border-color)] gap-2">
        <svg class="w-7 h-7 flex-shrink-0 text-[var(--text-primary)]" viewBox="0 0 100 100" fill="none" stroke="currentColor" stroke-width="8" stroke-linejoin="round">
          <polygon points="50,7 89,29.5 89,70.5 50,93 11,70.5 11,29.5"/>
          <path d="M30 72 L30 34 L50 54 L70 34 L70 72" stroke-linecap="round"/>
        </svg>
        <span v-if="sidebarOpen" class="font-semibold text-sm text-[var(--text-primary)] truncate">Mercury Memory</span>
      </div>

      <!-- Nav -->
      <nav class="flex-1 py-2 px-1.5 overflow-y-auto">
        <template v-for="(group, gi) in groups" :key="gi">
          <div v-if="group.label && sidebarOpen" class="px-2.5 pt-3 pb-1 text-[11px] uppercase tracking-wider text-[var(--text-secondary)]">{{ group.label }}</div>
          <button
            v-for="item in group.items" :key="item.path"
            @click="go(item)"
            :class="[isActive(item) ? 'text-[#1890FF] bg-[#1890FF]/10' : 'text-[var(--text-secondary)] hover:bg-[var(--bg-card)] hover:text-[var(--text-primary)]']"
            class="relative w-full flex items-center gap-2 px-2.5 py-1.5 rounded-md text-sm transition-colors"
          >
            <div v-if="isActive(item)" class="absolute left-0 top-1/2 -translate-y-1/2 w-0.5 h-4 bg-[#1890FF] rounded-r"></div>
            <span class="flex-shrink-0 opacity-80" v-html="item.icon"></span>
            <span v-if="sidebarOpen" class="truncate flex-1 text-left">{{ item.label }}</span>
            <span v-if="sidebarOpen && item.count != null && item.count > 0"
              class="text-[11px] px-1.5 py-px rounded-full"
              :class="item.path === '/candidates' ? 'bg-[#CF1322] text-white' : 'bg-[var(--bg-card)] text-[var(--text-secondary)]'"
            >{{ item.count }}</span>
          </button>
          <div v-if="gi === 0" class="my-1.5 mx-2 border-t border-[var(--border-color)]"></div>
        </template>
      </nav>

      <!-- Bottom -->
      <div class="border-t border-[var(--border-color)]">
        <div v-if="sidebarOpen && sources.length" class="px-3 py-2 border-b border-[var(--border-color)]">
          <label class="block text-[11px] text-[var(--text-secondary)] mb-1" for="source-select">Source</label>
          <select id="source-select" :value="activeSource" @change="onSourceChange"
            class="w-full rounded border border-[var(--border-color)] bg-[var(--bg-card)] px-2 py-1 text-[13px] text-[var(--text-primary)] focus:outline-none">
            <option v-for="s in sources" :key="s.id" :value="s.id" :disabled="!s.available">{{ s.name }}</option>
          </select>
        </div>

        <div class="flex items-center gap-1 px-2 py-1.5">
          <button @click="toggleTheme" class="flex-1 flex items-center justify-center gap-1.5 py-1.5 rounded text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:bg-[var(--bg-card)] text-[13px]" :title="isDark ? 'Light' : 'Dark'">
            <svg v-if="isDark" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M6.3 17.7l-1.4 1.4M19.1 4.9l-1.4 1.4"/></svg>
            <svg v-else width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"/></svg>
            <span v-if="sidebarOpen">{{ isDark ? 'Light' : 'Dark' }}</span>
          </button>
          <button @click="toggleSidebar" class="py-1.5 px-2 rounded text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:bg-[var(--bg-card)]">
            <svg :class="[sidebarOpen ? 'rotate-180' : '']" class="transition-transform" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m15 18-6-6 6-6"/></svg>
          </button>
        </div>
      </div>
    </aside>

    <!-- Main -->
    <main class="flex-1 overflow-hidden">
      <router-view />
    </main>
  </div>
</template>
