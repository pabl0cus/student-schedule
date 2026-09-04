import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import svgr from 'vite-plugin-svgr';
import tailwindcss from '@tailwindcss/vite';

const backendProxy = {
  '/recordings-api': {
    target: 'http://127.0.0.1:8012',
    changeOrigin: true,
    headers: {
      'X-Forwarded-Prefix': '/recordings-api',
      'X-KPI-Local-Request': '1',
    },
    rewrite: (path: string) => path.replace(/^\/recordings-api/, ''),
  },
  '/community/v1': {
    target: 'http://127.0.0.1:8012',
    changeOrigin: true,
  },
};

export default defineConfig({
  plugins: [react(), svgr(), tailwindcss()],
  server: {
    open: true,
    port: 3001,
    strictPort: true,
    proxy: backendProxy,
  },
  preview: {
    port: 3001,
    strictPort: true,
    proxy: backendProxy,
  },
  build: {
    outDir: 'build',
    chunkSizeWarningLimit: 1000,
  },
});
