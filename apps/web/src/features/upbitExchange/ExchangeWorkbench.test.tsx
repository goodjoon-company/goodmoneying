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

    expect(screen.getByLabelText("uuids[]")).toHaveAttribute("placeholder", expect.stringContaining("쉼표"));
    expect(screen.getByLabelText("include_expired")).toHaveAttribute("type", "checkbox");

    await userEvent.click(screen.getByRole("button", { name: /메인포켓 자산 이전 목록 조회 기능 선택/ }));
    expect(screen.getByLabelText("start_time")).toHaveAttribute("type", "datetime-local");
    expect(screen.getByLabelText("order_by").tagName).toBe("SELECT");

    await userEvent.click(screen.getByRole("tab", { name: /주문/ }));
    await userEvent.click(screen.getByRole("button", { name: /체결 대기 주문 목록 조회 기능 선택/ }));
    expect(screen.getByLabelText("limit")).toHaveAttribute("type", "number");
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
    expect(await screen.findByRole("table", { name: "계정 잔고 결과" })).toHaveTextContent("KRW");
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

  it("공식 주문 테스트는 테스트 배지와 함께 확인 절차 없이 실행한다", async () => {
    const execute = vi.fn(async () => traceFor("rest.order-test", { result: "accepted" }, 201));
    render(<ExchangeWorkbench gateway={fakeGateway({ execute })} initialGroup="order" />);
    await userEvent.click(await screen.findByRole("button", { name: /주문 생성 테스트 기능 선택/ }));

    expect(screen.getByText("비파괴 테스트")).toBeVisible();
    fireEvent.change(screen.getByLabelText("market"), { target: { value: "KRW-BTC" } });
    fireEvent.change(screen.getByLabelText("side"), { target: { value: "bid" } });
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

    expect(screen.getByRole("alert")).toHaveTextContent("업비트로 전송하지 않습니다");
    expect(screen.getByRole("region", { name: "최종 요청 미리보기" })).toHaveTextContent("rest.new-order");
    expect(screen.getByRole("button", { name: "정책으로 전송 차단됨" })).toBeDisabled();
    expect(execute).not.toHaveBeenCalled();
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
    expect(within(tabs).getAllByRole("tab")).toHaveLength(7);
    expect(screen.getByRole("main", { name: "Exchange API 작업대" })).toBeVisible();
    expect(screen.getByRole("region", { name: "요청 구성" })).toBeVisible();
    expect(screen.getByRole("region", { name: "응답 결과" })).toBeVisible();
  });
});
