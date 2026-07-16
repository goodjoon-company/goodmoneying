import type { ReactNode } from "react";

import type { CatalogParameter, ParameterValue } from "./types";
import {
  currentParameterInputValue,
  formatParameterInputValue,
  parameterConstraintText
} from "./parameterInput";
import { parameterDisplayName } from "./parameterPresentation";

export function CatalogParameterField({
  parameter,
  value,
  idPrefix,
  endpointId = "",
  label,
  inputLabel,
  screenInitialMaximum = false,
  error,
  suggestions,
  onChange
}: {
  parameter: CatalogParameter;
  value: ParameterValue | undefined;
  idPrefix: string;
  endpointId?: string;
  label?: string;
  inputLabel?: string;
  screenInitialMaximum?: boolean;
  error?: string;
  suggestions?: string[];
  onChange: (value: string | boolean | undefined) => void;
}) {
  const safeName = parameter.name.replaceAll(/[^a-zA-Z0-9_-]/g, "-");
  const inputId = `${idPrefix}-${safeName}`;
  const hintId = `${inputId}-hint`;
  const errorId = `${inputId}-error`;
  const displayLabel = label ?? parameterDisplayName(endpointId, parameter.name);
  const accessibleLabel = inputLabel ?? displayLabel;
  const constraint = parameterConstraintText(parameter, { screenInitialMaximum });
  const describedBy = [constraint ? hintId : "", error ? errorId : ""].filter(Boolean).join(" ") || undefined;
  const common = {
    id: inputId,
    required: parameter.required,
    "aria-label": accessibleLabel,
    "aria-describedby": describedBy,
    "aria-invalid": error ? true : undefined
  } as const;
  const formatted = formatParameterInputValue(parameter, value);
  let control: ReactNode;
  if (parameter.type === "boolean") {
    control = <input {...common} type="checkbox" checked={value === true} onChange={(event) => onChange(event.target.checked)} />;
  } else if (parameter.enum) {
    control = <select {...common} value={formatted} onChange={(event) => onChange(event.target.value || undefined)}>
      {!parameter.required ? <option value="">지정 안 함</option> : null}
      {parameter.enum.map((item) => <option key={String(item)} value={String(item)}>{String(item)}</option>)}
    </select>;
  } else if (parameter.type === "array") {
    control = <textarea {...common} rows={3} placeholder="쉼표로 여러 값을 구분" value={formatted}
      onChange={(event) => onChange(event.target.value || undefined)} />;
  } else {
    const numeric = parameter.type === "integer" || parameter.type === "number" ||
      parameter.format === "integer-string" || parameter.format === "decimal-string";
    const type = parameter.format === "date-time" ? "datetime-local"
      : parameter.format === "time" ? "time" : numeric ? "number" : "text";
    control = <input {...common} type={type} min={parameter.minimum} max={parameter.maximum}
      step={parameter.step ?? (parameter.format === "decimal-string" ? "any" : undefined)}
      list={suggestions?.length ? `${inputId}-suggestions` : undefined}
      value={formatted} onChange={(event) => onChange(event.target.value || undefined)} />;
  }
  const quickValues = [
    ["최소값", parameter.minimum],
    ["기본값", parameter.default],
    ["최대값", parameter.maximum]
  ] as const;
  return <div className={`catalog-parameter-field${parameter.type === "boolean" ? " boolean-inline" : ""}${error ? " has-error" : ""}`} data-parameter={parameter.name}>
    <label htmlFor={inputId}>
      <span>{displayLabel} <em>{parameter.required ? "필수" : "선택"}</em></span>
      {control}
    </label>
    {suggestions?.length ? <datalist id={`${inputId}-suggestions`}>
      {suggestions.map((suggestion) => <option key={suggestion} value={suggestion} />)}
    </datalist> : null}
    {constraint ? <small id={hintId} className="parameter-constraint">{constraint}</small> : null}
    <div className="parameter-actions">
      {parameter.format === "date-time" || parameter.format === "time" ? <button type="button"
        aria-label={`${accessibleLabel} 현재 시각 입력`} onClick={() => onChange(currentParameterInputValue(parameter))}>
        현재 시각(Now)
      </button> : null}
      {quickValues.map(([quickLabel, quickValue]) => quickValue !== undefined ? <button type="button"
        key={quickLabel} aria-label={`${accessibleLabel} ${quickLabel} 입력`}
        onClick={() => onChange(Array.isArray(quickValue) ? quickValue.join(",") : String(quickValue))}>
        {quickLabel.replace("값", "")}
      </button> : null)}
      {!parameter.required && parameter.type !== "boolean" && formatted ? <button type="button" aria-label={`${accessibleLabel} 입력 지우기`} onClick={() => onChange(undefined)}>지우기</button> : null}
    </div>
    {error ? <small id={errorId} className="parameter-error">{error}</small> : null}
  </div>;
}
