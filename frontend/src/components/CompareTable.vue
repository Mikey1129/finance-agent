<template>
  <table>
    <thead>
      <tr><th>指标</th><th v-for="c in companies" :key="c">{{ c }}</th></tr>
    </thead>
    <tbody>
      <tr v-for="m in metricRows" :key="m.code">
        <td>{{ m.name }}</td>
        <td v-for="c in companies" :key="c">{{ fmtMetric(comparison[c]?.[m.code]) }}</td>
      </tr>
    </tbody>
  </table>
</template>

<script setup>
import { computed } from 'vue'
import { fmtMetric } from '../utils/format'

const props = defineProps({ comparison: { type: Object, default: () => ({}) } })

const companies = computed(() => Object.keys(props.comparison || {}))

const metricRows = computed(() => {
  const codes = []
  for (const c of companies.value) {
    for (const code of Object.keys(props.comparison[c] || {})) {
      if (!codes.includes(code)) codes.push(code)
    }
  }
  return codes.map((code) => {
    const first = companies.value.map((c) => props.comparison[c]?.[code]).find((m) => m)
    return { code, name: first?.name || code }
  })
})
</script>
