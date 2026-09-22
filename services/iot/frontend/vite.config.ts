import { defineConfig, loadEnv } from 'vite'
import react, { reactCompilerPreset } from '@vitejs/plugin-react'
import babel from '@rolldown/plugin-babel'

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '');
  return {
    plugins: [
      react(),
      babel({ presets: [reactCompilerPreset()] })
    ],
    server: {
      proxy: {
        '/backend': {
          target: env.VITE_BACKEND,
          changeOrigin: true,
          // The backend serves a self-signed TLS cert until M5 puts a real
          // proxy in front, so skip cert verification here.
          secure: false,
          rewrite: (path) => path.replace(/^\/backend/, '') // strip the /backend prefix
        }
      }
    }
  };
})
