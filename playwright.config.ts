import { defineConfig, devices } from "@playwright/test";

const apiBaseURL = process.env.E2E_API_BASE_URL ?? "http://127.0.0.1:18000";
const webBaseURL = process.env.E2E_WEB_BASE_URL ?? "http://127.0.0.1:15173";
const skipWebServer = process.env.E2E_SKIP_WEBSERVER === "1";
const apiURL = new URL(apiBaseURL);
const webURL = new URL(webBaseURL);

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
            `GOODMONEYING_DEMO_DATA=1 PYTHONPATH=apps/api:apps/worker:packages/shared uv run uvicorn goodmoneying_api.main:app --host ${apiURL.hostname} --port ${apiURL.port}`,
          url: `${apiBaseURL}/health`,
          reuseExistingServer: false,
          timeout: 30_000
        },
        {
          command: `VITE_API_BASE_URL=${apiBaseURL} VITE_OPERATOR_TOKEN=local-dev-token npm --workspace apps/web run dev -- --host ${webURL.hostname} --port ${webURL.port}`,
          url: webBaseURL,
          reuseExistingServer: false,
          timeout: 30_000
        }
      ]
});
