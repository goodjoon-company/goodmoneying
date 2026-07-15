import { ExchangeWorkbench, createHttpExchangeGateway } from "../features/upbitExchange";
import { UpbitApiWorkbench } from "./upbit-api-test/UpbitApiWorkbench";
import type {
  WorkbenchExtensionProps,
  WorkbenchModuleExtension,
  WorkbenchModuleId
} from "./upbit-api-test/types";

const exchangeGateway = createHttpExchangeGateway(
  import.meta.env.VITE_UPBIT_GATEWAY_BASE_URL ?? "/upbit-gateway"
);

function ExchangeWorkbenchExtension({ context, onContextChange }: WorkbenchExtensionProps) {
  return <ExchangeWorkbench gateway={exchangeGateway} marketValue={context.market}
    onMarketChange={(market) => onContextChange(marketContext(market))} />;
}

const defaultExtensions: WorkbenchModuleExtension[] = [{
  id: "exchange",
  label: "Exchange API",
  Component: ExchangeWorkbenchExtension
}];

export function UpbitApiTest({ moduleId, market, onMarketChange, extensions }: {
  moduleId: WorkbenchModuleId;
  market?: string;
  onMarketChange?: (market: string) => void;
  extensions?: WorkbenchModuleExtension[];
}) {
  const resolvedExtensions = [
    ...defaultExtensions.filter((candidate) => !extensions?.some((item) => item.id === candidate.id)),
    ...(extensions ?? [])
  ];
  return <UpbitApiWorkbench moduleId={moduleId} market={market}
    onMarketChange={onMarketChange} extensions={resolvedExtensions} />;
}

function marketContext(market: string) {
  const normalized = market.trim().toUpperCase();
  const [quote = "KRW", base = "BTC"] = normalized.split("-");
  return { market: normalized, quote, base };
}
