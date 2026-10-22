import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  { path: '/', name: 'dashboard', component: () => import('../views/DashboardView.vue'), meta: { title: '指标概览' } },
  { path: '/chat', name: 'chat', component: () => import('../views/ChatView.vue'), meta: { title: '对话分析' } },
  { path: '/upload', name: 'upload', component: () => import('../views/UploadView.vue'), meta: { title: '上传财报' } },
  { path: '/skills', name: 'skills', component: () => import('../views/SkillsView.vue'), meta: { title: '技能分析' } },
  { path: '/compare', name: 'compare', component: () => import('../views/CompareView.vue'), meta: { title: '公司对比' } },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

router.afterEach((to) => {
  document.title = to.meta.title ? `${to.meta.title} · 财报分析 Agent` : '企业财报智能分析 Agent'
})

export default router
