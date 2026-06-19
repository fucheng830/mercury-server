<script setup>
import { ref, onMounted, computed } from 'vue'

const stats = ref({ total: 0, episodic: { count: 0, avg_importance: 0 }, semantic: { count: 0, avg_importance: 0 }, core: { count: 0, avg_importance: 0 }, entities: 0, relations: 0 })
const statsLoading = ref(true)

const searchQuery = ref('')
const searchLayer = ref('')
const searchResults = ref([])
const searchLoading = ref(false)
const searchError = ref('')
const activeTab = ref('all')

const editorVisible = ref(false)
const editorForm = ref({ content: '', layer: 'episodic', importance: 3, tags: '' })
const editorSaving = ref(false)

const layerColors = { episodic: 'blue', semantic: 'purple', core: 'amber' }

function layerBadgeClass(layer) {
  const color = layerColors[layer] || 'gray'
  const map = {
    blue: 'bg-blue-500/20 text-blue-400 border-blue-500/30',
    purple: 'bg-purple-500/20 text-purple-400 border-purple-500/30',
    amber: 'bg-amber-500/20 text-amber-400 border-amber-500/30',
    gray: 'bg-gray-500/20 text-gray-400 border-gray-500/30',
  }
  return map[color] || map.gray
}

function importanceStars(n) {
  return '★'.repeat(Math.round(n || 0)) + '☆'.repeat(5 - Math.round(n || 0))
}

async function fetchStats() {
  statsLoading.value = true
  try {
    const res = await fetch('/api/memory/stats')
    if (res.ok) stats.value = await res.json()
  } catch (e) {
    console.error('Failed to load stats:', e)
  } finally {
    statsLoading.value = false
  }
}

async function handleSearch() {
  searchLoading.value = true
  searchError.value = ''
  try {
    if (searchQuery.value.trim()) {
      const body = { query: searchQuery.value, limit: 20 }
      if (searchLayer.value) body.layer = searchLayer.value
      const res = await fetch('/api/memory/query', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      const data = await res.json()
      searchResults.value = data.results || []
    } else {
      const params = new URLSearchParams({ limit: '50' })
      if (searchLayer.value) params.set('layer', searchLayer.value)
      const res = await fetch(`/api/memory/recent?${params}`)
      searchResults.value = res.ok ? await res.json() : []
    }
  } catch (e) {
    searchError.value = e.message
  } finally {
    searchLoading.value = false
  }
}

async function fetchRecentMemories() {
  searchLoading.value = true
  searchError.value = ''
  try {
    const params = new URLSearchParams({ limit: '50' })
    if (activeTab.value && activeTab.value !== 'all') {
      params.set('layer', activeTab.value)
      searchLayer.value = activeTab.value
    } else {
      searchLayer.value = ''
    }
    const res = await fetch(`/api/memory/recent?${params}`)
    searchResults.value = res.ok ? await res.json() : []
  } catch (e) {
    searchError.value = e.message
  } finally {
    searchLoading.value = false
  }
}

function switchTab(tab) {
  activeTab.value = tab
  searchQuery.value = ''
  fetchRecentMemories()
}

function openNewEditor() {
  editorForm.value = { content: '', layer: 'episodic', importance: 3, tags: '' }
  editorVisible.value = true
}

async function handleEditorSave() {
  editorSaving.value = true
  try {
    const tags = editorForm.value.tags
      .split(',')
      .map(t => t.trim())
      .filter(Boolean)
    await fetch('/api/memory/write', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        target: 'memory',
        action: 'add',
        content: editorForm.value.content,
        layer: editorForm.value.layer,
        importance: Number(editorForm.value.importance),
        tags,
      }),
    })
    editorVisible.value = false
    await fetchStats()
  } catch (e) {
    alert('保存失败: ' + e.message)
  } finally {
    editorSaving.value = false
  }
}

function formatTime(ts) {
  if (!ts) return ''
  return new Date(ts).toLocaleString('zh-CN')
}

onMounted(() => {
  fetchStats()
  fetchRecentMemories()
})
</script>

<template>
  <div class="h-full overflow-auto p-6 max-w-5xl mx-auto">
    <div class="flex items-center justify-between mb-6">
      <h1 class="text-2xl font-bold text-[var(--text-primary)]">Hermes Memory</h1>
      <button @click="openNewEditor" class="px-4 py-2 rounded-lg text-sm bg-blue-500 text-white hover:bg-blue-600 transition-colors">+ 新增</button>
    </div>

    <!-- Stat Cards -->
    <div class="grid grid-cols-3 gap-4 mb-6">
      <div class="bg-[var(--bg-card)] rounded-xl border border-[var(--border-color)] p-4">
        <div class="text-xs text-blue-400 uppercase tracking-wider mb-1">Episodic</div>
        <div class="text-2xl font-bold text-[var(--text-primary)]">{{ stats.episodic?.count || 0 }}</div>
        <div class="text-xs text-[var(--text-secondary)] mt-1">Avg importance: {{ (stats.episodic?.avg_importance || 0).toFixed(1) }}</div>
      </div>
      <div class="bg-[var(--bg-card)] rounded-xl border border-[var(--border-color)] p-4">
        <div class="text-xs text-purple-400 uppercase tracking-wider mb-1">Semantic</div>
        <div class="text-2xl font-bold text-[var(--text-primary)]">{{ stats.semantic?.count || 0 }}</div>
        <div class="text-xs text-[var(--text-secondary)] mt-1">Avg importance: {{ (stats.semantic?.avg_importance || 0).toFixed(1) }}</div>
      </div>
      <div class="bg-[var(--bg-card)] rounded-xl border border-[var(--border-color)] p-4">
        <div class="text-xs text-amber-400 uppercase tracking-wider mb-1">Core</div>
        <div class="text-2xl font-bold text-[var(--text-primary)]">{{ stats.core?.count || 0 }}</div>
        <div class="text-xs text-[var(--text-secondary)] mt-1">Avg importance: {{ (stats.core?.avg_importance || 0).toFixed(1) }}</div>
      </div>
    </div>

    <!-- Tab Filters -->
    <div class="flex gap-2 mb-4">
      <button
        @click="switchTab('all')"
        :class="activeTab === 'all' ? 'bg-[var(--text-primary)] text-[var(--bg-page)]' : 'bg-[var(--bg-card)] text-[var(--text-secondary)] hover:text-[var(--text-primary)] border border-[var(--border-color)]'"
        class="px-4 py-2 rounded-lg text-sm font-medium transition-colors"
      >All</button>
      <button
        @click="switchTab('episodic')"
        :class="activeTab === 'episodic' ? 'bg-blue-500 text-white' : 'bg-blue-500/10 text-blue-500 border border-blue-500/30'"
        class="px-4 py-2 rounded-lg text-sm font-medium transition-colors"
      >Episodic ({{ stats.episodic?.count || 0 }})</button>
      <button
        @click="switchTab('semantic')"
        :class="activeTab === 'semantic' ? 'bg-purple-500 text-white' : 'bg-purple-500/10 text-purple-500 border border-purple-500/30'"
        class="px-4 py-2 rounded-lg text-sm font-medium transition-colors"
      >Semantic ({{ stats.semantic?.count || 0 }})</button>
      <button
        @click="switchTab('core')"
        :class="activeTab === 'core' ? 'bg-amber-500 text-white' : 'bg-amber-500/10 text-amber-500 border border-amber-500/30'"
        class="px-4 py-2 rounded-lg text-sm font-medium transition-colors"
      >Core ({{ stats.core?.count || 0 }})</button>
    </div>

    <!-- Search Bar -->
    <div class="flex gap-2 mb-6">
      <input
        v-model="searchQuery"
        @keyup.enter="handleSearch"
        class="flex-1 rounded-lg border border-[var(--border-color)] bg-[var(--bg-card)] px-4 py-2 text-sm text-[var(--text-primary)] focus:outline-none focus:border-blue-500/50"
        placeholder="搜索记忆..."
      />
      <button @click="handleSearch" class="px-4 py-2 rounded-lg text-sm bg-blue-500 text-white hover:bg-blue-600 transition-colors">搜索</button>
    </div>

    <!-- Search Results -->
    <div v-if="searchLoading" class="text-center py-8 text-[var(--text-secondary)]">搜索中...</div>
    <div v-else-if="searchError" class="text-center py-4 text-red-500">{{ searchError }}</div>
    <div v-else-if="searchResults.length" class="space-y-3">
      <div
        v-for="r in searchResults"
        :key="r.id"
        class="bg-[var(--bg-card)] rounded-lg border border-[var(--border-color)] p-4"
      >
        <div class="flex items-center gap-2 mb-2">
          <span :class="layerBadgeClass(r.layer)" class="px-2 py-0.5 rounded text-xs font-medium border">
            {{ r.layer }}
          </span>
          <span class="text-amber-400 text-sm">{{ importanceStars(r.importance) }}</span>
          <span v-if="r.rrf_score" class="ml-auto text-xs text-[var(--text-secondary)]">RRF: {{ Number(r.rrf_score).toFixed(4) }}</span>
        </div>
        <p class="text-sm text-[var(--text-primary)] mb-2 line-clamp-3">{{ r.content }}</p>
        <p v-if="r.summary" class="text-xs text-[var(--text-secondary)] mb-2">{{ r.summary }}</p>
        <div class="flex items-center gap-3 text-xs text-[var(--text-secondary)]">
          <span v-if="r.tags?.length" class="flex gap-1">
            <span v-for="tag in r.tags" :key="tag" class="px-1.5 py-0.5 rounded bg-[var(--bg-page)] text-[var(--text-secondary)]">{{ tag }}</span>
          </span>
          <span v-if="r.recall_count">Recall: {{ r.recall_count }}</span>
          <span v-if="r.source">Source: {{ r.source }}</span>
          <span class="ml-auto">{{ formatTime(r.created_at) }}</span>
        </div>
      </div>
    </div>
    <div v-else-if="searchQuery && !searchLoading" class="text-center py-8 text-[var(--text-secondary)]">无搜索结果</div>

    <!-- Editor Modal -->
    <div v-if="editorVisible" class="fixed inset-0 z-50 flex items-center justify-center bg-black/50" @click.self="editorVisible = false">
      <div class="bg-[var(--bg-card)] rounded-xl border border-[var(--border-color)] p-6 w-full max-w-lg shadow-xl">
        <h2 class="text-lg font-semibold text-[var(--text-primary)] mb-4">新增记忆</h2>
        <div class="space-y-4">
          <div>
            <label class="block text-sm text-[var(--text-secondary)] mb-1">内容</label>
            <textarea
              v-model="editorForm.content"
              rows="5"
              class="w-full rounded-lg border border-[var(--border-color)] bg-[var(--bg-page)] px-3 py-2 text-sm text-[var(--text-primary)] focus:outline-none focus:border-blue-500/50 resize-none"
              placeholder="输入记忆内容..."
            ></textarea>
          </div>
          <div class="grid grid-cols-3 gap-3">
            <div>
              <label class="block text-sm text-[var(--text-secondary)] mb-1">层级</label>
              <select v-model="editorForm.layer" class="w-full rounded-lg border border-[var(--border-color)] bg-[var(--bg-page)] px-3 py-2 text-sm text-[var(--text-primary)]">
                <option value="episodic">Episodic</option>
                <option value="semantic">Semantic</option>
                <option value="core">Core</option>
              </select>
            </div>
            <div>
              <label class="block text-sm text-[var(--text-secondary)] mb-1">重要性 (1-5)</label>
              <input
                v-model.number="editorForm.importance"
                type="number"
                min="1"
                max="5"
                class="w-full rounded-lg border border-[var(--border-color)] bg-[var(--bg-page)] px-3 py-2 text-sm text-[var(--text-primary)] focus:outline-none focus:border-blue-500/50"
              />
            </div>
            <div>
              <label class="block text-sm text-[var(--text-secondary)] mb-1">标签 (逗号分隔)</label>
              <input
                v-model="editorForm.tags"
                class="w-full rounded-lg border border-[var(--border-color)] bg-[var(--bg-page)] px-3 py-2 text-sm text-[var(--text-primary)] focus:outline-none focus:border-blue-500/50"
                placeholder="tag1, tag2"
              />
            </div>
          </div>
        </div>
        <div class="flex justify-end gap-3 mt-6">
          <button @click="editorVisible = false" class="px-4 py-2 rounded-lg text-sm text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition-colors">取消</button>
          <button @click="handleEditorSave" :disabled="editorSaving || !editorForm.content.trim()" class="px-4 py-2 rounded-lg text-sm bg-blue-500 text-white hover:bg-blue-600 disabled:opacity-50 transition-colors">
            {{ editorSaving ? '保存中...' : '保存' }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>
