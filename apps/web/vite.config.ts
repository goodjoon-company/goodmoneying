import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig(() => {
  const apiHost = process.env.GOODMONEYING_API_HOST ?? "127.0.0.1";
  const apiPort = process.env.GOODMONEYING_API_PORT ?? "8000";
  const apiTarget = process.env.VITE_DEV_API_PROXY_TARGET ?? `http://${apiHost}:${apiPort}`;
  const gatewayTarget = process.env.VITE_DEV_UPBIT_GATEWAY_PROXY_TARGET ?? "http://127.0.0.1:8001";
  const operatorToken = process.env.GOODMONEYING_OPERATOR_TOKEN ?? "local-dev-token";

  return {
    plugins: [react()],
    server: {
      port: 5173,
      proxy: {
        "/api": {
          target: apiTarget,
          changeOrigin: true,
          ws: true,
          headers: { "X-Operator-Token": operatorToken },
          rewrite: (path) => path.replace(/^\/api/, "")
        },
        "/upbit-gateway": {
          target: gatewayTarget,
          changeOrigin: true,
          ws: true,
          xfwd: true,
          headers: { "X-Operator-Token": operatorToken },
          configure: (proxy) => {
            proxy.on("proxyReqWs", (proxyRequest, request) => {
              proxyRequest.setHeader("X-Forwarded-Host", request.headers.host ?? "");
              proxyRequest.setHeader("X-Forwarded-Proto", "http");
              proxyRequest.setHeader("X-Operator-Token", operatorToken);
            });
          },
          rewrite: (path) => path.replace(/^\/upbit-gateway/, "")
        }
      }
    }
  };
});
