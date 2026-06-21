import { createServer } from "vite";

const host = process.env.GOODMONEYING_WEB_HOST ?? "127.0.0.1";
const port = Number(process.env.GOODMONEYING_WEB_PORT ?? "5173");

const server = await createServer({
  root: "apps/web",
  clearScreen: false,
  server: {
    host,
    port,
    strictPort: true
  }
});

await server.listen();
server.printUrls();

async function close() {
  await server.close();
  process.exit(0);
}

process.on("SIGTERM", close);
process.on("SIGINT", close);
