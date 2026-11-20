import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Dev: the API (`filinglens serve`, port 8765) is proxied under /api, so no CORS setup is needed.
// Build: `filinglens serve` serves web/dist itself and the API sits at the same origin.
export default defineConfig({
  plugins: [react()],
  build: {
    chunkSizeWarningLimit: 1300,   // antd 6 (~35 components) minifies to ~1.26 MB, ~400 KB gzipped
    rollupOptions: {
      output: {
        // group only the modules actually imported (naming packages here would pull in every icon)
        manualChunks: (id) =>
          /node_modules\/(echarts|zrender)\//.test(id) ? 'echarts'
            : /node_modules\/(antd|@ant-design|@rc-component|rc-[^/]+)\//.test(id) ? 'antd'
              : undefined,
      },
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: process.env.FILINGLENS_API ?? 'http://127.0.0.1:8765',
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/api/, ''),
      },
    },
  },
})
