import { useEffect, useState } from "react";
import type { ErrorSchema, FieldProps } from "@rjsf/utils";

// Templates for "insert question" buttons (Laya question types).
const TEMPLATES: Record<string, { name: string; q: Record<string, unknown> }> = {
  noul: {
    name: "new_yes_no",
    q: { type: "noul", instructions: "Does `message` …?", threshold: 0.75 },
  },
  choice: {
    name: "new_choice",
    q: { type: "choice", instructions: "Which … does `message` …?", criteria: { option_a: "…", option_b: "…", other: "none of the other options fits" } },
  },
  score: {
    name: "new_score",
    q: { type: "score", instructions: "How … is `message`?", criteria: ["low", "medium", "high"], threshold: 2 },
  },
};

const pretty = (v: unknown) => JSON.stringify(v ?? {}, null, 2);

// Flatten rjsf's nested errorSchema into "path: message" lines.
function flatten(es: ErrorSchema | undefined, path: string[] = []): string[] {
  if (!es) return [];
  const out: string[] = [];
  for (const [k, v] of Object.entries(es)) {
    if (k === "__errors") out.push(...(v as unknown as string[]).map((m) => (path.length ? `${path.join(".")}: ${m}` : m)));
    else out.push(...flatten(v as ErrorSchema, [...path, k]));
  }
  return out;
}

/** Edits an object-valued field as JSON text (schema property with "x-editor": "json"). */
export function JsonTextField(props: FieldProps) {
  const { formData, onChange, fieldPathId, schema, errorSchema, name, disabled, readonly } = props;
  const [text, setText] = useState(() => pretty(formData));
  const [parseError, setParseError] = useState<string | null>(null);

  // Follow external changes (reset, switching nodes) unless the text already means the same.
  useEffect(() => {
    try {
      if (JSON.stringify(JSON.parse(text)) === JSON.stringify(formData ?? {})) return;
    } catch {
      /* the text is mid-edit and invalid: replace it */
    }
    setText(pretty(formData));
    setParseError(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [formData]);

  const commit = (value: unknown) => onChange(value as any, fieldPathId.path);

  const edit = (t: string) => {
    setText(t);
    try {
      const v = JSON.parse(t);
      if (!v || typeof v !== "object" || Array.isArray(v)) throw new Error("must be a JSON object");
      setParseError(null);
      commit(v);
    } catch (e: any) {
      setParseError(e.message);
    }
  };

  const insert = (kind: keyof typeof TEMPLATES) => {
    const cur = (formData ?? {}) as Record<string, unknown>;
    let n = TEMPLATES[kind].name;
    for (let i = 2; n in cur; i++) n = `${TEMPLATES[kind].name}_${i}`;
    const next = { ...cur, [n]: TEMPLATES[kind].q };
    setText(pretty(next));
    setParseError(null);
    commit(next);
  };

  // ajv reports a failed if/then branch as an extra, unhelpful line next to the real error.
  const schemaErrors = flatten(errorSchema).filter((m) => !/must match "(then|else|if)" schema/.test(m));
  const errors = [...(parseError ? [`Invalid JSON: ${parseError}`] : []), ...schemaErrors];
  const lines = text.split("\n").length;

  return (
    <div className="json-field">
      <div className="json-head">
        <label className="control-label" htmlFor={fieldPathId.$id}>{schema.title ?? name}</label>
        <div className="json-actions">
          <button type="button" className="btn ghost sm" disabled={disabled || readonly || !!parseError}
                  onClick={() => setText(pretty(formData))}>Format</button>
          {schema.default !== undefined && (
            <button type="button" className="btn ghost sm" disabled={disabled || readonly}
                    onClick={() => { setText(pretty(schema.default)); setParseError(null); commit(structuredClone(schema.default)); }}>
              Reset to presets
            </button>
          )}
        </div>
      </div>
      {schema.description && <p className="field-description">{schema.description}</p>}
      <div className="json-insert">
        <span className="muted">Add question:</span>
        <button type="button" className="btn ghost sm" onClick={() => insert("noul")}>+ yes/no</button>
        <button type="button" className="btn ghost sm" onClick={() => insert("choice")}>+ choice</button>
        <button type="button" className="btn ghost sm" onClick={() => insert("score")}>+ score</button>
      </div>
      <textarea
        id={fieldPathId.$id}
        className={`json-text${errors.length ? " invalid" : ""}`}
        spellCheck={false}
        rows={Math.min(Math.max(lines, 8), 28)}
        value={text}
        disabled={disabled}
        readOnly={readonly}
        onChange={(e) => edit(e.target.value)}
        onKeyDown={(e) => {
          // Tab inserts two spaces instead of leaving the editor.
          if (e.key !== "Tab") return;
          e.preventDefault();
          const el = e.currentTarget;
          const { selectionStart: s, selectionEnd: t } = el;
          edit(text.slice(0, s) + "  " + text.slice(t));
          requestAnimationFrame(() => el.setSelectionRange(s + 2, s + 2));
        }}
      />
      {errors.length > 0 && (
        <ul className="error-detail">
          {errors.map((m, i) => <li key={i}>{m}</li>)}
        </ul>
      )}
    </div>
  );
}
