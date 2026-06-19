<script setup>
import { ref, computed, watch, onMounted } from 'vue'
import { useMemoryApi } from '../composables/useMemoryApi'
import TypeBadge from '../components/memory/TypeBadge.vue'
import MemoryDrawer from '../components/memory/MemoryDrawer.vue'

const props = defineProps({
  stage: { type: String, default: 'memory' }, // memory | candidate | observation
})

const api = useMemoryApi()

const items = ref([])
const total = ref(0)
const page = ref(1)
const size = ref(25)
const pages = ref(1)
const loading = ref(false)
const error = ref('')

const search = ref('')
const searchMode = ref(false)
const searchResults = ref([])

const typeSel = ref([])        // selected type names
const scopeSel = ref('')       // '' | repo | global | user
const statusSel = ref('')      // '' | active | archived | superseded
const sort = ref('updated_at')
const order = ref('desc')

const typesList = ref([])
const typeMap = computed(() => Object.fromEntries(typesList.value.map(t => [t.name, t])))
const projects = ref([])
const projectName = computed(() => {
  const m = Object.fromEntries(projects.value.map(p => [p.id, p.name]))
  return (id) => (id && m[id]) ? m[id] : (id ? String(id).slice(0, 8) : '—')
})

const stats = ref({ memory: { count: 0 }, candidate: { count: 0 }, observation: { count: 0 } })

const selectedIds = ref(new Set())
const drawerItem = ref(null)
const drawerOpen = ref(false)

const titleMap = { memory: 'All memories', candidate: 'Candidates', observation: 'Observations' }
const title = computed(() => titleMap[props.stage] || 'Memories')

async function loadList() {
  loading.value = true
  error.value = ''
  try {
    const data = await api.list({
      stage: props.stage,
      types: typeSel.value,
      scope: scopeSel.value,
      status: statusSel.value || (props.stage === 'candidate' ? '' : 'active'),
      sort: sort.value,
      order: order.value,
      page: page.value,
      size: size.value,
    })
    items.value = data.items || []
    total.value = data.total || 0
    pages.value = data.pages || 1
    searchMode.value = false
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

let searchTimer = null
async function runSearch() {
  const q = search.value.trim()
  if (!q) { loadList(); return }
  loading.value = true
  error.value = ''
  try {
    const data = await api.query({
      query: q,
      stage: props.stage,
      types: typeSel.value.length ? typeSel.value : undefined,
      scopes: scopeSel.value ? [scopeSel.value] : undefined,
      statuses: statusSel.value ? [statusSel.value] : undefined,
      limit: 50,
    })
    searchResults.value = data.results || []
    searchMode.value = true
    total.value = searchResults.value.length
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

function onSearchInput() {
  clearTimeout(searchTimer)
  searchTimer = setTimeout(runSearch, 350)
}

const displayItems = computed(() => (searchMode.value ? searchResults.value : items.value))

function toggleType(name) {
  const i = typeSel.value.indexOf(name)
  if (i >= 0) typeSel.value.splice(i, 1)
  else typeSel.value.push(name)
  page.value = 1
  if (search.value.trim()) runSearch(); else loadList()
}

function onFilterChange() {
  page.value = 1
  if (search.value.trim()) runSearch(); else loadList()
}

function clearFilters() {
  typeSel.value = []; scopeSel.value = ''; statusSel.value = ''
  search.value = ''
  page.value = 1
  loadList()
}

function goto(p) {
  if (p < 1 || p > pages.value) return
  page.value = p
  loadList()
}

function openDrawer(item) { drawerItem.value = item; drawerOpen.value = true }
function closeDrawer() { drawerOpen.value = false }

async function onConfirm(item) {
  try { await api.confirm(item.id); drawerOpen.value = false; await loadList(); await loadStats() }
  catch (e) { alert('确认失败: ' + e.message) }
}
async function onReject(item) {
  try { await api.reject(item.id); drawerOpen.value = false; await loadList(); await loadStats() }
  catch (e) { alert('驳回失败: ' + e.message) }
}

function toggleSelect(id) {
  const s = new Set(selectedIds.value)
  s.has(id) ? s.delete(id) : s.add(id)
  selectedIds.value = s
}
function toggleSelectAll() {
  const ids = displayItems.value.map(i => i.id)
  const allSelected = ids.every(id => selectedIds.value.has(id))
  const s = new Set(selectedIds.value)
  if (allSelected) ids.forEach(id => s.delete(id))
  else ids.forEach(id => s.add(id))
  selectedIds.value = s
}

async function loadStats() {
  try { stats.value = await api.stats() } catch { /* ignore */ }
}

function relTime(ts) {
  if (!ts) return ''
  const t = new Date(ts).getTime()
  const diff = Date.now() - t
  const day = 86400000
  if (diff < day) return Math.max(1, Math.floor(diff / 3600000)) + '小时前'
  if (diff < 7 * day) return Math.floor(diff / day) + '天前'
  if (diff < 30 * day) return Math.floor(diff / (7 * day)) + '周前'
  return Math.floor(diff / (30 * day)) + '月前'
}

const scopeIcon = { repo: '📁', global: '🌐', user: '👤' }

onMounted(async () => {
  await Promise.all([api.loadTypes(), api.loadProjects()])
  typesList.value = api.types.value
  projects.value = api.projects.value
  await Promise.all([loadList(), loadStats()])
})

watch(() => props.stage, () => { page.value = 1; search.value = ''; loadList() })
</script>

<template>
  <div class="h-full flex flex-col bg-[var(--bg-page)]">
    <!-- Top bar -->
    <div class="flex items-center px-5 h-14 border-b border-[var(--border-color)] flex-shrink-0">
      <div class="min-w-0">
        <h1 class="text-base font-semibold text-[var(--text-primary)] truncate">
          {{ title }} · <span class="text-[var(--text-secondary)] font-normal">{{ total }} 条</span>
          <span class="ml-2 text-[13px] text-[#52C41A]">活跃 {{ stats.memory?.count || 0 }}</span>
          <span class="ml-2 text-[13px] text-[#D48806]">待审 {{ stats.candidate?.count || 0 }}</span>
        </h1>
      </div>
      <div class="ml-auto flex items-center gap-3 text-[13px] text-[var(--text-secondary)]">
        <span class="hidden md:inline">已连接 · 127.0.0.1</span>
        <button class="px-2.5 py-1 rounded bg-[#1A1A1A] text-white text-[13px] hover:opacity-90">+ 新建</button>
      </div>
    </div>

    <!-- Search -->
    <div class="px-5 pt-3 flex-shrink-0">
      <div class="relative">
        <input
          v-model="search"
          @input="onSearchInput"
          class="w-full rounded border border-[var(--border-color)] bg-[var(--bg-page)] px-3 py-1.5 text-sm text-[var(--text-primary)] focus:outline-none focus:border-[#1890FF]"
          placeholder="全文搜索 (FTS5 + RRF 融合)..."
        />
        <span class="absolute right-2 top-1/2 -translate-y-1/2 text-[10px] text-[var(--text-secondary)] border border-[var(--border-color)] rounded px-1.5 py-px">按 K</span>
      </div>
    </div>

    <!-- Filters -->
    <div class="px-5 py-2 flex items-center gap-1.5 flex-wrap text-[13px] flex-shrink-0">
      <button
        v-for="t in typesList" :key="t.name"
        @click="toggleType(t.name)"
        :class="typeSel.includes(t.name)
          ? 'border-[#1890FF] text-[#1890FF] bg-[var(--bg-page)]'
          : 'border-[var(--border-color)] text-[var(--text-secondary)] bg-[var(--bg-page)] hover:text-[var(--text-primary)]'"
        class="px-2 py-1 rounded border"
      >{{ t.name }}</button>
      <span v-if="typeSel.length" class="text-[#1890FF]">type: {{ typeSel.length }} 项</span>

      <select v-model="scopeSel" @change="onFilterChange" class="px-2 py-1 rounded border border-[var(--border-color)] bg-[var(--bg-page)] text-[var(--text-secondary)]">
        <option value="">scope</option>
        <option value="repo">repo</option>
        <option value="global">global</option>
        <option value="user">user</option>
      </select>
      <select v-model="statusSel" @change="onFilterChange" class="px-2 py-1 rounded border border-[var(--border-color)] bg-[var(--bg-page)] text-[var(--text-secondary)]">
        <option value="">status</option>
        <option value="active">active</option>
        <option value="archived">archived</option>
        <option value="superseded">superseded</option>
      </select>
      <select v-model="sort" @change="onFilterChange" class="px-2 py-1 rounded border border-[var(--border-color)] bg-[var(--bg-page)] text-[var(--text-secondary)]">
        <option value="updated_at">更新时间</option>
        <option value="created_at">创建时间</option>
        <option value="importance">重要性</option>
        <option value="recall_count">召回次数</option>
      </select>
      <button @click="order = order === 'desc' ? 'asc' : 'desc'; onFilterChange()" class="px-1.5 py-1 text-[var(--text-secondary)]">{{ order === 'desc' ? '↓' : '↑' }}</button>
      <button @click="clearFilters" class="ml-auto text-[#1890FF] hover:underline">清空</button>
    </div>

    <!-- Table -->
    <div class="flex-1 overflow-auto px-5 pb-2">
      <div v-if="loading" class="py-10 text-center text-[13px] text-[var(--text-secondary)]">加载中…</div>
      <div v-else-if="error" class="py-10 text-center text-[13px] text-red-500">{{ error }}</div>
      <table v-else class="w-full text-[13px] border-collapse">
        <thead class="sticky top-0 bg-[var(--bg-page)] z-10">
          <tr class="text-[11px] uppercase tracking-wide text-[var(--text-secondary)]">
            <th class="text-left font-normal py-1.5 px-2 border-b border-[var(--border-color)] w-8">
              <input type="checkbox" :checked="displayItems.length && displayItems.every(i => selectedIds.has(i.id))" @change="toggleSelectAll" />
            </th>
            <th class="text-left font-normal py-1.5 px-2 border-b border-[var(--border-color)] w-24">类型</th>
            <th class="text-left font-normal py-1.5 px-2 border-b border-[var(--border-color)]">标题 / 内容</th>
            <th class="text-left font-normal py-1.5 px-2 border-b border-[var(--border-color)] w-24">项目</th>
            <th class="text-left font-normal py-1.5 px-2 border-b border-[var(--border-color)] w-20">作用域</th>
            <th class="text-left font-normal py-1.5 px-2 border-b border-[var(--border-color)] w-20">状态</th>
            <th class="text-left font-normal py-1.5 px-2 border-b border-[var(--border-color)] w-16">更新</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="r in displayItems" :key="r.id"
            @click="openDrawer(r)"
            class="cursor-pointer hover:bg-[var(--bg-card)] border-b border-[var(--border-color)]"
          >
            <td class="py-0.5 px-2 border-b border-[var(--border-color)]" @click.stop>
              <input type="checkbox" :checked="selectedIds.has(r.id)" @change="toggleSelect(r.id)" />
            </td>
            <td class="py-0.5 px-2"><TypeBadge :name="r.type" :color="typeMap[r.type]?.color || 'gray'" /></td>
            <td class="py-0.5 px-2 text-[var(--text-primary)] max-w-0">
              <div class="truncate">{{ r.content }}</div>
            </td>
            <td class="py-0.5 px-2 text-[var(--text-secondary)]">{{ projectName(r.project_id) }}</td>
            <td class="py-0.5 px-2 text-[var(--text-secondary)]">{{ scopeIcon[r.scope] || '' }} {{ r.scope }}</td>
            <td class="py-0.5 px-2">
              <span class="inline-flex items-center gap-1 text-[var(--text-secondary)]">
                <span class="w-1.5 h-1.5 rounded-full" :style="{ background: r.status === 'active' ? '#52C41A' : '#BFBFBF' }"></span>
                {{ r.status }}
              </span>
            </td>
            <td class="py-0.5 px-2 text-[var(--text-secondary)] whitespace-nowrap">{{ relTime(r.updated_at || r.created_at) }}</td>
          </tr>
          <tr v-if="!displayItems.length">
            <td colspan="7" class="py-10 text-center text-[var(--text-secondary)]">无数据</td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- Pagination -->
    <div class="flex items-center gap-3 px-5 h-10 border-t border-[var(--border-color)] text-[13px] text-[var(--text-secondary)] flex-shrink-0">
      <span>共 {{ total }} 条 · 已选 {{ selectedIds.size }}</span>
      <span class="ml-auto">排序：{{ sort === 'updated_at' ? '更新时间' : sort }} {{ order === 'desc' ? '↓' : '↑' }}</span>
      <div class="flex items-center gap-1">
        <button @click="goto(page - 1)" :disabled="page <= 1" class="px-1.5 disabled:opacity-30">‹</button>
        <span>{{ page }} / {{ pages }}</span>
        <button @click="goto(page + 1)" :disabled="page >= pages" class="px-1.5 disabled:opacity-30">›</button>
      </div>
      <span>每页 {{ size }}</span>
    </div>

    <MemoryDrawer
      :open="drawerOpen"
      :item="drawerItem"
      :type-map="typeMap"
      @close="closeDrawer"
      @confirmed="onConfirm"
      @rejected="onReject"
    />
  </div>
</template>
