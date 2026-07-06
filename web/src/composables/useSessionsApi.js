import { ref } from 'vue'

const BASE = '/api/sessions'

async function parse(res) {
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json()
}

async function post(url, body) {
  return parse(await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {}),
  }))
}

export function useSessionsApi() {
  const results = ref([])

  // Message-level hybrid search (RRF). Each hit carries the matched message,
  // its session metadata, and prev[]/next[] context-window turns.
  async function searchMessages({ query, namespace = 'claude', limit = 20, offset = 0, context_window = 3 } = {}) {
    const d = await post(`${BASE}/search`, { query, namespace, limit, offset, context_window })
    results.value = d.results || []
    return results.value
  }

  return { results, searchMessages }
}
