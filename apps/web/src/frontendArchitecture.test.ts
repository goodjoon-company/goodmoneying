import { describe, expect, test } from "vitest";

import appSource from "./App.tsx?raw";
const componentModules = import.meta.glob("./components/*.tsx", {
  eager: true,
  query: "?raw",
  import: "default"
});
const stylesheetModules = import.meta.glob(["./styles.css", "./styles/*.css"], {
  eager: true,
  query: "?url",
  import: "default"
});

describe("프론트엔드 Module 구조", () => {
  test("App shell은 Query Provider와 운영 콘솔 조립만 담당한다", () => {
    expect(appSource).toContain("OperationsConsole");
    expect(appSource).not.toContain("function Dashboard");
    expect(appSource).not.toContain("function Targets");
    expect(appSource).not.toContain("function Markets");
    expect(appSource).not.toContain("function DetailModal");
    expect(appSource.split("\n").length).toBeLessThanOrEqual(40);
  });

  test("운영 화면은 화면 단위 Module로 분리된다", () => {
    [
      "components/OperationsConsole.tsx",
      "components/Dashboard.tsx",
      "components/Targets.tsx",
      "components/Markets.tsx",
      "components/Detail.tsx",
      "components/ScalabilityReadiness.tsx",
      "components/common.tsx"
    ].forEach((path) => {
      expect(`./${path}` in componentModules, path).toBe(true);
    });
  });

  test("CSS는 entrypoint와 역할별 stylesheet로 나뉜다", () => {
    [
      "./styles.css",
      "./styles/base.css",
      "./styles/shell.css",
      "./styles/common.css",
      "./styles/data-tables.css",
      "./styles/modals.css",
      "./styles/shell-fidelity.css",
      "./styles/dashboard.css",
      "./styles/collection-table.css",
      "./styles/responsive.css"
    ].forEach((path) => {
      expect(path in stylesheetModules, path).toBe(true);
    });
  });
});
