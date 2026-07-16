import "@testing-library/jest-dom/vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ExchangeWorkbench } from "./ExchangeWorkbench";
import { exchangeCatalogFixture, fakeGateway, traceFor } from "./testFixtures";

afterEach(cleanup);

describe("Exchange 전체 REST 작업대", () => {
  it("카탈로그의 Exchange 38개를 7개 기능 그룹에서 빠짐없이 제공한다", async () => {
    render(<ExchangeWorkbench gateway={fakeGateway()} />);
    await screen.findByRole("status", { name: "자격 증명 상태" });

    const renderedIds = new Set<string>();
    for (const tabName of ["포켓", "계정", "주문", "출금", "입금", "Travel Rule", "서비스"]) {
      await userEvent.click(screen.getByRole("tab", { name: new RegExp(tabName) }));
      for (const button of screen.getAllByRole("button", { name: /기능 선택/ })) {
        renderedIds.add(button.getAttribute("data-endpoint-id") ?? "");
      }
    }

    expect(renderedIds).toEqual(new Set(exchangeCatalogFixture.rest_endpoints.map((item) => item.endpoint_id)));
    expect(renderedIds).toHaveLength(38);
  });

  it("배열·불리언·날짜·열거형·정수 파라미터를 타입에 맞는 입력으로 표시한다", async () => {
    render(<ExchangeWorkbench gateway={fakeGateway()} initialGroup="pocket" />);
    await userEvent.click(await screen.findByRole("button", { name: /포켓별 API Key 목록 조회 기능 선택/ }));

    expect(screen.getByLabelText("UUID 목록(uuids[])")).toHaveAttribute("placeholder", expect.stringContaining("쉼표"));
    expect(screen.getByLabelText("만료 항목 포함(include_expired)")).toHaveAttribute("type", "checkbox");

    await userEvent.click(screen.getByRole("button", { name: /메인포켓 자산 이전 목록 조회 기능 선택/ }));
    expect(screen.getByLabelText("시작 시각(start_time)")).toHaveAttribute("type", "datetime-local");
    expect(screen.getByLabelText("정렬 순서(order_by)").tagName).toBe("SELECT");

    await userEvent.click(screen.getByRole("tab", { name: /주문/ }));
    await userEvent.click(screen.getByRole("button", { name: /체결 대기 주문 목록 조회 기능 선택/ }));
    expect(screen.getByLabelText("페이지 크기(limit)")).toHaveAttribute("type", "number");
    expect(screen.getByLabelText("페이지 크기(limit)")).toHaveValue(100);
    expect(screen.getByText("기본 100 · 최소 1 · 최대 100 · 단위 개")).toBeVisible();

    await userEvent.click(screen.getByRole("tab", { name: /출금/ }));
    await userEvent.click(screen.getByRole("button", { name: /list-withdrawals 기능 선택/ }));
    expect(screen.getByLabelText("이전 커서(from)")).toHaveAttribute("type", "text");
  });

  it("현재 시각을 KST 초 단위로 채우고 숫자 제한 초과 요청을 사전에 차단한다", async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-07-16T00:00:01.000Z"));
    try {
      const execute = vi.fn();
      render(<ExchangeWorkbench gateway={fakeGateway({ execute })} initialGroup="pocket" />);
      await act(async () => { await Promise.resolve(); });
      fireEvent.click(screen.getByRole("button", { name: /메인포켓 자산 이전 목록 조회 기능 선택/ }));
      fireEvent.click(screen.getByRole("button", { name: "시작 시각(start_time) 현재 시각 입력" }));
      expect((screen.getByLabelText("시작 시각(start_time)") as HTMLInputElement).value)
        .toMatch(/^2026-07-16T09:00:01(?:\.000)?$/);

      fireEvent.click(screen.getByRole("tab", { name: /주문/ }));
      fireEvent.click(screen.getByRole("button", { name: /체결 대기 주문 목록 조회 기능 선택/ }));
      fireEvent.change(screen.getByLabelText("페이지 크기(limit)"), { target: { value: "101" } });
      fireEvent.click(screen.getByRole("button", { name: "조회 실행" }));
      expect(screen.getByRole("alert")).toHaveTextContent("최대 100 이하");
      expect(execute).not.toHaveBeenCalled();
    } finally {
      vi.useRealTimers();
    }
  });

  it("상호 배타 파라미터를 함께 입력하면 게이트웨이 호출 전에 차단한다", async () => {
    const execute = vi.fn();
    render(<ExchangeWorkbench gateway={fakeGateway({ execute })} initialGroup="order" />);
    await userEvent.click(await screen.findByRole("button", { name: /체결 대기 주문 목록 조회 기능 선택/ }));

    await userEvent.type(screen.getByLabelText("상태(state)"), "wait");
    await userEvent.type(screen.getByLabelText("상태 목록(states[])"), "watch");
    await userEvent.click(screen.getByRole("button", { name: "조회 실행" }));

    expect(screen.getByRole("alert")).toHaveTextContent("동시에 사용할 수 없습니다");
    expect(execute).not.toHaveBeenCalled();
  });

  it("공통 페어 어댑터를 사용하고 조회 결과를 잔고 표와 원본 추적으로 연결한다", async () => {
    const normalize = vi.fn((value: string) => value.trim().toUpperCase());
    const execute = vi.fn(async () => traceFor("rest.get-balance", [
      { currency: "KRW", balance: "120000", locked: "0", avg_buy_price: "0" }
    ]));
    render(
      <ExchangeWorkbench
        gateway={fakeGateway({ execute })}
        marketAdapter={{ normalize, suggestions: ["KRW-BTC"], inputLabel: "공통 페어" }}
        initialGroup="order"
      />
    );
    await userEvent.click(await screen.findByRole("button", { name: /페어별 주문 가능 정보 조회 기능 선택/ }));
    await userEvent.type(screen.getByLabelText("공통 페어"), "krw-btc");
    await userEvent.click(screen.getByRole("button", { name: "조회 실행" }));

    await waitFor(() => expect(execute).toHaveBeenCalledWith({
      endpoint_id: "rest.available-order-information",
      parameters: { market: "KRW-BTC" }
    }));
    expect(normalize).toHaveBeenCalled();
    expect(normalize).toHaveBeenLastCalledWith("KRW-BTC");

    await userEvent.click(screen.getByRole("tab", { name: /계정/ }));
    await userEvent.click(screen.getByRole("button", { name: /포켓 잔고 조회 기능 선택/ }));
    await userEvent.click(screen.getByRole("button", { name: "조회 실행" }));
    const balanceTable = await screen.findByRole("table", { name: "계정 잔고 결과" });
    expect(balanceTable).toHaveTextContent("KRW");
    expect(balanceTable).toHaveTextContent("120,000 ￦");
    await userEvent.click(screen.getByRole("button", { name: "원본 추적 열기" }));
    expect(screen.getByRole("dialog", { name: "API 원본 추적" })).toHaveTextContent("trace_id");
  });

  it("상위 공통 페어를 주입하고 변경을 통지하며 기능 전환 뒤에도 유지한다", async () => {
    const onMarketChange = vi.fn();
    render(
      <ExchangeWorkbench
        gateway={fakeGateway()}
        initialGroup="order"
        marketAdapter={{
          normalize: (value) => value.trim().toUpperCase(),
          suggestions: ["KRW-BTC", "KRW-ETH"],
          inputLabel: "공통 페어"
        }}
        marketValue="KRW-BTC"
        onMarketChange={onMarketChange}
      />
    );
    await userEvent.click(await screen.findByRole("button", { name: /페어별 주문 가능 정보 조회 기능 선택/ }));
    expect(screen.getByLabelText("공통 페어")).toHaveValue("KRW-BTC");

    fireEvent.change(screen.getByLabelText("공통 페어"), { target: { value: "krw-eth" } });
    expect(onMarketChange).toHaveBeenLastCalledWith("KRW-ETH");
    expect(screen.getByLabelText("공통 페어")).toHaveValue("KRW-ETH");

    await userEvent.click(screen.getByRole("button", { name: /주문 생성 테스트 기능 선택/ }));
    expect(screen.getByLabelText("공통 페어")).toHaveValue("KRW-ETH");
  });

  it("통합 작업대에서는 중복 market 입력을 숨기고 상위 공통 페어를 요청에 사용한다", async () => {
    const execute = vi.fn(async () => traceFor("rest.available-order-information", []));
    render(
      <ExchangeWorkbench
        gateway={fakeGateway({ execute })}
        initialGroup="order"
        marketValue="KRW-ETH"
        showMarketSelection={false}
      />
    );
    await userEvent.click(await screen.findByRole("button", { name: /페어별 주문 가능 정보 조회 기능 선택/ }));

    expect(screen.queryByLabelText("거래쌍(market)")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "조회 실행" }));
    await waitFor(() => expect(execute).toHaveBeenCalledWith({
      endpoint_id: "rest.available-order-information",
      parameters: { market: "KRW-ETH" }
    }));
  });

  it("공식 주문 테스트는 테스트 배지와 함께 확인 절차 없이 실행한다", async () => {
    const execute = vi.fn(async () => traceFor("rest.order-test", { result: "accepted" }, 201));
    render(<ExchangeWorkbench gateway={fakeGateway({ execute })} initialGroup="order" />);
    await userEvent.click(await screen.findByRole("button", { name: /주문 생성 테스트 기능 선택/ }));

    expect(screen.getByText("비파괴 테스트")).toBeVisible();
    fireEvent.change(screen.getByLabelText("거래쌍(market)"), { target: { value: "KRW-BTC" } });
    fireEvent.change(screen.getByLabelText("주문 방향(side)"), { target: { value: "bid" } });
    await userEvent.click(screen.getByRole("button", { name: "주문 테스트 실행" }));

    await waitFor(() => expect(execute).toHaveBeenCalledTimes(1));
    expect(screen.queryByRole("dialog", { name: /확인/ })).not.toBeInTheDocument();
  });

  it("실행 중 기능을 바꾸면 이전 응답을 새 기능의 결과나 원본 추적으로 표시하지 않는다", async () => {
    let resolveRequest: ((trace: ReturnType<typeof traceFor>) => void) | undefined;
    const execute = vi.fn(() => new Promise<ReturnType<typeof traceFor>>((resolve) => {
      resolveRequest = resolve;
    }));
    render(<ExchangeWorkbench gateway={fakeGateway({ execute })} initialGroup="asset" />);
    await userEvent.click(await screen.findByRole("button", { name: /포켓 잔고 조회 기능 선택/ }));
    await userEvent.click(screen.getByRole("button", { name: "조회 실행" }));
    await waitFor(() => expect(execute).toHaveBeenCalledTimes(1));

    await userEvent.click(screen.getByRole("tab", { name: /주문/ }));
    await userEvent.click(screen.getByRole("button", { name: /주문 생성 테스트 기능 선택/ }));
    await act(async () => {
      resolveRequest?.(traceFor("rest.get-balance", [{ currency: "KRW", balance: "10" }]));
    });
    expect(screen.queryByRole("button", { name: "원본 추적 열기" })).not.toBeInTheDocument();
    expect(screen.queryByRole("table", { name: /결과/ })).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "주문 생성 테스트" })).toBeVisible();
  });

  it("게이트웨이 응답 endpoint가 요청과 다르면 결과와 원본 추적을 거부한다", async () => {
    const execute = vi.fn(async () => traceFor("rest.get-order", [{ uuid: "wrong-source" }]));
    render(<ExchangeWorkbench gateway={fakeGateway({ execute })} initialGroup="asset" />);
    await userEvent.click(await screen.findByRole("button", { name: /포켓 잔고 조회 기능 선택/ }));
    await userEvent.click(screen.getByRole("button", { name: "조회 실행" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("응답 출처가 선택한 API 기능과 일치하지 않습니다");
    expect(screen.queryByRole("button", { name: "원본 추적 열기" })).not.toBeInTheDocument();
    expect(screen.queryByText("wrong-source")).not.toBeInTheDocument();
  });

  it("실제 주문·취소·이전·입출금·검증은 위험 배너와 미리보기만 보이고 실행하지 않는다", async () => {
    const execute = vi.fn();
    render(<ExchangeWorkbench gateway={fakeGateway({ execute })} initialGroup="order" />);
    await userEvent.click(await screen.findByRole("button", { name: /주문 생성 기능 선택/ }));
    fireEvent.change(screen.getByLabelText("거래쌍(market)"), { target: { value: "krw-btc" } });

    expect(screen.getByRole("alert")).toHaveTextContent("업비트로 전송하지 않습니다");
    expect(screen.getByRole("region", { name: "최종 요청 미리보기" })).toHaveTextContent("rest.new-order");
    expect(screen.getByRole("region", { name: "최종 요청 미리보기" })).toHaveTextContent("KRW-BTC");
    expect(screen.getByRole("button", { name: "정책으로 전송 차단됨" })).toBeDisabled();
    expect(execute).not.toHaveBeenCalled();
  });

  it.each([
    ["order", /개별 주문 조회 기능 선택/, "uuid 또는 identifier"],
    ["order", /개별 주문 취소 접수 기능 선택/, "uuid 또는 identifier"],
    ["withdrawal", /개별 출금 조회 기능 선택/, "uuid 또는 txid"],
    ["deposit", /개별 입금 조회 기능 선택/, "uuid 또는 txid + currency"]
  ] as const)(
    "%s 기능의 대체 필수 입력 조합을 폼에서 안내한다",
    async (initialGroup, endpointName, guidance) => {
      render(<ExchangeWorkbench gateway={fakeGateway()} initialGroup={initialGroup} />);
      await userEvent.click(await screen.findByRole("button", { name: endpointName }));

      expect(screen.getByText((content) => content.includes(guidance))).toBeVisible();
    }
  );

  it("영구 차단된 주문 취소도 대체 필수 입력 조합의 충족 여부를 표시한다", async () => {
    render(<ExchangeWorkbench gateway={fakeGateway()} initialGroup="order" />);
    await userEvent.click(await screen.findByRole("button", { name: /개별 주문 취소 접수 기능 선택/ }));
    expect(screen.getByText(/필수 입력 조합.*미충족/)).toBeVisible();

    await userEvent.type(screen.getByLabelText("식별자(identifier)"), "client-order-id");

    expect(screen.getByText(/필수 입력 조합.*충족/)).toBeVisible();
    expect(screen.getByRole("button", { name: "정책으로 전송 차단됨" })).toBeDisabled();
  });

  it("입금 조회의 txid 대안은 currency를 함께 입력해야 충족한다", async () => {
    const execute = vi.fn(async () => traceFor("rest.get-deposit", []));
    render(<ExchangeWorkbench gateway={fakeGateway({ execute })} initialGroup="deposit" />);
    await userEvent.click(await screen.findByRole("button", { name: /개별 입금 조회 기능 선택/ }));
    await userEvent.type(screen.getByLabelText("트랜잭션 ID(txid)"), "transaction-id");

    await userEvent.click(screen.getByRole("button", { name: "조회 실행" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("필수 입력 조합");
    expect(execute).not.toHaveBeenCalled();

    await userEvent.type(screen.getByLabelText("통화(currency)"), "BTC");
    await userEvent.click(screen.getByRole("button", { name: "조회 실행" }));
    await waitFor(() => expect(execute).toHaveBeenCalledWith(expect.objectContaining({
      parameters: { txid: "transaction-id", currency: "BTC" }
    })));
  });

  it.each([
    ["order", /개별 주문 조회 기능 선택/, "식별자(identifier)"],
    ["withdrawal", /개별 출금 조회 기능 선택/, "트랜잭션 ID(txid)"],
    ["deposit", /개별 입금 조회 기능 선택/, "UUID(uuid)"]
  ] as const)(
    "%s 조회는 대체 필수 입력 조합이 없으면 실행 전에 차단한다",
    async (initialGroup, endpointName, validField) => {
      const execute = vi.fn(async ({ endpoint_id }: { endpoint_id: string }) => traceFor(endpoint_id, []));
      render(<ExchangeWorkbench gateway={fakeGateway({ execute })} initialGroup={initialGroup} />);
      await userEvent.click(await screen.findByRole("button", { name: endpointName }));

      await userEvent.click(screen.getByRole("button", { name: "조회 실행" }));
      expect(await screen.findByRole("alert")).toHaveTextContent("필수 입력 조합");
      expect(execute).not.toHaveBeenCalled();

      await userEvent.type(screen.getByLabelText(validField), "test-value");
      await userEvent.click(screen.getByRole("button", { name: "조회 실행" }));
      await waitFor(() => expect(execute).toHaveBeenCalledTimes(1));
    }
  );

  it("비-2xx 추적 봉투는 친화 오류와 원본 추적을 함께 제공한다", async () => {
    const execute = vi.fn(async () => traceFor(
      "rest.get-balance",
      { error: { name: "too_many_requests" } },
      429
    ));
    render(<ExchangeWorkbench gateway={fakeGateway({ execute })} initialGroup="asset" />);
    await userEvent.click(await screen.findByRole("button", { name: /포켓 잔고 조회 기능 선택/ }));

    await userEvent.click(screen.getByRole("button", { name: "조회 실행" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("요청 수 제한");
    await userEvent.click(screen.getByRole("button", { name: "원본 추적 열기" }));
    expect(screen.getByRole("dialog", { name: "API 원본 추적" })).toHaveTextContent("too_many_requests");
  });

  it("사용자가 명시한 false 불리언을 요청에 직렬화한다", async () => {
    const execute = vi.fn(async () => traceFor("rest.get-pocket-api-keys", []));
    render(<ExchangeWorkbench gateway={fakeGateway({ execute })} initialGroup="pocket" />);
    await userEvent.click(await screen.findByRole("button", { name: /포켓별 API Key 목록 조회 기능 선택/ }));
    const checkbox = screen.getByLabelText("만료 항목 포함(include_expired)");
    await userEvent.click(checkbox);
    await userEvent.click(checkbox);

    await userEvent.click(screen.getByRole("button", { name: "조회 실행" }));

    await waitFor(() => expect(execute).toHaveBeenCalledWith(expect.objectContaining({
      parameters: { include_expired: false }
    })));
  });

  it("서버 자격 증명 부재와 503을 친화적으로 표시하고 비밀값은 DOM에 넣지 않는다", async () => {
    const gateway = fakeGateway({
      health: { status: "ok", service: "upbit-gateway", catalog_version: "1.6.3", credentials_configured: false },
      execute: async () => { throw Object.assign(new Error("SECRET_KEY=never-render"), { status: 503 }); }
    });
    const { container } = render(<ExchangeWorkbench gateway={gateway} initialGroup="asset" />);

    expect(await screen.findByRole("status", { name: "자격 증명 상태" })).toHaveTextContent("서버 미설정");
    await userEvent.click(screen.getByRole("button", { name: /포켓 잔고 조회 기능 선택/ }));
    await userEvent.click(screen.getByRole("button", { name: "조회 실행" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("서버에 API Key가 설정되지 않았습니다");
    expect(container).not.toHaveTextContent("SECRET_KEY");
    expect(container.querySelector('input[type="password"]')).toBeNull();
  });

  it("탭·기능 목록·폼·결과 영역에 접근 가능한 이름과 관계를 제공한다", async () => {
    render(<ExchangeWorkbench gateway={fakeGateway()} />);
    const tabs = await screen.findByRole("tablist", { name: "Exchange API 기능 그룹" });
    const tabButtons = within(tabs).getAllByRole("tab");
    expect(tabButtons).toHaveLength(7);
    for (const tab of tabButtons) {
      expect(tab).toHaveAttribute("aria-controls");
      expect(document.getElementById(tab.getAttribute("aria-controls") ?? "")).not.toBeNull();
    }
    tabButtons[0].focus();
    await userEvent.keyboard("{ArrowRight}");
    expect(screen.getByRole("tab", { name: /계정/ })).toHaveFocus();
    expect(screen.getByRole("tab", { name: /계정/ })).toHaveAttribute("aria-selected", "true");
    const panelId = screen.getByRole("tab", { name: /계정/ }).getAttribute("aria-controls");
    expect(document.getElementById(panelId ?? "")).toHaveAttribute("role", "tabpanel");
    expect(screen.getByRole("main", { name: "Exchange API 작업대" })).toBeVisible();
    expect(screen.getByRole("region", { name: "요청 구성" })).toBeVisible();
    expect(screen.getByRole("region", { name: "응답 결과" })).toBeVisible();
  });

  it("원본 추적 모달로 포커스를 이동하고 Escape로 닫은 뒤 호출 버튼에 복귀한다", async () => {
    render(<ExchangeWorkbench gateway={fakeGateway()} initialGroup="asset" />);
    await userEvent.click(await screen.findByRole("button", { name: /포켓 잔고 조회 기능 선택/ }));
    await userEvent.click(screen.getByRole("button", { name: "조회 실행" }));
    const trigger = await screen.findByRole("button", { name: "원본 추적 열기" });

    await userEvent.click(trigger);

    expect(trigger.querySelector("svg")).not.toBeNull();
    expect(trigger).toHaveTextContent("");
    expect(screen.getByRole("button", { name: "원본 추적 닫기" })).toHaveFocus();
    await userEvent.keyboard("{Tab}");
    expect(screen.getByRole("button", { name: "원본 추적 닫기" })).toHaveFocus();
    await userEvent.keyboard("{Escape}");
    expect(screen.queryByRole("dialog", { name: "API 원본 추적" })).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });
});
