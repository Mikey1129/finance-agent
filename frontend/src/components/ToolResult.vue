<template>
  <div class="tool-result">
    <div class="tr-head">
      <span class="badge" :class="result.status === 'success' ? 'ok' : 'warn'">
        {{ result.status === 'success' ? '✓' : result.status }}
      </span>
      <span class="tr-name">{{ result.tool }}</span>
      <span v-if="pages.length" class="tr-src">来源: {{ pages.join(', ') }}</span>
    </div>

    <div class="tr-body">
      <template v-if="result.tool === 'query_financial_fact'">
        <span v-for="(f, i) in facts" :key="i" class="fact">
          {{ itemName(f) }} = {{ fmtMoney(f.value) }}
        </span>
        <span v-if="!facts.length" class="hint">无匹配事实</span>
      </template>

      <template v-else-if="result.tool === 'compute_metric'">
        <span v-for="(m, i) in metrics" :key="i" class="metric">{{ m.name }} = {{ fmtMetric(m) }}</span>
        <span v-if="!metrics.length" class="hint">数据不足</span>
      </template>

      <template v-else-if="result.tool === 'retrieve_evidence'">
        <div v-for="(c, i) in chunks" :key="i" class="evidence">
          <b>{{ truncate(c.text) }}</b> (p{{ c.page_start ?? '?' }})
        </div>
        <span v-if="!chunks.length" class="hint">无匹配片段</span>
      </template>

      <template v-else-if="result.tool === 'validate_result'">
        <span class="hint">校验 errors={{ result.data?.errors ?? 0 }} warnings={{ result.data?.warnings ?? 0 }}</span>
      </template>

      <template v-else>
        <span class="hint">{{ summarize(result.data) }}</span>
      </template>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { fmtMetric, fmtMoney } from '../utils/format'

const props = defineProps({ result: { type: Object, required: true } })

const pages = computed(() => {
  const srcs = props.result.sources || []
  return [...new Set(srcs.filter((s) => s.page != null).map((s) => `p${s.page}`))]
})

const facts = computed(() =>
  Array.isArray(props.result.data) ? props.result.data.filter((f) => f.value != null) : []
)

const metrics = computed(() => {
  const d = props.result.data || {}
  const vals = Array.isArray(d) ? d : Object.values(d)
  return vals.filter((m) => m && m.value != null)
})

const chunks = computed(() => (Array.isArray(props.result.data) ? props.result.data : []))

function itemName(f) {
  return f.raw_item || f.standard_item || '?'
}
function truncate(s, n = 120) {
  const t = String(s ?? '')
  return t.slice(0, n) + (t.length > n ? '…' : '')
}
function summarize(data) {
  if (data == null) return ''
  if (typeof data === 'string') return data
  try {
    const s = JSON.stringify(data)
    return s.length > 200 ? s.slice(0, 200) + '…' : s
  } catch {
    return ''
  }
}
</script>
