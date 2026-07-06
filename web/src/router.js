import { createRouter, createWebHistory } from 'vue-router'

import Dashboard from './views/Dashboard.vue'
import HistoryView from './views/HistoryView.vue'
import PlansView from './views/PlansView.vue'
import ProjectsView from './views/ProjectsView.vue'
import ProjectDetailView from './views/ProjectDetailView.vue'
import ConversationView from './views/ConversationView.vue'
import RecapView from './views/RecapView.vue'
import MemoryView from './views/MemoryView.vue'
import MemoriesView from './views/MemoriesView.vue'
import GraphView from './views/GraphView.vue'
import SessionSearchView from './views/SessionSearchView.vue'

const routes = [
  { path: '/', component: Dashboard },
  { path: '/history', component: HistoryView },
  { path: '/plans', component: PlansView },
  { path: '/projects', component: ProjectsView },
  { path: '/projects/:projectId', component: ProjectDetailView, props: true },
  { path: '/projects/:projectId/sessions/:sessionId', component: ConversationView, props: true },
  { path: '/recap', component: RecapView },
  { path: '/memory', component: MemoriesView, props: { stage: 'memory' } },
  { path: '/candidates', component: MemoriesView, props: { stage: 'candidate' } },
  { path: '/observations', component: MemoriesView, props: { stage: 'observation' } },
  { path: '/graph', component: GraphView },
  { path: '/sessions/search', component: SessionSearchView },
  { path: '/sources/:source', component: Dashboard, props: true },
  { path: '/sources/:source/history', component: HistoryView, props: true },
  { path: '/sources/:source/projects', component: ProjectsView, props: true },
  { path: '/sources/:source/projects/:projectId', component: ProjectDetailView, props: true },
  {
    path: '/sources/:source/projects/:projectId/sessions/:sessionId',
    component: ConversationView,
    props: true,
  },
]

export default createRouter({
  history: createWebHistory(),
  routes,
})
