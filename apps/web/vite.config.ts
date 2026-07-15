import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig(() => {
  const apiHost = process.env.GOODMONEYING_API_HOST ?? "127.0.0.1";
  const apiPort = process.env.GOODMONEYING_API_PORT ?? "8000";
  const apiTarget = process.env.VITE_DEV_API_PROXY_TARGET ?? `http://${apiHost}:${apiPort}`;
  const upbitGatewayTarget = process.env.VITE_DEV_UPBIT_GATEWAY_PROXY_TARGET ?? "http://127.0.0.1:8001";

  return {
    plugins: [react()],
    server: {
      port: 5173,
      proxy: {
        "/api": {
          target: apiTarget,
          changeOrigin: true,
          ws: true,
          rewrite: (path) => path.replace(/^\/api/, "")
        },
        "/upbit-gateway": {
          target: upbitGatewayTarget,
          changeOrigin: true,
          rewrite: (path) => path.replace(/^\/upbit-gateway/, "")
        }
      }
    }
  };
});
