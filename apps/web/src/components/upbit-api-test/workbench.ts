import type {
  CatalogEndpoint,
  CatalogParameter,
  ParameterValue,
  RequestParameters,
  WorkbenchContext
} from "./types";

export const quotationGroups = [
  { id: "pair", label: "페어" },
  { id: "candle", label: "캔들" },
  { id: "trade", label: "체결" },
  { id: "ticker", label: "현재가" },
  { id: "orderbook", label: "호가" }
] as const;

export type QuotationGroupId = (typeof quotationGroups)[number]["id"];

export function selectQuotationEndpoints(endpoints: CatalogEndpoint[]): CatalogEndpoint[] {
  const groupOrder = new Map(quotationGroups.map((group, index) => [group.id, index]));
  return endpoints
    .filter((endpoint) => endpoint.category === "quotation")
    .sort((left, right) =>
      (groupOrder.get(left.functional_group as QuotationGroupId) ?? 99) -
      (groupOrder.get(right.functional_group as QuotationGroupId) ?? 99)
    );
}

export function buildInitialParameters(
  endpoint: CatalogEndpoint,
  context: WorkbenchContext
): RequestParameters {
  return Object.fromEntries(endpoint.parameters.flatMap((parameter) => {
    const common = commonParameterValue(parameter.name, context);
    if (common !== undefined) return [[parameter.name, common]];
    if (parameter.enum?.length) return [[parameter.name, parameter.enum[0]]];
    if (parameter.name === "count") return [[parameter.name, parameter.maximum ?? 200]];
    if (parameter.type === "boolean") return [[parameter.name, false]];
    return [];
  }));
}

export function coerceParameterValue(
  parameter: CatalogParameter,
  value: string | boolean
): ParameterValue {
  if (parameter.type === "boolean") return value === true || value === "true";
  if (parameter.format === "date-time") return new Date(String(value)).toISOString();
  if (parameter.type === "integer" || parameter.type === "number") return Number(value);
  if (parameter.type === "array") return String(value).split(",").map((item) => item.trim()).filter(Boolean);
  return String(value);
}

export function formatParameterValue(
  parameter: CatalogParameter,
  value: ParameterValue | undefined
): string {
  if (value === undefined) return "";
  if (parameter.format !== "date-time") return Array.isArray(value) ? value.join(",") : String(value);
  const date = new Date(String(value));
  if (Number.isNaN(date.getTime())) return String(value);
  const local = new Date(date.getTime() - date.getTimezoneOffset() * 60_000);
  return local.toISOString().slice(0, 16);
}

export function serializeParameters(
  endpoint: CatalogEndpoint,
  values: Record<string, ParameterValue | undefined>
): RequestParameters {
  const result: RequestParameters = {};
  for (const parameter of endpoint.parameters) {
    const value = values[parameter.name];
    if (value === undefined || value === "" || (Array.isArray(value) && value.length === 0)) continue;
    result[parameter.name] = value;
  }
  return result;
}

function commonParameterValue(name: string, context: WorkbenchContext): ParameterValue | undefined {
  if (name === "market" || name === "markets") return context.market;
  if (name === "quote_currencies") return context.quote;
  if (name === "base_currencies") return context.base;
  return undefined;
}
