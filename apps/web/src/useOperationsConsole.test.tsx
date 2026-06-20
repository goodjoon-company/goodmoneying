import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { createFixtureOperationsDataClient } from "./operationsFixture";
import { useOperationsConsole } from "./useOperationsConsole";

function Harness() {
  const consoleState = useOperationsConsole({
    dataClient: createFixtureOperationsDataClient(),
    refetchOnDashboard: false
  });

  if (!consoleState.snapshot) return <span>loading</span>;

  return (
    <section>
      <strong>{consoleState.snapshot.dashboard.status}</strong>
      <span>선택 {consoleState.selectedInstrumentId}</span>
      <span>상세 {consoleState.isDetailOpen ? "열림" : "닫힘"}</span>
      <button type="button" onClick={() => void consoleState.openInstrumentDetail(3)}>
        3번 상세
      </button>
    </section>
  );
}

describe("운영 화면 상태 Module", () => {
  it("첫 스냅샷의 첫 거래 상품을 선택하고 상세 열기 상태를 관리한다", async () => {
    const user = userEvent.setup();
    const queryClient = new QueryClient();

    render(
      <QueryClientProvider client={queryClient}>
        <Harness />
      </QueryClientProvider>
    );

    await waitFor(() => expect(screen.getByText("normal")).toBeInTheDocument());
    expect(screen.getByText("선택 1")).toBeInTheDocument();
    expect(screen.getByText("상세 닫힘")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "3번 상세" }));

    expect(await screen.findByText("선택 3")).toBeInTheDocument();
    expect(screen.getByText("상세 열림")).toBeInTheDocument();
  });
});
