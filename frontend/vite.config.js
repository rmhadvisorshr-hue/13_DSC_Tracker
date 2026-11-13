import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig(({ mode }) => ({
  base: mode === 'mobile' ? './' : '/',
  plugins: [react(), tailwindcss()],
  server: { port: 5173, strictPort: true },
}))
