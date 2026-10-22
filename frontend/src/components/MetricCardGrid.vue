<template>
  <div>
    <div v-for="(cards, group) in grouped" :key="group">
      <div class="mgroup">{{ group }}</div>
      <div class="mgrid">
        <StatCard v-for="c in cards" :key="c.code" :metric="c" />
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import StatCard from './StatCard.vue'

const props = defineProps({ metrics: { type: Array, default: () => [] } })

const grouped = computed(() => {
  const g = {}
  for (const m of props.metrics) (g[m.group] ??= []).push(m)
  return g
})
</script>
