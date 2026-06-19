<script setup>
import { ref, onMounted } from 'vue'
import RecapCard from '../components/recap/RecapCard.vue'
import RecapCalendar from '../components/recap/RecapCalendar.vue'

const activeTab = ref('today')
const today = new Date().toISOString().split('T')[0]
const selectedDate = ref(today)
const recap = ref(null)
const calendarDates = ref([])
const recapList = ref([])
const loading = ref(false)
const generating = ref(false)
const error = ref('')

async function fetchRecap(date) {
  loading.value = true
  error.value = ''
  try {
    const res = await fetch(`/api/recap/daily?date=${date}`)
    const data = await res.json()
    recap.value = data.generated !== false ? data : null
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

async function generateRecapAction() {
  generating.value = true
  error.value = ''
  try {
    const res = await fetch(`/api/recap/generate?date=${selectedDate.value}`, { method: 'POST' })
    const data = await res.json()
    if (res.ok) {
      recap.value = data
    } else {
      error.value = data.detail || '生成失败'
    }
  } catch (e) {
    error.value = e.message
  } finally {
    generating.value = false
  }
}

async function fetchCalendar() {
  try {
    const res = await fetch('/api/recap/calendar?months=3')
    calendarDates.value = await res.json()
  } catch (e) {
    console.error('Failed to load calendar:', e)
  }
}

async function fetchRecapList() {
  try {
    const res = await fetch('/api/recap/list')
    recapList.value = await res.json()
  } catch (e) {
    console.error('Failed to load recap list:', e)
  }
}

async function handleWriteToMemory(knowledge) {
  try {
    await fetch('/api/memory/write', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ target: 'memory', action: 'add', content: knowledge.content || knowledge }),
    })
    alert('已写入 Hermes 记忆')
  } catch (e) {
    alert('写入失败: ' + e.message)
  }
}

async function ingestRecap() {
  if (!recap.value) return
  try {
    const res = await fetch(`/api/recap/ingest?date=${recap.value.date}`, { method: 'POST' })
    const data = await res.json()
    if (res.ok) {
      alert(`已写入知识库: ${data.ingested} 条`)
      recap.value = { ...recap.value, ingested: true }
    } else {
      alert('写入失败: ' + (data.detail || '未知错误'))
    }
  } catch (e) {
    alert('写入失败: ' + e.message)
  }
}

function onDateSelect(date) {
  selectedDate.value = date
  activeTab.value = 'today'
  fetchRecap(date)
}

onMounted(() => {
  fetchRecap(selectedDate.value)
  fetchCalendar()
  fetchRecapList()
})
</script>

<template>
  <div class="h-full overflow-auto p-6 max-w-4xl mx-auto">
    <h1 class="text-2xl font-bold text-[var(--text-primary)] mb-6">Daily Recap</h1>

    <div class="flex gap-1 mb-6 bg-[var(--bg-card)] rounded-lg p-1 border border-[var(--border-color)] w-fit">
      <button v-for="tab in [{ id: 'today', label: '今日复盘' }, { id: 'calendar', label: '日历' }, { id: 'stats', label: '统计概览' }]"
        :key="tab.id"
        @click="activeTab = tab.id"
        :class="activeTab === tab.id ? 'bg-blue-500 text-white' : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]'"
        class="px-4 py-1.5 rounded-md text-sm transition-colors"
      >{{ tab.label }}</button>
    </div>

    <div v-if="activeTab === 'today'">
      <div v-if="loading" class="text-center py-8 text-[var(--text-secondary)]">加载中...</div>
      <div v-else-if="error" class="text-center py-8">
        <p class="text-red-500 mb-3">{{ error }}</p>
        <button @click="generateRecapAction" class="px-4 py-2 rounded-lg text-sm bg-blue-500 text-white hover:bg-blue-600">重试</button>
      </div>
      <div v-else-if="!recap" class="text-center py-8">
        <p class="text-[var(--text-secondary)] mb-3">{{ selectedDate }} 尚未生成复盘</p>
        <button @click="generateRecapAction" :disabled="generating" class="px-4 py-2 rounded-lg text-sm bg-blue-500 text-white hover:bg-blue-600 disabled:opacity-50">
          {{ generating ? '生成中...' : '生成复盘' }}
        </button>
      </div>
      <RecapCard v-else :recap="recap" @regenerate="generateRecapAction" @write-to-memory="handleWriteToMemory" />
      <div v-if="recap && !recap.ingested" class="mt-4 text-center">
        <button @click="ingestRecap" class="px-4 py-2 rounded-lg text-sm bg-green-600 text-white hover:bg-green-700 transition-colors">写入知识库 (Episodic)</button>
      </div>
      <div v-if="recap?.ingested" class="mt-4 text-center">
        <span class="text-sm text-green-500">已写入知识库</span>
      </div>
    </div>

    <div v-if="activeTab === 'calendar'" class="grid grid-cols-1 lg:grid-cols-3 gap-6">
      <div class="lg:col-span-2 bg-[var(--bg-card)] rounded-xl border border-[var(--border-color)] p-5">
        <RecapCalendar :dates="calendarDates" @select-date="onDateSelect" />
      </div>
      <div class="bg-[var(--bg-card)] rounded-xl border border-[var(--border-color)] p-5">
        <h3 class="text-sm font-semibold text-[var(--text-secondary)] uppercase tracking-wider mb-3">历史复盘</h3>
        <div v-if="recapList.length === 0" class="text-sm text-[var(--text-secondary)]">暂无</div>
        <div v-for="r in recapList" :key="r.date" @click="onDateSelect(r.date)"
          class="py-2 border-b border-[var(--border-color)] cursor-pointer hover:text-blue-500 transition-colors last:border-0">
          <div class="text-sm font-medium text-[var(--text-primary)]">{{ r.date }}</div>
          <div class="text-xs text-[var(--text-secondary)] truncate">{{ r.summary }}</div>
        </div>
      </div>
    </div>

    <div v-if="activeTab === 'stats'" class="bg-[var(--bg-card)] rounded-xl border border-[var(--border-color)] p-5">
      <p class="text-[var(--text-secondary)] text-sm">统计概览功能 — 可通过 Dashboard 页面查看会话、Token、工具使用等统计图表。</p>
      <p class="text-[var(--text-secondary)] text-sm mt-2">选择日历中的日期可查看对应日期的复盘详情。</p>
    </div>
  </div>
</template>
