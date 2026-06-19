import { ref } from 'vue'

const BASE = '/api/memory'

async function parse(res) {
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json()
}

function post(url, body) {
  return parse(fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {}),
  }))
}

export function useMemoryApi() {
  const types = ref([])
  const projects = ref([])

  async function list(params = {}) {
    const qs = new URLSearchParams()
    for (const [k, v] of Object.entries(params)) {
      if (v === null || v === undefined || v === '') continue
      qs.set(k, Array.isArray(v) ? v.join(',') : v)
    }
    return parse(await fetch(`${BASE}/list?${qs.toString()}`))
  }

  async function query(body) {
    return post(`${BASE}/query`, body)
  }

  async function stats() {
    return parse(await fetch(`${BASE}/stats`))
  }

  async function loadTypes() {
    const d = await parse(await fetch(`${BASE}/types`))
    types.value = d.types || []
    return types.value
  }

  async function loadProjects() {
    const d = await parse(await fetch(`${BASE}/projects`))
    projects.value = d.projects || []
    return projects.value
  }

  function confirm(id, body) { return post(`${BASE}/${id}/confirm`, body) }
  function reject(id) { return post(`${BASE}/${id}/reject`, {}) }
  function write(body) { return post(`${BASE}/write`, body) }

  return { list, query, stats, loadTypes, loadProjects, confirm, reject, write, types, projects }
}
