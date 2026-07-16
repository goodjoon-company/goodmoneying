import type {
  CatalogEndpoint,
  CatalogParameter,
  ParameterValue,
  RequestParameters,
  WorkbenchContext
} from "./types";
import {
  coerceParameterInputValue,
  formatParameterInputValue
} from "./parameterInput";

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
    if (endpoint.safety === "read" && parameter.name === "count" && parameter.maximum !== undefined) {
      return [[parameter.name, parameter.maximum]];
    }
    if (parameter.default !== undefined) return [[parameter.name, parameter.default]];
    if (parameter.enum?.length) return [[parameter.name, parameter.enum[0]]];
    if (parameter.type === "boolean") return [[parameter.name, false]];
    return [];
  }));
}

export function coerceParameterValue(
  parameter: CatalogParameter,
  value: string | boolean
): ParameterValue {
  return coerceParameterInputValue(parameter, value);
}

export function formatParameterValue(
  parameter: CatalogParameter,
  value: ParameterValue | undefined
): string {
  return formatParameterInputValue(parameter, value);
}

export function serializeParameters(
  endpoint: CatalogEndpoint,
  values: Record<string, ParameterValue | undefined>,
  context: WorkbenchContext
): RequestParameters {
  const result: RequestParameters = {};
  for (const parameter of endpoint.parameters) {
    const value = commonParameterValue(parameter.name, context) ?? values[parameter.name];
    if (value === undefined || value === "" || (Array.isArray(value) && value.length === 0)) continue;
    result[parameter.name] = value;
  }
  return result;
}

export function isCommonParameter(name: string): boolean {
  return name === "market" || name === "markets" ||
    name === "quote_currencies" || name === "base_currencies";
}

function commonParameterValue(name: string, context: WorkbenchContext): ParameterValue | undefined {
  if (name === "market" || name === "markets") return context.market;
  if (name === "quote_currencies") return context.quote;
  if (name === "base_currencies") return context.base;
  return undefined;
}
