<template>
  <div class="card chat-box">
    <div class="chat-scroll" ref="scrollEl">
      <div v-if="!messages.length" class="empty">
        输入问题开始分析。示例：「{{ exampleCompany }}2024年营业收入是多少？」<br />
        支持精确数值查询 / 指标计算 / 深度归因（附证据引用）。
      </div>
      <div v-for="(m, i) in messages" :key="i" class="msg">
        <div class="msg-q"><div class="bubble">{{ m.q }}</div></div>
        <div class="msg-a">
          <div class="bubble">
            <span v-if="m.loading" class="hint">思考中…</span>
            <div v-else-if="m.error" class="warnline">✗ {{ m.error }}</div>
            <template v-else>
              <div class="md" v-html="m.html"></div>
              <div v-if="m.missing?.length" class="warnline">⚠ 缺失证据: {{ m.missing.join('；') }}</div>
              <div v-if="m.contradictions?.length" class="warnline">⚠ 冲突: {{ m.contradictions.join('；') }}</div>
              <a v-if="m.runId" class="dl" :href="api.exportRunUrl(m.runId)" download>导出报告</a>
              <ToolResult v-for="(t, j) in m.tools || []" :key="j" :result="t" />
            </template>
          </div>
        </div>
      </div>
    </div>
    <div class="chat-input">
      <input
        type="text"
        v-model="query"
        placeholder="如：公牛集团2024年营业收入是多少？"
        @keyup.enter="ask"
      />
      <button @click="ask" :disabled="asking || !query.trim()">提问</button>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, nextTick, onMounted } from 'vue'
import { store, bootstrap, setLastCompany } from '../store'
import { api } from '../api'
import { renderMarkdown } from '../utils/markdown'
import ToolResult from '../components/ToolResult.vue'

const query = ref('')
const asking = ref(false)
const messages = ref([])
const threadId = ref(localStorage.getItem('fa_thread') || null)
const scrollEl = ref(null)

const exampleCompany = computed(() => store.companies[0] || '公牛集团')

async function ask() {
  const q = query.value.trim()
  if (!q || asking.value) return
  query.value = ''
  messages.value.push({ q, loading: true })
  asking.value = true
  scrollToBottom()
  try {
    const j = await api.ask(q, threadId.value)
    if (j.thread_id) {
      threadId.value = j.thread_id
      localStorage.setItem('fa_thread', j.thread_id)
    }
    if (j.company_name) setLastCompany(j.company_name)
    const last = messages.value[messages.value.length - 1]
    last.loading = false
    last.html = renderMarkdown(j.answer || '(无回答)')
    last.missing = j.missing_evidence
    last.contradictions = j.contradictions
    last.runId = j.run_id
    last.tools = j.tool_results
  } catch (e) {
    const last = messages.value[messages.value.length - 1]
    last.loading = false
    last.error = e.message
  } finally {
    asking.value = false
    scrollToBottom()
  }
}

async function scrollToBottom() {
  await nextTick()
  scrollEl.value?.scrollTo({ top: scrollEl.value.scrollHeight, behavior: 'smooth' })
}

onMounted(async () => {
  if (!store.companies.length) await bootstrap()
})
</script>
