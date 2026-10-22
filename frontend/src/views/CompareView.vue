<template>
  <div>
    <div class="card">
      <h2>公司对比</h2>
      <div class="field">
        <label>公司（至少选 2 家）</label>
        <div class="chips">
          <button
            v-for="c in store.companies"
            :key="c"
            class="chip"
            :class="{ on: selected.includes(c) }"
            @click="toggle(c)"
          >
            {{ c }}
          </button>
        </div>
        <div v-if="!store.companies.length" class="hint">暂无公司，请先上传财报</div>
      </div>
      <div class="row2" style="margin-top: 12px">
        <div class="field">
          <label>年度</label>
          <input type="number" v-model.number="year" />
        </div>
      </div>
      <button style="margin-top: 14px" @click="run" :disabled="running || selected.length < 2">对比</button>
    </div>

    <div v-if="error" class="empty">{{ error }}</div>
    <div v-else-if="result" class="card">
      <h2>指标对比（{{ result.year }}）</h2>
      <div ref="chartEl" class="compare-chart"></div>
      <div class="mgroup">全部指标明细</div>
      <CompareTable :comparison="result.comparison" />
    </div>
    <div v-else class="empty">选择至少 2 家公司后点击「对比」</div>
  </div>
</template>

<script setup>
import { ref, onMounted, onBeforeUnmount, nextTick } from 'vue'
import * as echarts from 'echarts'
import { store, bootstrap } from '../store'
import { api } from '../api'
import CompareTable from '../components/CompareTable.vue'

const selected = ref([])
const year = ref(2024)
const running = ref(false)
const result = ref(null)
const error = ref('')
const chartEl = ref(null)
let chart = null

// 图表用：百分比口径、跨公司可比的指标
const CHART_CODES = ['gross_margin', 'net_margin', 'roe', 'revenue_growth']
const CHART_NAMES = { gross_margin: '毛利率', net_margin: '净利率', roe: 'ROE', revenue_growth: '营收增长率' }

function toggle(c) {
  const i = selected.value.indexOf(c)
  if (i >= 0) selected.value.splice(i, 1)
  else selected.value.push(c)
}

async function run() {
  running.value = true
  error.value = ''
  result.value = null
  try {
    result.value = await api.compare(selected.value, year.value, null, 'consolidated')
    await nextTick()
    drawChart()
  } catch (e) {
    error.value = e.message
  } finally {
    running.value = false
  }
}

function drawChart() {
  if (!chartEl.value) return
  if (!chart) chart = echarts.init(chartEl.value)
  const comparison = result.value?.comparison || {}
  const companies = Object.keys(comparison)
  const codes = CHART_CODES.filter((c) => companies.some((co) => comparison[co]?.[c]?.value != null))
  const series = companies.map((co) => ({
    name: co,
    type: 'bar',
    data: codes.map((c) => {
      const m = comparison[co]?.[c]
      return m?.value != null ? +(m.value * 100).toFixed(2) : null
    }),
  }))
  chart.setOption(
    {
      grid: { left: 12, right: 20, top: 40, bottom: 28, containLabel: true },
      legend: { textStyle: { color: '#8b949e' }, top: 0 },
      tooltip: {
        trigger: 'axis',
        backgroundColor: '#1a2230',
        borderColor: '#263041',
        textStyle: { color: '#e6edf3' },
        valueFormatter: (v) => v + '%',
      },
      xAxis: {
        type: 'category',
        data: codes.map((c) => CHART_NAMES[c] || c),
        axisLine: { lineStyle: { color: '#263041' } },
        axisLabel: { color: '#8b949e' },
      },
      yAxis: {
        type: 'value',
        axisLabel: { color: '#8b949e', formatter: '{value}%' },
        splitLine: { lineStyle: { color: '#1f2836' } },
      },
      series,
    },
    true
  )
}

onMounted(async () => {
  if (!store.companies.length) await bootstrap()
  if (!selected.value.length && store.companies.length >= 2) {
    selected.value = store.companies.slice(0, 2)
  }
})
onBeforeUnmount(() => {
  chart && chart.dispose()
})
</script>
