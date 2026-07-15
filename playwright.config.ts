import { defineConfig, devices } from "@playwright/test";

const apiBaseURL = process.env.E2E_API_BASE_URL ?? "http://127.0.0.1:18000";
const webBaseURL = process.env.E2E_WEB_BASE_URL ?? "http://127.0.0.1:15173";
const operatorToken = process.env.E2E_OPERATOR_TOKEN ?? "local-dev-token";
const skipWebServer = process.env.E2E_SKIP_WEBSERVER === "1";
const apiURL = new URL(apiBaseURL);
const webURL = new URL(webBaseURL);
const inheritedEnv = Object.fromEntries(
  Object.entries(process.env).filter((entry): entry is [string, string] => entry[1] !== undefined)
);

export default defineConfig({
  testDir: "tests/e2e",
  timeout: 120_000,
  expect: {
    timeout: 15_000
  },
  use: {
    baseURL: webBaseURL,
    trace: "retain-on-failure",
    screenshot: "only-on-failure"
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] }
    }
  ],
  webServer: skipWebServer
    ? undefined
    : [
        {
          command:
            'uv run python tests/e2e/seeded_api.py --host "$E2E_API_HOST" --port "$E2E_API_PORT"',
          env: {
            ...inheritedEnv,
            E2E_API_HOST: apiURL.hostname,
            E2E_API_PORT: apiURL.port,
            GOODMONEYING_DATABASE_URL: "",
            GOODMONEYING_DEMO_DATA: "0",
            GOODMONEYING_OPERATOR_TOKEN: operatorToken,
            PYTHONPATH: "apps/api:apps/worker:apps/upbit_gateway:packages/shared"
          },
          url: `${apiBaseURL}/health`,
          reuseExistingServer: false,
          timeout: 30_000
        },
        {
          command:
            'npm --workspace apps/web run dev -- --host "$E2E_WEB_HOST" --port "$E2E_WEB_PORT"',
          env: {
            ...inheritedEnv,
            E2E_WEB_HOST: webURL.hostname,
            E2E_WEB_PORT: webURL.port,
            VITE_API_BASE_URL: apiBaseURL,
            VITE_DEV_UPBIT_GATEWAY_PROXY_TARGET: apiBaseURL,
            VITE_OPERATOR_TOKEN: operatorToken
          },
          url: webBaseURL,
          reuseExistingServer: false,
          timeout: 30_000
        }
      ]
});
