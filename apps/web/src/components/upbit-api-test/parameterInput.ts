import type { CatalogParameter, ParameterValue } from "./types";

export type ParameterErrors = Record<string, string>;

const DYNAMIC_SOURCE_LABELS: Record<string, string> = {
  "rest.available-order-information": "주문 가능 정보",
  "rest.list-orderbook-instruments": "호가 정책",
  "rest.available-withdrawal-information": "출금 가능 정보"
};

export function currentParameterInputValue(
  parameter: CatalogParameter,
  now = new Date()
): string {
  if (parameter.format === "time") {
    const shifted = parameter.timezone === "UTC" ? now : new Date(now.getTime() + 9 * 60 * 60_000);
    return shifted.toISOString().slice(11, 19);
  }
  const shifted = parameter.timezone === "UTC" ? now : new Date(now.getTime() + 9 * 60 * 60_000);
  return shifted.toISOString().slice(0, 19);
}

export function coerceParameterInputValue(
  parameter: CatalogParameter,
  value: string | boolean
): ParameterValue {
  if (parameter.type === "boolean") return value === true || value === "true";
  if (parameter.format === "date-time") return parameterDateTimeToIso(parameter, String(value));
  if (parameter.type === "integer" || parameter.type === "number") return Number(value);
  if (parameter.type === "array") {
    return String(value).split(",").map((item) => item.trim());
  }
  return String(value);
}

export function formatParameterInputValue(
  parameter: CatalogParameter,
  value: ParameterValue | undefined
): string {
  if (value === undefined) return "";
  if (parameter.format !== "date-time") {
    return Array.isArray(value) ? value.join(",") : String(value);
  }
  if (/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?$/.test(String(value))) {
    return String(value);
  }
  const date = new Date(String(value));
  if (Number.isNaN(date.getTime())) return String(value);
  const shifted = parameter.timezone === "UTC"
    ? date
    : new Date(date.getTime() + 9 * 60 * 60_000);
  return shifted.toISOString().slice(0, parameter.step === 1 ? 19 : 16);
}

export function initialParameterInputValues(
  parameters: CatalogParameter[],
  options: { preferMaximumReadCount?: boolean } = {}
): Record<string, string | boolean> {
  return Object.fromEntries(parameters.flatMap((parameter) => {
    if (options.preferMaximumReadCount && parameter.name === "count" && parameter.maximum !== undefined) {
      return [[parameter.name, String(parameter.maximum)]];
    }
    if (parameter.default !== undefined) {
      const value = Array.isArray(parameter.default)
        ? parameter.default.join(",")
        : parameter.type === "boolean" ? Boolean(parameter.default) : String(parameter.default);
      return [[parameter.name, value]];
    }
    return [];
  }));
}

export function parameterConstraintText(
  parameter: CatalogParameter,
  options: { screenInitialMaximum?: boolean } = {}
): string {
  const parts: string[] = [];
  if (parameter.format === "decimal-string") parts.push("숫자 형식 문자열");
  if (options.screenInitialMaximum && parameter.maximum !== undefined) {
    parts.push(`화면 초기 ${parameter.maximum}`);
  }
  if (parameter.default !== undefined) {
    parts.push(`${options.screenInitialMaximum ? "API 기본" : "기본"} ${formatConstraintValue(parameter.default)}`);
  }
  if (parameter.minimum !== undefined) parts.push(`최소 ${parameter.minimum}`);
  if (parameter.maximum !== undefined) parts.push(`최대 ${parameter.maximum}`);
  if (parameter.max_items !== undefined) parts.push(`최대 ${parameter.max_items}개 항목`);
  if (parameter.range_max_seconds !== undefined) {
    parts.push(`기간 최대 ${parameter.range_max_seconds / 86400}일`);
  }
  if (parameter.timezone) parts.push(parameter.timezone === "Asia/Seoul" ? "KST" : "UTC");
  if (parameter.unit) parts.push(`단위 ${parameter.unit}`);
  if (parameter.dynamic_constraint_source) {
    parts.push(`동적 제한: ${DYNAMIC_SOURCE_LABELS[parameter.dynamic_constraint_source] ?? parameter.dynamic_constraint_source}`);
  } else if (parameter.format === "decimal-string" && parameter.default === undefined &&
    parameter.minimum === undefined && parameter.maximum === undefined) {
    parts.push("공식 정적 제한 없음");
  }
  if (parameter.format === "cursor") parts.push("UUID 커서(cursor)");
  return parts.join(" · ");
}

export function validateParameterValues(
  definitions: CatalogParameter[],
  values: Record<string, unknown>
): ParameterErrors {
  const errors: ParameterErrors = {};
  const byName = new Map(definitions.map((parameter) => [parameter.name, parameter]));
  for (const parameter of definitions) {
    const value = values[parameter.name];
    if (isEmpty(value)) {
      if (parameter.required) errors[parameter.name] = "필수 값을 입력해 주세요.";
      continue;
    }
    if (parameter.type === "array" && Array.isArray(value)) {
      if (value.some((item) => typeof item === "string" && !item.trim())) {
        errors[parameter.name] = "빈 항목을 제거해 주세요.";
        continue;
      }
      if (parameter.max_items !== undefined && value.length > parameter.max_items) {
        errors[parameter.name] = `최대 ${parameter.max_items}개까지 입력할 수 있습니다.`;
        continue;
      }
      if (parameter.unique_items && new Set(value).size !== value.length) {
        errors[parameter.name] = "중복 항목을 제거해 주세요.";
        continue;
      }
    }
    if (parameter.format === "csv" && typeof value === "string") {
      const items = value.split(",").map((item) => item.trim());
      if (items.some((item) => !item)) {
        errors[parameter.name] = "빈 항목을 제거해 주세요.";
        continue;
      }
      if (parameter.max_items !== undefined && items.length > parameter.max_items) {
        errors[parameter.name] = `최대 ${parameter.max_items}개까지 입력할 수 있습니다.`;
        continue;
      }
      if (parameter.unique_items && new Set(items).size !== items.length) {
        errors[parameter.name] = "중복 항목을 제거해 주세요.";
        continue;
      }
    }
    if (isNumericParameter(parameter)) {
      const numeric = Number(value);
      if (!Number.isFinite(numeric) || (isIntegerParameter(parameter) && !Number.isInteger(numeric))) {
        errors[parameter.name] = isIntegerParameter(parameter)
          ? "정수로 입력해 주세요."
          : "유효한 숫자로 입력해 주세요.";
        continue;
      }
      if (parameter.minimum !== undefined && numeric < parameter.minimum) {
        errors[parameter.name] = `최소 ${parameter.minimum} 이상으로 입력해 주세요.`;
        continue;
      }
      if (parameter.maximum !== undefined && numeric > parameter.maximum) {
        errors[parameter.name] = `최대 ${parameter.maximum} 이하로 입력해 주세요.`;
        continue;
      }
    }
    if (parameter.format === "date-time" && Number.isNaN(new Date(String(value)).getTime())) {
      errors[parameter.name] = "유효한 날짜와 시간을 선택해 주세요.";
    }
  }
  const checked = new Set<string>();
  for (const parameter of definitions) {
    const partnerName = parameter.range_with;
    if (!partnerName || parameter.range_max_seconds === undefined || checked.has(parameter.name)) continue;
    const partner = byName.get(partnerName);
    if (!partner) continue;
    checked.add(parameter.name);
    checked.add(partnerName);
    const first = values[parameter.name];
    const second = values[partnerName];
    if (isEmpty(first) || isEmpty(second)) continue;
    const firstDate = new Date(String(first));
    const secondDate = new Date(String(second));
    const [start, end] = parameter.name.startsWith("start")
      ? [firstDate, secondDate]
      : [secondDate, firstDate];
    const duration = (end.getTime() - start.getTime()) / 1000;
    if (duration < 0 || duration > parameter.range_max_seconds) {
      errors[parameter.name] = `날짜 범위는 시작 이후 ${parameter.range_max_seconds / 86400}일 이내여야 합니다.`;
    }
  }
  return errors;
}

function parameterDateTimeToIso(parameter: CatalogParameter, value: string): string {
  if (/[zZ]$|[+-]\d{2}:\d{2}$/.test(value)) return new Date(value).toISOString();
  if (parameter.timezone === "UTC") return new Date(`${value}Z`).toISOString();
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2}))?$/.exec(value);
  if (!match) return new Date(value).toISOString();
  const [, year, month, day, hour, minute, second = "0"] = match;
  return new Date(Date.UTC(
    Number(year), Number(month) - 1, Number(day), Number(hour) - 9,
    Number(minute), Number(second)
  )).toISOString();
}

function isNumericParameter(parameter: CatalogParameter): boolean {
  return parameter.type === "integer" || parameter.type === "number" ||
    parameter.format === "integer-string" || parameter.format === "decimal-string";
}

function isIntegerParameter(parameter: CatalogParameter): boolean {
  return parameter.type === "integer" || parameter.format === "integer-string";
}

function isEmpty(value: unknown): boolean {
  return value === undefined || value === null || value === "" ||
    (Array.isArray(value) && value.length === 0);
}

function formatConstraintValue(value: CatalogParameter["default"]): string {
  return Array.isArray(value) ? value.join(", ") : String(value);
}
