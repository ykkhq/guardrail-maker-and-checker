import type { FieldDescriptor, Rule } from "../types";

export interface Upstream {
  id: string;
  label: string;
}

interface Props {
  config: { match?: "all" | "any"; rules?: Rule[] };
  upstream: Upstream[];
  fields: Record<string, FieldDescriptor[]>;
  defaultRules: Record<string, Rule | null>;
  onChange: (config: Record<string, unknown>) => void;
}

const OP_LABEL: Record<string, string> = {
  eq: "is", ne: "is not", gte: "≥", gt: ">", lte: "≤", lt: "<", in: "is one of", contains: "contains",
};

// A value that fits the field type, used when the field changes.
function initialValue(f: FieldDescriptor, op: string): unknown {
  switch (f.type) {
    case "boolean":
      return true;
    case "enum":
      return op === "in" ? [f.values?.[0]?.value] : f.values?.[0]?.value;
    case "integer":
      return f.max ?? 1;
    case "number":
      return f.max === 1 ? 0.9 : 0;
    case "list":
      return f.values?.[0]?.value ?? "";
    default:
      return "";
  }
}

// Whether a value has the right shape for the field and operator (mirrors outputs.check_rule).
function fits(f: FieldDescriptor, op: string, v: unknown): boolean {
  const allowed = (f.values ?? []).map((x) => x.value);
  switch (f.type) {
    case "boolean":
      return typeof v === "boolean";
    case "number":
    case "integer":
      return typeof v === "number" && (f.min === undefined || v >= f.min) && (f.max === undefined || v <= f.max);
    case "enum":
      return op === "in"
        ? Array.isArray(v) && v.every((x) => allowed.includes(x as never))
        : allowed.includes(v as never);
    case "list":
      return !allowed.length || allowed.includes(v as never);
    default:
      return typeof v === "string";
  }
}

function describe(r: Rule, f: FieldDescriptor | undefined): string {
  const v = Array.isArray(r.value) ? r.value.join(", ") : String(r.value);
  return `${r.node}.${f?.label ?? r.field} ${OP_LABEL[r.op] ?? r.op} ${v}`;
}

export function ConditionEditor({ config, upstream, fields, defaultRules, onChange }: Props) {
  const rules = config.rules ?? [];
  const match = config.match ?? "all";
  const set = (next: Partial<typeof config>) => onChange({ ...config, match, rules, ...next });
  const setRule = (i: number, r: Rule) => set({ rules: rules.map((x, k) => (k === i ? r : x)) });

  const ruleFor = (nodeId: string): Rule =>
    defaultRules[nodeId] ?? { node: nodeId, field: "detected", op: "eq", value: true };

  const addRule = () => {
    if (!upstream.length) return;
    // Prefer the nearest node (last in upstream order), e.g. the Laya classifier right before.
    set({ rules: [...rules, ruleFor(upstream[upstream.length - 1].id)] });
  };

  return (
    <div className="cond">
      <div className="cond-match">
        <span>Go to <b className="t-true">true</b> when</span>
        <select value={match} onChange={(e) => set({ match: e.target.value as "all" | "any" })}>
          <option value="all">all rules match</option>
          <option value="any">any rule matches</option>
        </select>
      </div>

      {!upstream.length && (
        <p className="note">Connect a detector (for example the Laya Classifier) before this condition to build rules.</p>
      )}

      {rules.map((r, i) => {
        const nodeFields = fields[r.node] ?? [];
        const f = nodeFields.find((x) => x.field === r.field);
        const known = upstream.some((u) => u.id === r.node);
        return (
          <div key={i} className="rule">
            <div className="rule-head">
              <span className="muted">{i === 0 ? "If" : match === "all" ? "and" : "or"}</span>
              <button type="button" className="btn ghost sm" onClick={() => set({ rules: rules.filter((_, k) => k !== i) })}
                      aria-label="Remove rule">✕</button>
            </div>
            <label className="field">
              <span>Source</span>
              <select value={r.node} onChange={(e) => setRule(i, ruleFor(e.target.value))}>
                {!known && <option value={r.node}>{r.node} (not before this condition)</option>}
                {upstream.map((u) => <option key={u.id} value={u.id}>{u.label}</option>)}
              </select>
            </label>
            <label className="field">
              <span>Field</span>
              <select
                value={f ? r.field : ""}
                onChange={(e) => {
                  const nf = nodeFields.find((x) => x.field === e.target.value)!;
                  setRule(i, { ...r, field: nf.field, op: nf.ops[0], value: initialValue(nf, nf.ops[0]) });
                }}
              >
                {!f && <option value="">{r.field ? `${r.field} (unknown)` : "choose…"}</option>}
                {nodeFields.map((x) => <option key={x.field} value={x.field}>{x.label}</option>)}
              </select>
              {f?.help && <small className="muted">{f.help}</small>}
            </label>
            {f && (
              <div className="rule-row">
                <select value={r.op} onChange={(e) => {
                  const op = e.target.value;
                  // Keep the value if it still fits (e.g. "is" -> "is not"), otherwise reset it.
                  setRule(i, { ...r, op, value: fits(f, op, r.value) ? r.value : initialValue(f, op) });
                }}>
                  {!f.ops.includes(r.op) && <option value={r.op}>{r.op} (invalid)</option>}
                  {f.ops.map((op) => <option key={op} value={op}>{OP_LABEL[op] ?? op}</option>)}
                </select>
                <ValueInput field={f} op={r.op} value={r.value} onChange={(value) => setRule(i, { ...r, value })} />
              </div>
            )}
          </div>
        );
      })}

      <button type="button" className="btn sm" disabled={!upstream.length} onClick={addRule}>+ Add rule</button>

      {rules.length > 0 && (
        <p className="cond-summary">
          <b className="t-true">true</b> when{" "}
          {rules.map((r, i) => (
            <span key={i}>
              {i > 0 && <b> {match === "all" ? "and" : "or"} </b>}
              <code>{describe(r, (fields[r.node] ?? []).find((x) => x.field === r.field))}</code>
            </span>
          ))}
          , otherwise <b className="t-false">false</b>.
        </p>
      )}
    </div>
  );
}

function ValueInput({ field: f, op, value, onChange }: {
  field: FieldDescriptor; op: string; value: unknown; onChange: (v: unknown) => void;
}) {
  if (f.type === "boolean") {
    return (
      <select value={String(value)} onChange={(e) => onChange(e.target.value === "true")}>
        {typeof value !== "boolean" && <option value={String(value)}>{String(value)} (invalid)</option>}
        <option value="true">true</option>
        <option value="false">false</option>
      </select>
    );
  }
  if (f.type === "enum" && op === "in") {
    const sel = Array.isArray(value) ? value : [];
    return (
      <div className="checks">
        {f.values?.map((v) => (
          <label key={String(v.value)} title={v.label}>
            <input type="checkbox" checked={sel.includes(v.value)}
                   onChange={(e) => onChange(e.target.checked ? [...sel, v.value] : sel.filter((x) => x !== v.value))} />
            {String(v.value)}
          </label>
        ))}
      </div>
    );
  }
  if (f.values?.length && (f.type === "enum" || f.type === "integer" || f.type === "list")) {
    return (
      <select value={String(value)} onChange={(e) => {
        const v = f.values!.find((x) => String(x.value) === e.target.value)!;
        onChange(v.value);
      }}>
        {!f.values.some((v) => String(v.value) === String(value)) && <option value={String(value)}>{String(value)} (invalid)</option>}
        {f.values.map((v) => <option key={String(v.value)} value={String(v.value)}>{v.label}</option>)}
      </select>
    );
  }
  if (f.type === "number" || f.type === "integer") {
    return (
      <input type="number" value={typeof value === "number" ? value : ""} min={f.min} max={f.max}
             step={f.type === "integer" ? 1 : 0.01}
             onChange={(e) => onChange(e.target.value === "" ? 0 : Number(e.target.value))} />
    );
  }
  return <input value={String(value ?? "")} onChange={(e) => onChange(e.target.value)} />;
}
