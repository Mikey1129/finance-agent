import { reactive } from 'vue'

// 轻量全局状态：公司列表 + 健康状态 + 最近分析公司（跨视图共享，避免重复请求）
export const store = reactive({
  companies: [],
  healthy: false,
  lastCompany: localStorage.getItem('fa_last_company') || null,
})

export async function bootstrap() {
  try {
    const h = await fetch('/api/health').then((r) => r.json())
    store.healthy = h.status === 'ok'
    store.companies = h.companies || []
  } catch {
    store.healthy = false
  }
}

export function setLastCompany(name) {
  store.lastCompany = name || null
  localStorage.setItem('fa_last_company', name || '')
}
