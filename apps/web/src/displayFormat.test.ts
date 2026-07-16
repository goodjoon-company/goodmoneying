import { describe, expect, it } from "vitest";
import {
  formatAssetAmount,
  formatKstDate,
  formatKstDateTime,
  formatMoney,
  formatNumber
} from "./displayFormat";

describe("공통 화면 표시 정책", () => {
  it("사용자용 날짜와 시간을 KST 24시간 형식으로 표시한다", () => {
    expect(formatKstDateTime("2026-07-17T05:06:07Z"))
      .toBe("2026.07.17 14:06:07 KST");
    expect(formatKstDate("2026-07-16T15:00:00Z")).toBe("2026.07.17");
    expect(formatKstDateTime("invalid-date")).toBe("invalid-date");
  });

  it("숫자는 3자리 구분자와 최대 8자리 소수를 사용한다", () => {
    expect(formatNumber("1234567.12000000")).toBe("1,234,567.12");
    expect(formatNumber("0.123456789")).toBe("0.12345679");
    expect(formatNumber("not-a-number")).toBe("not-a-number");
  });

  it("화폐와 자산 단위를 값 뒤에 표시한다", () => {
    expect(formatMoney("1234567.99", "KRW")).toBe("1,234,567 ￦");
    expect(formatMoney("1234567.12000000", "USDT")).toBe("1,234,567.12 $");
    expect(formatMoney("0.123456789", "BTC")).toBe("0.12345679 ₿");
    expect(formatAssetAmount("1234.56780000", "ETH")).toBe("1,234.5678 ETH");
    expect(formatAssetAmount("0.12500000", "BTC")).toBe("0.125 ₿");
    expect(formatMoney("not-a-number", "KRW")).toBe("not-a-number");
  });

  it("통화 문맥을 알 수 없으면 단위를 추정하거나 빈 공백을 붙이지 않는다", () => {
    expect(formatMoney("1234.5", "")).toBe("1,234.5");
    expect(formatAssetAmount("1234.5", "")).toBe("1,234.5");
  });
});
