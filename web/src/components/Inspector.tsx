import { useMemo } from "react";
import Form from "@rjsf/core";
import validator from "@rjsf/validator-ajv8";
import type { RJSFSchema, UiSchema } from "@rjsf/utils";
import type { GuardNode } from "../graph";
import { KIND_LABEL } from "../graph";
import type { Issue, JsonSchema, NodeType } from "../types";

interface Props {
  node: GuardNode;
  nodeType: NodeType;
  allNodes: GuardNode[];
  catalog: Record<string, NodeType>;
  issues: Issue[];
  onChange: (config: Record<string, unknown>, label: string | null) => void;
  onDelete: () => void;
}

// rjsf/ajv reject unknown keywords such as our "x-secret"; strip them and
// return the paths of secret fields so the UI can hint at vault references.
function prepareSchema(schema: JsonSchema): { schema: JsonSchema; secrets: string[] } {
  const secrets: string[] = [];
  const walk = (s: any, path: string[]): any => {
    if (Array.isArray(s)) return s.map((x) => walk(x, path));
    if (!s || typeof s !== "object") return s;
    const out: any = {};
    for (const [k, v] of Object.entries(s)) {
      if (k.startsWith("x-")) {
        if (k === "x-secret" && v) secrets.push(path.join("."));
        continue;
      }
      out[k] = k === "properties"
        ? Object.fromEntries(Object.entries(v as object).map(([pk, pv]) => [pk, titled(walk(pv, [...path, pk]), pk)]))
        : walk(v, path);
    }
    return out;
  };
  return { schema: walk(schema, []), secrets };
}

const ACRONYMS: Record<string, string> = { url: "URL", id: "ID", ms: "(ms)", llm: "LLM", aws: "AWS", api: "API" };

// "on_detect" -> "On detect", unless the schema already has a title.
function titled(schema: any, key: string) {
  if (!schema || typeof schema !== "object" || schema.title) return schema;
  const words = key.split("_").map((w) => ACRONYMS[w] ?? w);
  const title = words.join(" ");
  return { ...schema, title: title.charAt(0).toUpperCase() + title.slice(1) };
}

// Condition rule values are any JSON value; the form edits them as text.
const toText = (v: unknown) => (v === undefined ? undefined : typeof v === "string" ? v : JSON.stringify(v));
const fromText = (v: unknown) => {
  if (typeof v !== "string") return v;
  try {
    return JSON.parse(v);
  } catch {
    return v;
  }
};

// Drop top-level keys that the user never set and that equal the schema default.
function withoutDefaults(cfg: Record<string, any>, schema: JsonSchema, original: Record<string, unknown>) {
  const props = schema.properties ?? {};
  const out: Record<string, any> = {};
  for (const [k, v] of Object.entries(cfg)) {
    if (v === undefined) continue;
    const isDefault = "default" in (props[k] ?? {}) && stable(props[k].default) === stable(v);
    if (!(k in original) && isDefault) continue;
    out[k] = v;
  }
  return out;
}

function stable(v: unknown): string {
  return JSON.stringify(v, (_, x) =>
    x && typeof x === "object" && !Array.isArray(x)
      ? Object.fromEntries(Object.entries(x).sort(([a], [b]) => a.localeCompare(b)))
      : x,
  );
}

export function Inspector({ node, nodeType, allNodes, catalog, issues, onChange, onDelete }: Props) {
  const isCondition = node.data.nodeType === "condition";
  const detectorIds = useMemo(
    () => allNodes.filter((n) => catalog[n.data.nodeType]?.kind === "custom").map((n) => n.id),
    [allNodes, catalog],
  );

  const { schema, uiSchema } = useMemo(() => {
    const prepared = prepareSchema(nodeType.config_schema);
    const s = prepared.schema;
    const ui: UiSchema = { "ui:submitButtonOptions": { norender: true } };
    for (const p of prepared.secrets) {
      ui[p] = {
        "ui:placeholder": "{vault://env/NAME}",
        "ui:help": "Use a Kong vault reference as the whole value; the secret itself lives in the data plane.",
      };
    }
    for (const key of ["message", "block_message"]) {
      if (s.properties?.[key]) ui[key] = { "ui:widget": "textarea", "ui:options": { rows: 2 } };
    }
    if (isCondition && s.properties?.rules?.items?.properties) {
      s.properties.rules.title = "Rules";
      s.properties.rules.items.title = "Rule";
      const rp = s.properties.rules.items.properties;
      if (detectorIds.length) rp.node = { ...rp.node, enum: detectorIds };
      rp.field = { ...rp.field, default: "detected" };
      rp.value = { type: "string", title: "Value", description: 'JSON: true, 0.8, "text"' };
    }
    return { schema: s as RJSFSchema, uiSchema: ui };
  }, [nodeType, isCondition, detectorIds]);

  const formData = useMemo(() => {
    const cfg = { ...node.data.config } as Record<string, any>;
    if (isCondition && Array.isArray(cfg.rules)) {
      cfg.rules = cfg.rules.map((r: any) => ({ ...r, value: toText(r.value) }));
    }
    return cfg;
  }, [node.data.config, isCondition]);

  const handle = (data: Record<string, any>) => {
    const cfg = { ...data };
    if (isCondition && Array.isArray(cfg.rules)) {
      cfg.rules = cfg.rules.map((r: any) => ({ ...r, value: fromText(r.value) }));
    }
    // rjsf emits the schema defaults as soon as a form mounts. Ignore changes
    // that only add defaults, so selecting a node doesn't mark the pipeline dirty.
    if (stable(withoutDefaults(cfg, schema, node.data.config)) === stable(node.data.config)) return;
    onChange(withoutDefaults(cfg, schema, node.data.config), node.data.label ?? null);
  };

  const removable = nodeType.kind !== "endpoint";
  return (
    <div className="inspector">
      <div className="insp-head">
        <span className={`chip kind-${nodeType.kind}`}>{KIND_LABEL[nodeType.kind]}</span>
        {nodeType.plugin && <code className="plugin">{nodeType.plugin}</code>}
      </div>
      <h3>{nodeType.label}</h3>
      <p className="muted">{nodeType.description}</p>
      <label className="field">
        <span>Node id</span>
        <input value={node.id} readOnly />
      </label>
      <label className="field">
        <span>Label</span>
        <input
          value={node.data.label ?? ""}
          placeholder={nodeType.label}
          onChange={(e) => onChange(node.data.config, e.target.value || null)}
        />
      </label>
      {issues.length > 0 && (
        <ul className="issues">
          {issues.map((i, k) => (
            <li key={k} className={i.level}>{i.message}</li>
          ))}
        </ul>
      )}
      {Object.keys(schema.properties ?? {}).length > 0 ? (
        <Form
          key={node.id}
          schema={schema}
          uiSchema={uiSchema}
          formData={formData}
          validator={validator}
          liveValidate
          showErrorList={false}
          onChange={(e) => handle(e.formData)}
        />
      ) : (
        <p className="muted">No settings.</p>
      )}
      {removable && (
        <button className="btn danger block" onClick={onDelete}>Delete node</button>
      )}
    </div>
  );
}
