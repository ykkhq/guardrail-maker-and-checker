import { useState } from "react";
import type { PlaygroundResult, PlaygroundSample, SegmentResponse, TraceStep } from "../types";

interface Props {
  samples: PlaygroundSample[];
  deployed: boolean;
  dirty: boolean;
  running: boolean;
  result: PlaygroundResult | null;
  onRun: (mode: "live" | "dry", text: string) => void;
  onClear: () => void;
}

function summary(step: TraceStep): string {
  const r = step.result;
  if (step.type === "condition") return "";
  const parts: string[] = [];
  if (r.error) parts.push(r.error);
  if (r.label) parts.push(String(r.label));
  if (typeof r.score === "number") parts.push(`score ${r.score}`);
  const flags = Object.entries(r.flags ?? {}).filter(([, v]) => v && (!Array.isArray(v) || v.length));
  for (const [k, v] of flags) parts.push(Array.isArray(v) ? `${k}: ${v.join(", ")}` : v === true ? k : `${k}: ${v}`);
  if (r.entities?.length) parts.push([...new Set(r.entities.map((e: any) => e.type))].join(", "));
  return parts.join(" · ");
}

function lastUser(res: SegmentResponse): string | null {
  const msgs = res.body?.messages ?? [];
  const m = [...msgs].reverse().find((x) => x.role === "user");
  return m ? String(m.content) : null;
}

function liveText(body: any): string {
  if (body?.choices?.[0]?.message?.content) return body.choices[0].message.content;
  if (body?.error?.message) return body.error.message;
  return typeof body === "string" ? body : JSON.stringify(body, null, 2);
}

export function Playground({ samples, deployed, dirty, running, result, onRun, onClear }: Props) {
  const [text, setText] = useState(samples[0]?.text ?? "");
  const trace = result && "trace" in result.trace ? (result.trace as SegmentResponse) : null;
  const err = result && "error" in result.trace ? (result.trace as { error: string }).error : null;

  return (
    <div className="playground">
      {samples.length > 0 && (
        <div className="samples">
          {samples.map((s) => (
            <button key={s.label} className="btn ghost sm" onClick={() => setText(s.text)}>{s.label}</button>
          ))}
        </div>
      )}
      <textarea rows={4} value={text} onChange={(e) => setText(e.target.value)} placeholder="User message…" />
      <div className="row">
        <button className="btn" disabled={running || !text.trim()} onClick={() => onRun("dry", text)}
                title="Runs the engine nodes of the canvas as it is now (no Kong, no LLM)">
          Dry run
        </button>
        <button className="btn primary" disabled={running || !text.trim() || !deployed} onClick={() => onRun("live", text)}
                title={deployed ? "Send through Kong → guardrails → LLM" : "Deploy first"}>
          Send via Kong
        </button>
        {result && <button className="btn ghost" onClick={onClear}>Clear</button>}
      </div>
      {dirty && deployed && <p className="note">Unsaved changes: live requests use the last deployed version.</p>}
      {running && <p className="muted">Running…</p>}

      {result?.live && (
        <div className={`live ${result.live.status >= 400 || result.live.status === 0 ? "bad" : "good"}`}>
          <div className="live-head">
            <strong>Kong · HTTP {result.live.status || "—"}</strong>
            {result.live.latency_ms !== undefined && <span>{result.live.latency_ms} ms</span>}
          </div>
          <pre>{liveText(result.live.body)}</pre>
          {result.live.kong_request_id && <div className="muted mono">request id {result.live.kong_request_id}</div>}
        </div>
      )}

      {err && <p className="error">Engine: {err}</p>}
      {trace && (
        <div className="trace">
          <div className="trace-head">
            <strong>Engine trace</strong>
            <span className={`pill ${trace.decision === "block" ? "bad" : "good"}`}>
              {trace.decision === "block" ? `blocked ${trace.status} by ${trace.blocked_by}` : "allowed"}
            </span>
            <span className="muted">{trace.latency_ms} ms</span>
          </div>
          {trace.trace.length === 0 && <p className="muted">No engine nodes on this path.</p>}
          <ol>
            {trace.trace.map((s, i) => (
              <li key={i} className={`step outcome-${s.outcome}`}>
                <div className="step-head">
                  <span className="dot" />
                  <span className="mono">{s.node}</span>
                  <span className="step-outcome">{s.outcome.replace("_", " ")}</span>
                  <span className="muted">{s.latency_ms} ms</span>
                </div>
                {summary(s) && <div className="step-detail">{summary(s)}</div>}
              </li>
            ))}
          </ol>
          {trace.decision === "allow" && lastUser(trace) !== null && (
            <>
              <div className="muted">Prompt after guardrails (native Kong plugins not included):</div>
              <pre>{lastUser(trace)}</pre>
            </>
          )}
          {trace.decision === "block" && trace.message && <pre>{trace.message}</pre>}
        </div>
      )}
    </div>
  );
}
