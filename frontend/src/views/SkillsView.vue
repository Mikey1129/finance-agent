<template>
  <div>
    <div class="card">
      <h2>技能分析</h2>
      <div class="row2">
        <div class="field">
          <label>技能</label>
          <select v-model="skill">
            <option v-for="s in skills" :key="s.code" :value="s.code">{{ s.name }}</option>
          </select>
        </div>
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
      </div>
      <button style="margin-top: 14px" @click="run" :disabled="running || !company">运行</button>
    </div>

    <div class="card" v-if="result">
      <div class="skill-summary">{{ result.summary }}</div>
      <div v-if="result.warnings?.length" class="warnline">⚠ {{ result.warnings.join('；') }}</div>
      <div v-for="(s, i) in result.sections || []" :key="i">
        <div class="mgroup">{{ s.heading }}</div>
        <DataTable :rows="s.rows" />
      </div>
    </div>
    <div v-else class="empty">选择技能和公司后点击「运行」</div>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { store, bootstrap } from '../store'
import { api } from '../api'
import DataTable from '../components/DataTable.vue'

const skills = ref([])
const skill = ref('')
const company = ref(store.lastCompany || '')
const year = ref(2024)
const result = ref(null)
const running = ref(false)

async function run() {
  if (!skill.value || !company.value) return
  running.value = true
  try {
    result.value = await api.runSkill(skill.value, company.value, year.value || null)
  } catch (e) {
    result.value = { summary: '✗ ' + e.message, sections: [] }
  } finally {
    running.value = false
  }
}

onMounted(async () => {
  if (!store.companies.length) await bootstrap()
  try {
    const j = await api.skills()
    skills.value = j.skills || []
    if (skills.value.length && !skill.value) skill.value = skills.value[0].code
  } catch {
    /* 技能接口失败时留空 */
  }
  if (!company.value && store.companies.length) company.value = store.companies[0]
})
</script>
