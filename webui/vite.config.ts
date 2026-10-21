import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

// 开发服务器把 /api 代理到本机后端。端口与 base/server_port.py 同一套规则
// （SIYE_PORT，默认产品端口 8080）—— 同一个仓库的多个 worktree 并行开发时，
// 各自的 `npm run dev` 都能指向自己的后端。
const backendPort = process.env.SIYE_PORT || '8080'

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    rollupOptions: {
      output: {
        // 框架层单独成块：主 chunk 之前是 676 kB（gzip 220 kB），
        // 拆出来之后业务代码改动也不会让这些依赖的 hash 变。
        manualChunks: {
          'vendor-react': ['react', 'react-dom', 'i18next', 'react-i18next', 'i18next-browser-languagedetector'],
          'vendor-query': ['@tanstack/react-query'],
        },
      },
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: `http://localhost:${backendPort}`,
        changeOrigin: true,
        ws: true,  // 启用 WebSocket 代理
      },
    },
  },
})
