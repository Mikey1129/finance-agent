const BASE = '/api'

async function request(path, options = {}) {
  const res = await fetch(BASE + path, options)
  if (!res.ok) {
    let detail = res.statusText
    try {
      const j = await res.json()
      detail = j.detail || detail
    } catch {
      /* 非 JSON 响应，用 statusText */
    }
    throw new Error(detail)
  }
  return res.json()
}

export const api = {
  health: () => request('/health'),
  companies: () => request('/companies'),
  upload: (formData) => request('/upload', { method: 'POST', body: formData }),
  ask: (query, threadId) =>
    request('/ask', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query, thread_id: threadId }),
    }),
  overview: (company, year) =>
    request(`/company/${encodeURIComponent(company)}/overview?year=${year}`),
  skills: () => request('/skills'),
  runSkill: (code, companyName, fiscalYear) =>
    request(`/skills/${code}/run`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ company_name: companyName, fiscal_year: fiscalYear }),
    }),
  compare: (companyNames, fiscalYear, metricCodes = null, scope = 'consolidated') =>
    request('/compare', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        company_names: companyNames,
        fiscal_year: fiscalYear,
        metric_codes: metricCodes,
        scope,
      }),
    }),
  runs: (limit = 50) => request(`/runs?limit=${limit}`),
  exportRunUrl: (runId) => `${BASE}/runs/${runId}/export`,
}
