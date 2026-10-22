<template>
  <div class="stat-card" :class="{ signal: metric.is_signal, na: metric.value == null }">
    <div class="sc-name">{{ metric.name }}</div>
    <div class="sc-value">{{ metric.display }}</div>
  </div>
</template>

<script setup>
defineProps({ metric: { type: Object, required: true } })
</script>
