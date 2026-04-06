import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { visualizer } from 'rollup-plugin-visualizer'

function manualChunks(id) {
  if (id.includes('node_modules')) {
    if (
      id.includes('/react/') ||
      id.includes('/react-dom/') ||
      id.includes('@remix-run/router') ||
      id.includes('/history/') ||
      id.includes('react-router') ||
      id.includes('scheduler')
    ) {
      return 'framework'
    }
    if (id.includes('lightweight-charts')) return 'chart-vendor'
    if (id.includes('@xyflow/react') || id.includes('/dagre/')) return 'pipeline-vendor'
    if (id.includes('lucide-react')) return 'icon-vendor'
    return undefined
  }

  if (id.includes('/src/components/opportunities/')) return 'surface-opportunities'

  if (
    id.includes('/src/pages/Pipeline.jsx') ||
    id.includes('/src/components/pipeline/') ||
    id.includes('/src/hooks/usePipelineGraph.js') ||
    id.includes('/src/hooks/usePipelineUrlState.js') ||
    id.includes('/src/config/pipelineFeatures.js')
  ) {
    return 'route-pipeline'
  }

  return undefined
}

export default defineConfig(({ mode }) => ({
  define: {
    __APP_VERSION__: JSON.stringify('2.0.0-hardening'),
  },
  plugins: [
    react(),
    mode === 'stats' && visualizer({
      open: true,
      filename: 'dist/stats.html',
      gzipSize: true,
      brotliSize: true,
    })
  ],
  server: {
    port: 3000,
    proxy: {
      '/v1': {
        target: 'http://localhost:8002',
        changeOrigin: true,
      },
      '/health': {
        target: 'http://localhost:8002',
        changeOrigin: true,
      },
    }
  },
  build: {
    rollupOptions: {
      output: {
        manualChunks,
      },
    },
  }
}))
