import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [vue(), tailwindcss()],
  server: {
    port: 5180,
    proxy: {
      '/api': 'http://localhost:8788',
    },
  },
  test: {
    environment: 'jsdom',
  },
})
