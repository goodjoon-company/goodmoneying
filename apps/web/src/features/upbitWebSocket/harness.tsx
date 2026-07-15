import React from "react";
import ReactDOM from "react-dom/client";
import { UpbitWebSocketWorkbench } from "./UpbitWebSocketWorkbench";

const gatewayUrl = new URLSearchParams(window.location.search).get("gateway") ?? undefined;
ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
  <React.StrictMode><UpbitWebSocketWorkbench gatewayUrl={gatewayUrl} /></React.StrictMode>
);
