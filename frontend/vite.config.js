import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 生产构建走 base='/static/'（后端把 frontend/dist 挂载到 /static），
// 开发用 '/' 便于 localhost:5173 直接访问（/api 代理到后端 8000）。
export default defineConfig(({ command }) => ({
  base: command === 'build' ? '/static/' : '/',
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      '/api': { target: 'http://localhost:8000', changeOrigin: true },
    },
  },
}))
