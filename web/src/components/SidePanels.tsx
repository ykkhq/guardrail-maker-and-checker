import type { Issue } from "../types";

export function IssuesPanel({ issues, onSelect }: { issues: Issue[]; onSelect: (id: string) => void }) {
  if (!issues.length) return <p className="muted pad">No issues. The pipeline compiles.</p>;
  return (
    <ul className="issues pad">
      {issues.map((i, k) => (
        <li key={k} className={i.level}>
          {i.node ? (
            <button className="linklike mono" onClick={() => onSelect(i.node!)}>{i.node}</button>
          ) : null}{" "}
          {i.message}
        </li>
      ))}
    </ul>
  );
}

export function ConfigPanel({ config }: { config: { result?: unknown; stages?: any[]; warnings?: string[] } | null }) {
  if (!config) return <p className="muted pad">Fix the errors to see the compiled config.</p>;
  return (
    <div className="pad config">
      <h4>Kong execution order</h4>
      <ol className="stages">
        {config.stages?.map((s, i) => (
          <li key={i}>
            <span className="mono">{s.plugin}</span> <span className="muted">({s.phase})</span>
            {s.nodes && <div className="muted">engine: {s.nodes.join(" → ")}</div>}
          </li>
        ))}
        <li><span className="mono">ai-proxy-advanced</span> <span className="muted">(LLM)</span></li>
      </ol>
      {config.warnings?.map((w, i) => <p key={i} className="note">{w}</p>)}
      <h4>decK state</h4>
      <pre className="code">{JSON.stringify(config.result, null, 2)}</pre>
    </div>
  );
}
