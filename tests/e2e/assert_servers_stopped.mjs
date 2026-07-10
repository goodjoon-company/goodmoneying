import net from "node:net";

const RETRY_INTERVAL_MS = 100;
const STOP_TIMEOUT_MS = 2_000;

function connectionTarget(baseURL) {
  const url = new URL(baseURL);
  const port = Number(url.port || (url.protocol === "https:" ? 443 : 80));
  return { host: url.hostname, port };
}

function canConnect({ host, port }) {
  return new Promise((resolve) => {
    const socket = net.createConnection({ host, port });
    socket.setTimeout(300);
    socket.once("connect", () => {
      socket.destroy();
      resolve(true);
    });
    socket.once("error", () => resolve(false));
    socket.once("timeout", () => {
      socket.destroy();
      resolve(false);
    });
  });
}

function wait(delayMs) {
  return new Promise((resolve) => setTimeout(resolve, delayMs));
}

async function assertStopped(name, target) {
  const deadline = Date.now() + STOP_TIMEOUT_MS;
  while (await canConnect(target)) {
    if (Date.now() >= deadline) {
      throw new Error(
        `${name} 시험 서버가 종료되지 않았습니다: ${target.host}:${target.port}`,
      );
    }
    await wait(RETRY_INTERVAL_MS);
  }
  console.log(`${name} 시험 서버 종료 확인: ${target.host}:${target.port}`);
}

if (process.env.E2E_SKIP_WEBSERVER === "1") {
  console.log("배포 환경 E2E는 외부 서버를 사용하므로 로컬 서버 종료 검증을 건너뜁니다.");
} else {
  await Promise.all([
    assertStopped(
      "API",
      connectionTarget(
        process.env.E2E_API_BASE_URL ?? "http://127.0.0.1:18000",
      ),
    ),
    assertStopped(
      "웹",
      connectionTarget(
        process.env.E2E_WEB_BASE_URL ?? "http://127.0.0.1:15173",
      ),
    ),
  ]);
}
