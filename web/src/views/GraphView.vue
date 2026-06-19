<script setup>
import { ref, onMounted, onBeforeUnmount, watch, nextTick } from 'vue'
import * as echarts from 'echarts'

const chartRef = ref(null)
let chartInstance = null

const entities = ref([])
const relations = ref([])
const loading = ref(false)
const error = ref('')

const selectedEntity = ref(null)
const associatedMemories = ref([])
const memoriesLoading = ref(false)

const entityTypeFilter = ref('')
const entityTypeOptions = ['person', 'project', 'tool', 'concept', 'technology']

const typeColorMap = {
  person: '#ef4444',
  project: '#3b82f6',
  tool: '#22c55e',
  concept: '#a855f7',
  technology: '#f59e0b',
}

async function fetchGraph() {
  loading.value = true
  error.value = ''
  try {
    const params = new URLSearchParams()
    if (entityTypeFilter.value) params.set('entity_type', entityTypeFilter.value)
    const res = await fetch(`/api/memory/graph?${params}`)
    if (!res.ok) throw new Error('Failed to fetch graph')
    const data = await res.json()
    entities.value = data.entities || []
    relations.value = data.relations || []
    await nextTick()
    renderChart()
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

function renderChart() {
  if (!chartInstance || !entities.value.length) return

  const nodeMap = new Map(entities.value.map(e => [e.name, e]))

  const nodes = entities.value.map(e => ({
    id: e.name,
    name: e.name,
    symbolSize: 30,
    itemStyle: { color: typeColorMap[e.entity_type] || '#6b7280' },
    label: { show: true, fontSize: 11, color: '#e5e7eb' },
    data: e,
  }))

  const edges = relations.value
    .filter(r => nodeMap.has(r.source_name) && nodeMap.has(r.target_name))
    .map(r => ({
      source: r.source_name,
      target: r.target_name,
      lineStyle: { color: '#4b5563', width: 1.5, curveness: 0.2 },
      label: { show: true, formatter: r.relation, fontSize: 9, color: '#9ca3af' },
    }))

  chartInstance.setOption({
    tooltip: {
      trigger: 'item',
      formatter(params) {
        if (params.dataType === 'node') {
          const d = params.data.data
          return `<b>${d.name}</b><br/>Type: ${d.entity_type}<br/>${d.description || ''}`
        }
        if (params.dataType === 'edge') {
          return `${params.data.source} → ${params.data.target}<br/>${params.data.label?.formatter || ''}`
        }
        return ''
      },
    },
    series: [{
      type: 'graph',
      layout: 'force',
      roam: true,
      draggable: true,
      force: {
        repulsion: 300,
        edgeLength: [80, 200],
        gravity: 0.1,
      },
      data: nodes,
      links: edges,
      emphasis: {
        focus: 'adjacency',
        itemStyle: { borderWidth: 3, borderColor: '#fff' },
      },
    }],
  }, true)

  chartInstance.off('click')
  chartInstance.on('click', async (params) => {
    if (params.dataType === 'node') {
      selectedEntity.value = params.data.data
      await fetchAssociatedMemories(params.data.name)
    }
  })
}

async function fetchAssociatedMemories(entityName) {
  memoriesLoading.value = true
  associatedMemories.value = []
  try {
    const res = await fetch('/api/memory/query', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query: entityName, limit: 10 }),
    })
    const data = await res.json()
    associatedMemories.value = data.results || []
  } catch (e) {
    console.error('Failed to fetch memories:', e)
  } finally {
    memoriesLoading.value = false
  }
}

function resizeChart() {
  chartInstance?.resize()
}

onMounted(async () => {
  if (chartRef.value) {
    chartInstance = echarts.init(chartRef.value)
    window.addEventListener('resize', resizeChart)
  }
  await fetchGraph()
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', resizeChart)
  chartInstance?.dispose()
})

watch(entityTypeFilter, () => {
  fetchGraph()
})
</script>

<template>
  <div class="h-full flex">
    <!-- Left: Graph Canvas -->
    <div class="flex-1 flex flex-col">
      <!-- Top Bar -->
      <div class="flex items-center gap-4 px-4 py-3 border-b border-[var(--border-color)] bg-[var(--bg-card)]">
        <div class="text-xs text-[var(--text-secondary)]">
          Entities: <span class="text-[var(--text-primary)] font-medium">{{ entities.length }}</span>
        </div>
        <div class="text-xs text-[var(--text-secondary)]">
          Relations: <span class="text-[var(--text-primary)] font-medium">{{ relations.length }}</span>
        </div>
        <select
          v-model="entityTypeFilter"
          class="rounded-lg border border-[var(--border-color)] bg-[var(--bg-page)] px-2 py-1 text-xs text-[var(--text-primary)] focus:outline-none"
        >
          <option value="">All Types</option>
          <option v-for="t in entityTypeOptions" :key="t" :value="t">{{ t }}</option>
        </select>
        <button @click="fetchGraph" class="px-3 py-1 rounded-lg text-xs bg-blue-500 text-white hover:bg-blue-600 transition-colors">Refresh</button>

        <!-- Legend -->
        <div class="ml-auto flex gap-3">
          <span v-for="(color, type) in typeColorMap" :key="type" class="flex items-center gap-1 text-xs text-[var(--text-secondary)]">
            <span class="w-2.5 h-2.5 rounded-full" :style="{ backgroundColor: color }"></span>
            {{ type }}
          </span>
        </div>
      </div>

      <!-- Chart -->
      <div class="flex-1 relative">
        <div v-if="loading" class="absolute inset-0 flex items-center justify-center bg-[var(--bg-page)]/80 z-10">
          <span class="text-[var(--text-secondary)]">加载中...</span>
        </div>
        <div v-if="error" class="absolute inset-0 flex items-center justify-center">
          <span class="text-red-500">{{ error }}</span>
        </div>
        <div ref="chartRef" class="w-full h-full"></div>
      </div>
    </div>

    <!-- Right: Sidebar -->
    <div class="w-80 border-l border-[var(--border-color)] bg-[var(--bg-card)] overflow-auto flex-shrink-0">
      <div v-if="!selectedEntity" class="p-4 text-sm text-[var(--text-secondary)] text-center mt-8">
        点击节点查看实体详情
      </div>
      <template v-else>
        <div class="p-4 border-b border-[var(--border-color)]">
          <h3 class="text-base font-semibold text-[var(--text-primary)] mb-2">{{ selectedEntity.name }}</h3>
          <div class="flex items-center gap-2 mb-2">
            <span class="px-2 py-0.5 rounded text-xs font-medium text-white" :style="{ backgroundColor: typeColorMap[selectedEntity.entity_type] || '#6b7280' }">
              {{ selectedEntity.entity_type }}
            </span>
          </div>
          <p v-if="selectedEntity.description" class="text-sm text-[var(--text-secondary)]">{{ selectedEntity.description }}</p>
        </div>
        <div class="p-4">
          <h4 class="text-xs font-semibold text-[var(--text-secondary)] uppercase tracking-wider mb-3">关联记忆</h4>
          <div v-if="memoriesLoading" class="text-sm text-[var(--text-secondary)]">加载中...</div>
          <div v-else-if="!associatedMemories.length" class="text-sm text-[var(--text-secondary)]">无关联记忆</div>
          <div v-else class="space-y-2">
            <div
              v-for="m in associatedMemories"
              :key="m.id"
              class="p-2 rounded-lg bg-[var(--bg-page)] border border-[var(--border-color)] text-sm"
            >
              <p class="text-[var(--text-primary)] text-xs line-clamp-3">{{ m.content }}</p>
              <div class="flex items-center gap-2 mt-1 text-xs text-[var(--text-secondary)]">
                <span class="px-1.5 py-0.5 rounded text-[10px] bg-[var(--bg-card)]">{{ m.layer }}</span>
                <span v-if="m.importance" class="text-amber-400">{{ '★'.repeat(Math.round(m.importance)) }}</span>
              </div>
            </div>
          </div>
        </div>
      </template>
    </div>
  </div>
</template>
