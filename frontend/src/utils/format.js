// 16 个指标中，结果为比率/增长率（按百分比显示）的 code
const PCT_CODES = new Set([
  'gross_margin',
  'net_margin',
  'roa',
  'roe',
  'debt_to_asset_ratio',
  'revenue_growth',
  'net_profit_growth',
])

// 把 MetricResult（序列化后含 code/value/status）格式化为可读字符串
export function fmtMetric(m) {
  const v = m?.value
  if (v == null || v === '') return m?.status || '—'
  if (PCT_CODES.has(m.code)) return (v * 100).toFixed(2) + '%'
  return Number(v).toLocaleString('zh-CN', { maximumFractionDigits: 2 })
}

// 金额（内部统一为「元」）
export function fmtMoney(v) {
  if (v == null) return '—'
  return Number(v).toLocaleString('zh-CN', { maximumFractionDigits: 2 }) + ' 元'
}
