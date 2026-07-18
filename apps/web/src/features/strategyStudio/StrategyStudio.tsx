import { useRef, useState, type MutableRefObject } from "react";
import { useMutation } from "@tanstack/react-query";
import {
  createStrategy,
  publishStrategyVersion,
  validateStrategyGraph,
  type CreateStrategyCommand,
  type PublishStrategyVersionCommand,
  type StrategyDefinition,
  type StrategyGraph,
  type StrategyValidationResponse,
  type StrategyVersion
} from "../../api";
import "./strategy-studio.css";

const actorId = "operator:strategy-studio";

const initialGraph: StrategyGraph = {
  schema_version: "strategy-graph-v1",
  nodes: [
    {
      id: "market",
      type: "market_input",
      input_ports: [],
      output_ports: [{ name: "close", dataType: "decimal", timeframe: "1m" }],
      config: { market: "KRW-BTC" }
    },
    {
      id: "signal",
      type: "threshold_signal",
      input_ports: [{ name: "price", dataType: "decimal", timeframe: "1m" }],
      output_ports: [{ name: "enter_long", dataType: "boolean", timeframe: "1m" }],
      config: { operator: "gt", threshold: "100" }
    }
  ],
  edges: [{ from_node: "market", from_port: "close", to_node: "signal", to_port: "price" }],
  outputs: [{ node: "signal", port: "enter_long" }]
};

function commandFields(reason: string) {
  const requestId = globalThis.crypto.randomUUID();
  return {
    requestId,
    idempotencyKey: `strategy-studio:${requestId}`,
    actorId,
    requestedAt: new Date().toISOString(),
    reason
  };
}

function edgeText(edge: StrategyGraph["edges"][number]) {
  return `${edge.from_node}.${edge.from_port} → ${edge.to_node}.${edge.to_port}`;
}

function graphSnapshotKey(graph: StrategyGraph) {
  return JSON.stringify(graph);
}

function errorMessage(error: unknown) {
  return error instanceof Error ? error.message : "알 수 없는 오류";
}

function ensureStrategyCommand(commandRef: MutableRefObject<CreateStrategyCommand | null>) {
  commandRef.current ??= {
    ...commandFields("Strategy Studio 신규 전략"),
    ownerId: actorId,
    name: "KRW BTC momentum"
  };
  return commandRef.current;
}

function ensurePublishCommand(
  commandRef: MutableRefObject<Omit<PublishStrategyVersionCommand, "graph"> | null>
) {
  commandRef.current ??= commandFields("Strategy Studio 불변 version 게시");
  return commandRef.current;
}

export function StrategyStudio() {
  const [graph, setGraph] = useState<StrategyGraph>(initialGraph);
  const [outputName, setOutputName] = useState("enter_long");
  const [validation, setValidation] = useState<StrategyValidationResponse | null>(null);
  const [draftStrategy, setDraftStrategy] = useState<StrategyDefinition | null>(null);
  const [publishedVersion, setPublishedVersion] = useState<StrategyVersion | null>(null);
  const graphKeyRef = useRef(graphSnapshotKey(initialGraph));
  const strategyCommandRef = useRef<CreateStrategyCommand | null>(null);
  const publishCommandRef = useRef<Omit<PublishStrategyVersionCommand, "graph"> | null>(null);
  const validationMutation = useMutation({
    mutationFn: ({ graph: graphToValidate }: { graph: StrategyGraph; graphKey: string }) =>
      validateStrategyGraph(graphToValidate),
    onSuccess: (result, variables) => {
      if (variables.graphKey === graphKeyRef.current) {
        setValidation(result);
      }
    }
  });
  const publishMutation = useMutation({
    mutationFn: async () => {
      let strategy = draftStrategy;
      if (strategy === null) {
        strategy = await createStrategy(ensureStrategyCommand(strategyCommandRef));
        setDraftStrategy(strategy);
      }
      return publishStrategyVersion(strategy.strategyId, {
        ...ensurePublishCommand(publishCommandRef),
        graph
      });
    },
    onSuccess: setPublishedVersion
  });
  const invalidateDraftResult = () => {
    setValidation(null);
    setDraftStrategy(null);
    setPublishedVersion(null);
    strategyCommandRef.current = null;
    publishCommandRef.current = null;
    validationMutation.reset();
    publishMutation.reset();
  };
  const updateGraph = (updater: (current: StrategyGraph) => StrategyGraph) => {
    setGraph((current) => {
      const next = updater(current);
      graphKeyRef.current = graphSnapshotKey(next);
      return next;
    });
    invalidateDraftResult();
  };

  const applyOutput = () => {
    const name = outputName.trim();
    if (!name) return;
    updateGraph((current) => ({
      ...current,
      nodes: current.nodes.map((node) =>
        node.id === "signal"
          ? {
              ...node,
              output_ports: node.output_ports.map((port) =>
                port.name === current.outputs[0]?.port ? { ...port, name } : port
              )
            }
          : node
      ),
      edges: current.edges.map((edge) =>
        edge.from_node === "signal" ? { ...edge, from_port: name } : edge
      ),
      outputs: [{ node: "signal", port: name }]
    }));
  };
  const addCycle = () => {
    updateGraph((current) => ({
      ...current,
      edges: [
        ...current.edges,
        {
          from_node: "signal",
          from_port: current.outputs[0]?.port ?? "enter_long",
          to_node: "market",
          to_port: "close"
        }
      ]
    }));
  };
  const removeCycle = () => {
    updateGraph((current) => ({ ...current, edges: current.edges.slice(0, 1) }));
  };
  const errors = validation?.errors ?? [];
  const currentOutputPort = graph.outputs[0]?.port ?? "enter_long";
  const validateCurrentGraph = () => {
    const graphKey = graphSnapshotKey(graph);
    graphKeyRef.current = graphKey;
    validationMutation.mutate({ graph, graphKey });
  };

  return (
    <section className="strategy-studio" aria-label="Strategy Studio 작업 영역">
      <div className="strategy-studio-heading">
        <h2>Strategy Studio</h2>
        <p>색상 없이 코드와 위치로 검증 상태를 표시합니다.</p>
      </div>
      <div className="strategy-studio-grid">
        <section className="strategy-card" aria-label="전략 그래프">
          <div className="pointer-graph" role="img" aria-label="전략 그래프 포인터 뷰">
            <div><strong>market</strong><span>close</span></div>
            <span className="pointer-edge">market.close → signal.price</span>
            <div><strong>signal</strong><span>price → {currentOutputPort}</span></div>
            {graph.edges.slice(1).map((edge, index) => <span key={`${index}-${edgeText(edge)}`} className="pointer-edge">{edgeText(edge)}</span>)}
          </div>
          <table aria-label="전략 그래프 텍스트 대안">
            <caption>전략 그래프 텍스트 대안</caption>
            <thead><tr><th>node</th><th>type</th><th>port</th></tr></thead>
            <tbody>
              <tr><td>market</td><td>market_input</td><td>close</td></tr>
              <tr><td>signal</td><td>threshold_signal</td><td>{currentOutputPort}</td></tr>
              <tr><td>output</td><td>output</td><td>{currentOutputPort}</td></tr>
            </tbody>
          </table>
          <ul aria-label="전략 그래프 edge 목록">
            {graph.edges.map((edge, index) => (
              <li key={`${index}-${edgeText(edge)}`}>{edgeText(edge)}</li>
            ))}
          </ul>
        </section>

        <section className="strategy-card" role="group" aria-label="키보드 대체 편집기">
          <h3>키보드 대체 편집기</h3>
          <label htmlFor="strategy-output-name">출력 신호 이름</label>
          <input id="strategy-output-name" value={outputName} onChange={(event) => setOutputName(event.target.value)} />
          <button type="button" onClick={applyOutput}>출력 신호 적용</button>
          <p>출력 {currentOutputPort}</p>
          <div className="strategy-actions">
            <button type="button" onClick={addCycle}>순환 오류 edge 추가</button>
            <button type="button" onClick={removeCycle}>순환 오류 edge 제거</button>
          </div>
          <button type="button" onClick={validateCurrentGraph} disabled={validationMutation.isPending}>
            서버 검증
          </button>
          {validation?.valid ? <p role="status" aria-label="전략 그래프 검증 상태">검증 통과 · {validation.graphHash}</p> : null}
          {validationMutation.isError ? (
            <div role="alert" aria-label="전략 그래프 검증 요청 오류">
              {errorMessage(validationMutation.error)}
            </div>
          ) : null}
          {errors.length > 0 ? (
            <div role="alert" aria-label="전략 그래프 검증 오류">
              {errors.map((error, index) => (
                <p key={`${error.code}-${index}`}>{error.code} · node {error.nodeId ?? "-"} · edge {error.edgeIndex ?? "-"} · {error.message}</p>
              ))}
            </div>
          ) : null}
          <button type="button" onClick={() => publishMutation.mutate()} disabled={!validation?.valid || publishMutation.isPending || publishedVersion !== null}>
            불변 version 게시
          </button>
          {publishMutation.isError ? (
            <div role="alert" aria-label="전략 게시 오류">
              {errorMessage(publishMutation.error)}
            </div>
          ) : null}
        </section>
      </div>
      {publishedVersion ? (
        <section className="strategy-card published-version" aria-label="게시된 불변 전략 version">
          <h3>Version #{publishedVersion.version}</h3>
          <p>published · 불변 version</p>
          <code>{publishedVersion.graphHash}</code>
        </section>
      ) : null}
    </section>
  );
}
