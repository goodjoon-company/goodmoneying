import { UpbitApiWorkbench } from "./upbit-api-test/UpbitApiWorkbench";
import type { WorkbenchModuleExtension, WorkbenchModuleId } from "./upbit-api-test/types";

export function UpbitApiTest({ moduleId, market, onMarketChange, extensions }: {
  moduleId: WorkbenchModuleId;
  market?: string;
  onMarketChange?: (market: string) => void;
  extensions?: WorkbenchModuleExtension[];
}) {
  return <UpbitApiWorkbench moduleId={moduleId} market={market}
    onMarketChange={onMarketChange} extensions={extensions} />;
}
