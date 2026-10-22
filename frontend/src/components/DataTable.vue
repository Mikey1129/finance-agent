<template>
  <div v-if="!rows.length" class="hint">无</div>
  <table v-else>
    <thead>
      <tr><th v-for="k in keys" :key="k">{{ k }}</th></tr>
    </thead>
    <tbody>
      <tr v-for="(r, i) in rows.slice(0, 20)" :key="i">
        <td v-for="k in keys" :key="k">{{ cell(r[k]) }}</td>
      </tr>
    </tbody>
  </table>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({ rows: { type: Array, default: () => [] } })

const keys = computed(() => {
  const seen = []
  for (const r of props.rows) {
    for (const k of Object.keys(r)) if (!seen.includes(k)) seen.push(k)
    if (seen.length >= 5) break
  }
  return seen
})

function cell(v) {
  if (v == null) return ''
  if (typeof v === 'object') return JSON.stringify(v)
  return String(v)
}
</script>
