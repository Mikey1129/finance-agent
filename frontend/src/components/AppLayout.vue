<template>
  <div class="layout">
    <aside class="sidebar">
      <div class="brand">
        <div class="logo">📊</div>
        <div>
          <div class="brand-name">财报分析 Agent</div>
          <div class="brand-sub">数据可信 · 分析有据</div>
        </div>
      </div>
      <nav class="nav">
        <router-link v-for="item in navItems" :key="item.path" :to="item.path" class="nav-item">
          <span class="nav-icon">{{ item.icon }}</span><span>{{ item.title }}</span>
        </router-link>
      </nav>
      <div class="sidebar-foot">
        <span class="dot" :class="store.healthy ? 'ok' : 'err'"></span>
        <span>{{ store.healthy ? '服务正常' : '服务未连接' }}</span>
      </div>
    </aside>
    <div class="main">
      <header class="topbar">
        <div class="page-title">{{ $route.meta.title }}</div>
        <div class="topbar-right">
          <span v-if="store.companies.length" class="chip" style="cursor: default">
            {{ store.companies.length }} 家公司已入库
          </span>
        </div>
      </header>
      <main class="content">
        <router-view />
      </main>
    </div>
  </div>
</template>

<script setup>
import { onMounted } from 'vue'
import { store, bootstrap } from '../store'

const navItems = [
  { path: '/', title: '指标概览', icon: '📈' },
  { path: '/chat', title: '对话分析', icon: '💬' },
  { path: '/upload', title: '上传财报', icon: '📁' },
  { path: '/skills', title: '技能分析', icon: '🧠' },
  { path: '/compare', title: '公司对比', icon: '⚖️' },
]

onMounted(bootstrap)
</script>
