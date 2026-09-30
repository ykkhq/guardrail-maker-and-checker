import { Handle, Position, type NodeProps } from "@xyflow/react";
import { useCatalog } from "../catalog";
import type { GuardNode } from "../graph";
import { KIND_LABEL } from "../graph";

const OUTCOME_LABEL: Record<string, string> = {
  pass: "passed",
  mask: "masked",
  flag: "flagged",
  block: "blocked",
  error_open: "error (fail-open)",
  branch_true: "→ true",
  branch_false: "→ false",
  skipped: "not reached",
};

export function GuardNodeView({ id, data, selected }: NodeProps<GuardNode>) {
  const catalog = useCatalog();
  const nt = catalog[data.nodeType];
  const kind = nt?.kind ?? "custom";
  const errors = data.issues?.filter((i) => i.level === "error") ?? [];
  const warnings = data.issues?.filter((i) => i.level === "warning") ?? [];
  const hasInput = data.nodeType !== "prompt_in";
  const outputs = nt?.outputs ?? ["out"];
  const isEnd = data.nodeType === "response_out";

  return (
    <div
      className={[
        "gnode",
        `kind-${kind}`,
        selected ? "selected" : "",
        data.outcome ? `outcome-${data.outcome}` : "",
        errors.length ? "has-error" : "",
      ].join(" ")}
      title={[...errors, ...warnings].map((i) => i.message).join("\n") || nt?.description}
    >
      {hasInput && <Handle type="target" position={Position.Top} />}
      <div className="gnode-kind">
        <span>{KIND_LABEL[kind]}</span>
        {nt?.plugin && kind === "native" && <code>{nt.plugin}</code>}
      </div>
      <div className="gnode-title">{data.label || nt?.label || data.nodeType}</div>
      <div className="gnode-id">{id}</div>
      {(errors.length > 0 || warnings.length > 0) && (
        <div className="gnode-badges">
          {errors.length > 0 && <span className="badge badge-error">{errors.length} error{errors.length > 1 ? "s" : ""}</span>}
          {warnings.length > 0 && <span className="badge badge-warn">{warnings.length} warning{warnings.length > 1 ? "s" : ""}</span>}
        </div>
      )}
      {data.outcome && <div className="gnode-outcome">{OUTCOME_LABEL[data.outcome] ?? data.outcome}</div>}

      {!isEnd && outputs.length === 1 && <Handle type="source" position={Position.Bottom} />}
      {outputs.length === 2 &&
        outputs.map((h, i) => (
          <Handle
            key={h}
            type="source"
            id={h}
            position={Position.Bottom}
            className={`handle-${h}`}
            style={{ left: `${30 + i * 40}%` }}
          >
            <span className="handle-label">{h}</span>
          </Handle>
        ))}
    </div>
  );
}
