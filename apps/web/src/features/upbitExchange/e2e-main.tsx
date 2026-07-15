import React from "react";
import ReactDOM from "react-dom/client";
import { ExchangeWorkbench } from "./ExchangeWorkbench";
import { exchangeCatalogFixture, traceFor } from "./testFixtures";
import type { ExchangeGateway } from "./types";

type HarnessState = {
  gatewayCalls: number;
  upstreamCalls: number;
  nextStatus: number;
};

declare global {
  interface Window {
    __exchangeHarness: HarnessState;
  }
}

const state: HarnessState = {
  gatewayCalls: 0,
  upstreamCalls: 0,
  nextStatus: 0
};
window.__exchangeHarness = state;

const credentialsConfigured = new URLSearchParams(window.location.search).get("credentials") !== "absent";
const gateway: ExchangeGateway = {
  getHealth: async () => ({
    status: "ok",
    service: "upbit-gateway",
    catalog_version: "1.6.3",
    credentials_configured: credentialsConfigured
  }),
  getCatalog: async () => exchangeCatalogFixture,
  execute: async ({ endpoint_id }) => {
    state.gatewayCalls += 1;
    if (!credentialsConfigured) throw Object.assign(new Error("UNSAFE_DETAIL_MUST_NOT_RENDER"), { status: 503 });
    state.upstreamCalls += 1;
    if (state.nextStatus) {
      const status = state.nextStatus;
      state.nextStatus = 0;
      throw Object.assign(new Error("UNSAFE_DETAIL_MUST_NOT_RENDER"), { status });
    }
    if (endpoint_id === "rest.get-balance") {
      return traceFor(endpoint_id, [
        { currency: "KRW", balance: "120000", locked: "0", avg_buy_price: "0" },
        { currency: "BTC", balance: "0.125", locked: "0.01", avg_buy_price: "98000000" }
      ]);
    }
    if (endpoint_id === "rest.order-test") {
      return traceFor(endpoint_id, { result: "accepted", market: "KRW-BTC" }, 201);
    }
    return traceFor(endpoint_id, []);
  }
};

ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
  <React.StrictMode>
    <ExchangeWorkbench gateway={gateway} />
  </React.StrictMode>
);
