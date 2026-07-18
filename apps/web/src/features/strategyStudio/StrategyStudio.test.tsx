import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type {
  StrategyDefinition,
  StrategyGraph,
  StrategyValidationResponse,
  StrategyVersion
} from "../../api";
import { StrategyStudio } from "./StrategyStudio";

const validHash = "a".repeat(64);
const validationOk: StrategyValidationResponse = {
  valid: true,
  errors: [],
  graphHash: validHash
};
const validationError: StrategyValidationResponse = {
  valid: false,
  graphHash: "b".repeat(64),
  errors: [
    {
      code: "cycle_detected",
      message: "전략 graph에 순환이 있습니다.",
      nodeId: null,
      edgeIndex: 1
    }
  ]
};
const strategyDefinition: StrategyDefinition = {
  strategyId: 31,
  ownerId: "operator:strategy-studio",
  name: "KRW BTC momentum",
  createdAt: "2026-07-18T08:00:00Z"
};
const publishedVersion: StrategyVersion = {
  strategyVersionId: 41,
  strategyId: 31,
  version: 1,
  schemaVersion: "strategy-graph-v1",
  status: "published",
  graphHash: validHash,
  validation: validationOk,
  graph: {
    schema_version: "strategy-graph-v1",
    nodes: [],
    edges: [],
    outputs: []
  },
  publishedAt: "2026-07-18T08:00:01Z",
  createdAt: "2026-07-18T08:00:01Z"
};

const mocks = vi.hoisted(() => ({
  createStrategy: vi.fn(),
  publishStrategyVersion: vi.fn(),
  validateStrategyGraph: vi.fn()
}));

vi.mock("../../api", async () => {
  const actual = await vi.importActual<typeof import("../../api")>("../../api");
  return {
    ...actual,
    createStrategy: mocks.createStrategy,
    publishStrategyVersion: mocks.publishStrategyVersion,
    validateStrategyGraph: mocks.validateStrategyGraph
  };
});

beforeEach(() => {
  mocks.createStrategy.mockResolvedValue(strategyDefinition);
  mocks.publishStrategyVersion.mockResolvedValue(publishedVersion);
  mocks.validateStrategyGraph.mockResolvedValue(validationOk);
});

afterEach(() => {
  cleanup();
  Object.values(mocks).forEach((mock) => mock.mockReset());
});

describe("Strategy Studio", () => {
  it("전략 그래프를 포인터 뷰와 텍스트 대안으로 함께 표시한다", async () => {
    renderStrategyStudio();

    expect(await screen.findByRole("heading", { name: "Strategy Studio" })).toBeInTheDocument();
    const pointerGraph = screen.getByRole("img", { name: "전략 그래프 포인터 뷰" });
    expect(pointerGraph).toHaveTextContent("market");
    expect(pointerGraph).toHaveTextContent("signal");
    expect(pointerGraph).toHaveTextContent("market.close → signal.price");
    const textAlternative = screen.getByRole("table", { name: "전략 그래프 텍스트 대안" });
    expect(within(textAlternative).getByRole("cell", { name: "threshold_signal" })).toBeInTheDocument();
    expect(screen.getByRole("list", { name: "전략 그래프 edge 목록" })).toHaveTextContent(
      "market.close → signal.price"
    );
    expect(screen.getByText("색상 없이 코드와 위치로 검증 상태를 표시합니다.")).toBeInTheDocument();
  });

  it("마우스 없이 키보드 대체 편집기로 출력 신호와 edge 오류 예제를 조작한다", async () => {
    const user = userEvent.setup();
    renderStrategyStudio();

    const outputName = screen.getByLabelText("출력 신호 이름");
    await user.tab();
    expect(outputName).toHaveFocus();
    await user.tab();
    expect(screen.getByRole("button", { name: "출력 신호 적용" })).toHaveFocus();
    await user.tab();
    expect(screen.getByRole("button", { name: "순환 오류 edge 추가" })).toHaveFocus();
    await user.clear(outputName);
    await user.keyboard("exit_long");
    screen.getByRole("button", { name: "출력 신호 적용" }).focus();
    await user.keyboard("{Enter}");
    expect(screen.getByText("출력 exit_long")).toBeInTheDocument();

    screen.getByRole("button", { name: "순환 오류 edge 추가" }).focus();
    await user.keyboard("{Enter}");
    const edgeList = screen.getByRole("list", { name: "전략 그래프 edge 목록" });
    expect(within(edgeList).getByText("signal.exit_long → market.close")).toBeInTheDocument();
    await user.keyboard("{Enter}");
    expect(
      within(edgeList).getAllByRole("listitem")
    ).toHaveLength(3);
  });

  it("서버 검증 오류를 role alert와 안정 코드·node·edge 텍스트로 표시한다", async () => {
    const user = userEvent.setup();
    mocks.validateStrategyGraph.mockResolvedValue(validationError);
    renderStrategyStudio();

    await user.click(screen.getByRole("button", { name: "서버 검증" }));

    const alert = await screen.findByRole("alert", { name: "전략 그래프 검증 오류" });
    expect(alert).toHaveTextContent("cycle_detected");
    expect(alert).toHaveTextContent("node -");
    expect(alert).toHaveTextContent("edge 1");
    expect(alert).toHaveTextContent("전략 graph에 순환이 있습니다.");
  });

  it("변경 전 graph의 늦은 검증 통과 응답은 현재 graph를 게시 가능하게 만들지 않는다", async () => {
    const user = userEvent.setup();
    let resolveValidation: ((value: StrategyValidationResponse) => void) | undefined;
    mocks.validateStrategyGraph.mockImplementation(
      () => new Promise<StrategyValidationResponse>((resolve) => { resolveValidation = resolve; })
    );
    renderStrategyStudio();

    await user.click(screen.getByRole("button", { name: "서버 검증" }));
    await user.click(screen.getByRole("button", { name: "순환 오류 edge 추가" }));
    await act(async () => { resolveValidation?.(validationOk); });

    expect(screen.queryByRole("status", { name: "전략 그래프 검증 상태" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "불변 version 게시" })).toBeDisabled();
  });

  it("검증 또는 게시 요청이 거절되면 사용자에게 alert로 표시한다", async () => {
    const user = userEvent.setup();
    mocks.validateStrategyGraph.mockRejectedValueOnce(new Error("검증 서비스 오류"));
    renderStrategyStudio();

    await user.click(screen.getByRole("button", { name: "서버 검증" }));
    expect(await screen.findByRole("alert", { name: "전략 그래프 검증 요청 오류" })).toHaveTextContent("검증 서비스 오류");

    mocks.validateStrategyGraph.mockResolvedValueOnce(validationOk);
    mocks.createStrategy.mockRejectedValueOnce(new Error("게시 서비스 오류"));
    await user.click(screen.getByRole("button", { name: "서버 검증" }));
    await user.click(await screen.findByRole("button", { name: "불변 version 게시" }));
    expect(await screen.findByRole("alert", { name: "전략 게시 오류" })).toHaveTextContent("게시 서비스 오류");
  });

  it("게시 실패 후 재시도는 같은 전략 정의를 재사용한다", async () => {
    const user = userEvent.setup();
    mocks.publishStrategyVersion
      .mockRejectedValueOnce(new Error("게시 서비스 오류"))
      .mockResolvedValueOnce(publishedVersion);
    renderStrategyStudio();

    await user.click(screen.getByRole("button", { name: "서버 검증" }));
    await user.click(await screen.findByRole("button", { name: "불변 version 게시" }));
    expect(await screen.findByRole("alert", { name: "전략 게시 오류" })).toHaveTextContent("게시 서비스 오류");

    await user.click(screen.getByRole("button", { name: "불변 version 게시" }));

    expect(mocks.createStrategy).toHaveBeenCalledTimes(1);
    expect(mocks.publishStrategyVersion).toHaveBeenCalledTimes(2);
    expect(mocks.publishStrategyVersion).toHaveBeenNthCalledWith(2, 31, expect.any(Object));
    const firstCommand = mocks.publishStrategyVersion.mock.calls[0][1];
    const secondCommand = mocks.publishStrategyVersion.mock.calls[1][1];
    expect(secondCommand.idempotencyKey).toBe(firstCommand.idempotencyKey);
    expect(secondCommand.requestId).toBe(firstCommand.requestId);
    expect(await screen.findByText("Version #1")).toBeInTheDocument();
  });

  it("검증을 통과한 그래프만 불변 전략 version으로 게시하고 게시 결과를 읽기 전용으로 표시한다", async () => {
    const user = userEvent.setup();
    renderStrategyStudio();

    await user.click(screen.getByRole("button", { name: "서버 검증" }));
    await user.click(await screen.findByRole("button", { name: "불변 version 게시" }));

    expect(mocks.createStrategy).toHaveBeenCalledWith(
      expect.objectContaining({
        ownerId: "operator:strategy-studio",
        name: "KRW BTC momentum"
      })
    );
    expect(mocks.publishStrategyVersion).toHaveBeenCalledWith(
      31,
      expect.objectContaining({
        graph: expect.objectContaining<Partial<StrategyGraph>>({
          schema_version: "strategy-graph-v1"
        })
      })
    );
    expect(await screen.findByText("Version #1")).toBeInTheDocument();
    expect(screen.getByText("published · 불변 version")).toBeInTheDocument();
    expect(screen.getByText(validHash)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "불변 version 게시" })).toBeDisabled();
  });
});

function renderStrategyStudio() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } }
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <StrategyStudio />
    </QueryClientProvider>
  );
}
