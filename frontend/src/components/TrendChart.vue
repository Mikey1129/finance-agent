<template>
  <div ref="el" class="trend-chart"></div>
</template>

<script setup>
import { ref, onMounted, onBeforeUnmount, watch } from 'vue'
import * as echarts from 'echarts'

const props = defineProps({
  name: { type: String, default: '' },
  data: { type: Array, default: () => [] }, // [{ period, value, display }]
  color: { type: String, default: '#2ea8ff' },
})

const el = ref(null)
let chart = null

function render() {
  if (!el.value) return
  if (!chart) chart = echarts.init(el.value)
  const periods = props.data.map((d) => d.period)
  const values = props.data.map((d) => d.value)
  chart.setOption(
    {
      grid: { left: 12, right: 20, top: 30, bottom: 28, containLabel: true },
      tooltip: {
        trigger: 'axis',
        backgroundColor: '#1a2230',
        borderColor: '#263041',
        textStyle: { color: '#e6edf3' },
        formatter: (params) => {
          const i = params[0].dataIndex
          const display = props.data[i]?.display
          return `${props.name}<br><b>${params[0].name}</b>：${display ?? params[0].value}`
        },
      },
      xAxis: {
        type: 'category', data: periods,
        axisLine: { lineStyle: { color: '#263041' } }, axisLabel: { color: '#8b949e' },
      },
      yAxis: {
        type: 'value',
        splitLine: { lineStyle: { color: '#1f2836' } }, axisLabel: { color: '#8b949e' },
      },
      series: [
        {
          name: props.name, type: 'line', smooth: true, data: values,
          lineStyle: { color: props.color, width: 2 },
          itemStyle: { color: props.color },
          areaStyle: { color: props.color + '22' },
        },
      ],
    },
    true
  )
}

const resize = () => chart && chart.resize()

onMounted(() => {
  render()
  window.addEventListener('resize', resize)
})
onBeforeUnmount(() => {
  window.removeEventListener('resize', resize)
  chart && chart.dispose()
})
watch(() => props.data, render, { deep: true })
</script>
