import { useEffect, useState } from "react";
import { useQuery, type UseQueryResult } from "@tanstack/react-query";
import { createHttpOperationsDataClient, type OperationsDataClient } from "./operationsData";
import type { OperationsSnapshot } from "./api";

export type SectionId = "dashboard" | "targets" | "coverage" | "markets" | "analysis" | "system" | "upbit-api-test";

export type UseOperationsConsoleOptions = {
  dataClient?: OperationsDataClient;
  refetchOnDashboard?: boolean;
};

export type OperationsConsoleState = {
  snapshot: OperationsSnapshot | null;
  activeSection: SectionId;
  setActiveSection: (section: SectionId) => void;
  selectedInstrumentId: number | null;
  isDetailOpen: boolean;
  setDetailOpen: (open: boolean) => void;
  openInstrumentDetail: (instrumentId: number) => Promise<void>;
  query: UseQueryResult<OperationsSnapshot, Error>;
};

const defaultDataClient = createHttpOperationsDataClient();

export function useOperationsConsole(
  options: UseOperationsConsoleOptions = {}
): OperationsConsoleState {
  const dataClient = options.dataClient ?? defaultDataClient;
  const refetchOnDashboard = options.refetchOnDashboard ?? true;
  const [snapshot, setSnapshot] = useState<OperationsSnapshot | null>(null);
  const [activeSection, setActiveSection] = useState<SectionId>("dashboard");
  const [selectedInstrumentId, setSelectedInstrumentId] = useState<number | null>(null);
  const [isDetailOpen, setDetailOpen] = useState(false);
  const query = useQuery<OperationsSnapshot, Error>({
    queryKey: ["operations"],
    queryFn: () => dataClient.loadOperationsSnapshot(),
    refetchInterval:
      refetchOnDashboard && activeSection === "dashboard"
        ? 15_000
        : activeSection === "targets"
          ? 10_000
          : false
  });

  useEffect(() => {
    if (!query.data) return;
    setSnapshot(query.data);
    setSelectedInstrumentId(
      (current) => current ?? query.data.dashboard.targets[0]?.instrument.id ?? null
    );
  }, [query.data]);

  useEffect(() => {
    if (!dataClient.subscribeDashboardSummary) return;
    return dataClient.subscribeDashboardSummary((dashboard) => {
      setSnapshot((previous) =>
        previous
          ? {
              ...previous,
              dashboard,
              notifications: dashboard.alerts
            }
          : previous
      );
      setSelectedInstrumentId((current) => current ?? dashboard.targets[0]?.instrument.id ?? null);
    });
  }, [dataClient]);

  const openInstrumentDetail = async (instrumentId: number) => {
    setSelectedInstrumentId(instrumentId);
    setDetailOpen(true);
    if (!snapshot || snapshot.detail?.instrument.id === instrumentId) return;
    const next = await dataClient.loadInstrumentSnapshot(instrumentId);
    setSnapshot((previous) =>
      previous ? { ...previous, detail: next.detail, candles: next.candles } : previous
    );
  };

  return {
    snapshot,
    activeSection,
    setActiveSection,
    selectedInstrumentId,
    isDetailOpen,
    setDetailOpen,
    openInstrumentDetail,
    query
  };
}
