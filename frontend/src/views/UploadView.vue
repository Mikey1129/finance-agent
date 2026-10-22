<template>
  <div class="card">
    <h2>上传财报</h2>
    <div class="row2">
      <div class="field">
        <label>公司名称</label>
        <input type="text" v-model="company" placeholder="如：公牛集团" />
      </div>
      <div class="field">
        <label>财务年度</label>
        <input type="number" v-model.number="year" />
      </div>
    </div>
    <div class="field">
      <label>报表类型（PDF 自动识别，CSV/Excel 需选择）</label>
      <select v-model="stype">
        <option value="">PDF 自动识别</option>
        <option value="balance_sheet">资产负债表</option>
        <option value="income_statement">利润表</option>
        <option value="cash_flow">现金流量表</option>
      </select>
    </div>
    <div class="field">
      <label>文件（PDF / CSV / Excel）</label>
      <input type="file" ref="fileEl" accept=".pdf,.csv,.xlsx,.xls" />
    </div>
    <div class="hint" :class="{ warnline: hintError }">{{ hint }}</div>
    <button style="margin-top: 14px" @click="upload" :disabled="uploading">上传并解析</button>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { store, bootstrap } from '../store'
import { api } from '../api'

const company = ref('')
const year = ref(2024)
const stype = ref('')
const fileEl = ref(null)
const uploading = ref(false)
const hint = ref('')
const hintError = ref(false)

async function upload() {
  const file = fileEl.value?.files?.[0]
  const name = company.value.trim()
  if (!file || !name) {
    hint.value = '请填写公司名称并选择文件'
    hintError.value = true
    return
  }
  const fd = new FormData()
  fd.append('file', file)
  fd.append('company_name', name)
  fd.append('fiscal_year', year.value)
  if (stype.value) fd.append('statement_type', stype.value)

  uploading.value = true
  hintError.value = false
  hint.value = '解析中（PDF 需较长时间）…'
  try {
    const j = await api.upload(fd)
    hint.value = `✓ 已入库 ${j.facts} 条事实` + (j.chunks ? `、${j.chunks} 个文本片段` : '')
    await bootstrap()
    company.value = ''
    if (fileEl.value) fileEl.value.value = ''
  } catch (e) {
    hint.value = '✗ ' + e.message
    hintError.value = true
  } finally {
    uploading.value = false
  }
}
</script>
