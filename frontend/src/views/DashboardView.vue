<template>
  <div>
    <div class="card">
      <div class="dash-controls">
        <div class="field">
          <label>公司</label>
          <select v-model="company">
            <option v-for="c in store.companies" :key="c" :value="c">{{ c }}</option>
          </select>
        </div>
        <div class="field">
          <label>年度</label>
          <input type="number" v-model.number="year" />
        </div>
        <button class="btn-go" @click="load" :disabled="loading || !company">查看</button>
      </div>
    </div>

    <div v-if="loading" class="empty">加载中…</div>
    <div v-else-if="error" class="empty">{{ error }}</div>
    <div v-else-if="!overview" class="empty">选择公司并点击「查看」</div>
    <div v-else-if="!overview.metrics.length" class="empty">暂无数据，请先在「上传财报」页上传</div>
    <template v-else>
      <div class="card">
        <h2>指标概览（{{ overview.year }}）</h2>
        <MetricCardGrid :metrics="overview.metrics" />
      </div>

      <div class="card" v-if="trendCharts.length">
        <h2>关键指标趋势</h2>
        <div class="trend-grid">
          <div v-for="t in trendCharts" :key="t.code">
            <div class="trend-title">{{ t.name }}</div>
            <TrendChart :name="t.name" :data="t.data" :color="t.color" />
          </div>
        </div>
      </div>

      <div class="card" v-if="overview.facts.length">
        <h2>关键科目</h2>
        <div class="fact-row">
          <div v-for="(f, i) in overview.facts" :key="i" class="fact-lg">
            <span class="fact-label">{{ itemLabel(f.item) }}（{{ f.period }}）</span>
            <span class="fact-val">{{ fmtMoney(f.value) }}</span>
          </div>
        </div>
      </div>
    </template>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue'
import { store, bootstrap } from '../store'
import { api } from '../api'
import { fmtMoney } from '../utils/format'
import MetricCardGrid from '../components/MetricCardGrid.vue'
import TrendChart from '../components/TrendChart.vue'

const company = ref(store.lastCompany || '')
const year = ref(2024)
const overview = ref(null)
const loading = ref(false)
const error = ref('')

const COLORS = ['#2ea8ff', '#00d4aa', '#d29922', '#f85149', '#a371f7']
const TREND_META = {
  gross_margin: '毛利率',
  net_margin: '净利率',
  roe: 'ROE',
  revenue_growth: '营收增长率',
  net_profit_growth: '净利润增长率',
}
const ITEM_LABELS = {
  revenue: '营业收入',
  net_profit: '净利润',
  total_assets: '资产总计',
  total_liabilities: '负债合计',
  total_equity: '所有者权益',
  operating_cash_flow: '经营现金流净额',
}

const trendCharts = computed(() => {
  const trends = overview.value?.trends || {}
  return Object.entries(trends)
    .map(([code, series], i) => ({
      code,
      name: TREND_META[code] || code,
      data: series.filter((s) => s.value != null),
      color: COLORS[i % COLORS.length],
    }))
    .filter((t) => t.data.length >= 2)
})

async function load() {
  if (!company.value) return
  loading.value = true
  error.value = ''
  try {
    overview.value = await api.overview(company.value, year.value)
  } catch (e) {
    overview.value = null
    error.value = e.message
  } finally {
    loading.value = false
  }
}

function itemLabel(item) {
  return ITEM_LABELS[item] || item
}

onMounted(async () => {
  if (!store.companies.length) await bootstrap()
  if (!company.value && store.companies.length) company.value = store.companies[0]
  if (company.value) load()
})
</script>
