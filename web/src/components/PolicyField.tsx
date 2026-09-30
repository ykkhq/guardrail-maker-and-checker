import { useState } from "react";
import type { ErrorSchema, FieldProps } from "@rjsf/utils";

interface Category {
  id: string;
  name: string;
  description?: string;
  enabled?: boolean;
}

// snake_case id from a name; non-ASCII names (e.g. Japanese) fall back to custom_N.
function idFrom(name: string, taken: Set<string>): string {
  let base = name.toLowerCase().normalize("NFKD").replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "").slice(0, 32);
  if (!/^[a-z]/.test(base)) base = "custom";
  let id = base;
  for (let i = 2; taken.has(id); i++) id = `${base}_${i}`;
  return id;
}

function errorsOf(es: ErrorSchema | undefined): string[] {
  if (!es) return [];
  const out: string[] = [];
  const walk = (e: any, path: string[]) => {
    for (const [k, v] of Object.entries(e ?? {})) {
      if (k === "__errors") out.push(...(v as unknown as string[]).map((m) => (path.length ? `${path.join(".")}: ${m}` : m)));
      else walk(v, [...path, k]);
    }
  };
  walk(es, []);
  return out;
}

/** Llama Guard safety policy: ordered unsafe-content categories (schema "x-editor": "policy"). */
export function PolicyField(props: FieldProps) {
  const { formData, onChange, fieldPathId, schema, errorSchema, disabled, readonly } = props;
  const cats: Category[] = Array.isArray(formData) ? formData : [];
  const [open, setOpen] = useState<Record<number, boolean>>({});
  const commit = (next: Category[]) => onChange(next as any, fieldPathId.path);
  const update = (i: number, patch: Partial<Category>) => commit(cats.map((c, k) => (k === i ? { ...c, ...patch } : c)));
  // Fixed codes (mirrors guardrail_common.llamaguard.coded): standard categories
  // keep S1-S14, custom ones get S15+ in list order.
  const standard = ((schema.default as unknown as Category[]) ?? []).map((c) => c.id);
  let n = standard.length;
  const code = cats.map((c) => (standard.includes(c.id) ? `S${standard.indexOf(c.id) + 1}` : `S${++n}`));
  const custom = cats.filter((c) => !standard.includes(c.id) && c.enabled !== false).length;
  const ro = disabled || readonly;

  const add = () => {
    const name = "New category";
    const next = [...cats, { id: idFrom(name, new Set(cats.map((c) => c.id))), name, description: "", enabled: true }];
    setOpen({ ...open, [next.length - 1]: true });
    commit(next);
  };

  return (
    <div className="policy">
      <div className="json-head">
        <label className="control-label">{schema.title ?? "Safety policy"}</label>
        <div className="json-actions">
          <button type="button" className="btn ghost sm" disabled={ro}
                  onClick={() => commit(structuredClone(schema.default as unknown as Category[]))}>Reset to default</button>
        </div>
      </div>
      {schema.description && <p className="field-description">{schema.description}</p>}
      <ol className="cats">
        {cats.map((c, i) => (
          <li key={i} className={c.enabled === false ? "off" : ""}>
            <div className="cat-row">
              <input type="checkbox" checked={c.enabled !== false} disabled={ro}
                     title={c.enabled === false ? "Enable" : "Disable"}
                     onChange={(e) => update(i, { enabled: e.target.checked })} />
              <span className="cat-code">{code[i]}</span>
              <input className="cat-name" value={c.name} disabled={ro} aria-label="Category name"
                     onChange={(e) => update(i, { name: e.target.value })} />
              <button type="button" className={`btn ghost sm${c.description ? " has-desc" : ""}`}
                      title="Description" onClick={() => setOpen({ ...open, [i]: !open[i] })}>¶</button>
              <button type="button" className="btn ghost sm" disabled={ro || cats.length <= 1}
                      onClick={() => commit(cats.filter((_, k) => k !== i))} aria-label="Remove">✕</button>
            </div>
            {open[i] && (
              <div className="cat-detail">
                <label>
                  <span>id (used in condition rules)</span>
                  <input className="mono" value={c.id} disabled={ro}
                         onChange={(e) => update(i, { id: e.target.value })} />
                </label>
                <label>
                  <span>Description (what the category covers; recommended for custom categories)</span>
                  <textarea rows={3} value={c.description ?? ""} disabled={ro}
                            placeholder="e.g. Messages that ask to compare with or recommend a competitor's product."
                            onChange={(e) => update(i, { description: e.target.value })} />
                </label>
              </div>
            )}
          </li>
        ))}
      </ol>
      <button type="button" className="btn sm" disabled={ro} onClick={add}>+ Add category</button>
      {custom > 0 && (
        <p className="note">
          Custom categories need a Llama Guard model that follows the prompt (e.g. <code>llama-guard3:8b</code>).{" "}
          <code>llama-guard3:1b</code> only recognises the standard categories S1–S13.
        </p>
      )}
      {errorsOf(errorSchema).length > 0 && (
        <ul className="error-detail">{errorsOf(errorSchema).map((m, k) => <li key={k}>{m}</li>)}</ul>
      )}
    </div>
  );
}
