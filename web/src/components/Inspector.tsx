import { useMemo } from "react";
import Form from "@rjsf/core";
import validator from "@rjsf/validator-ajv8";
import type { RJSFSchema, UiSchema } from "@rjsf/utils";
import type { GuardNode } from "../graph";
import { KIND_LABEL } from "../graph";
import { JsonTextField } from "./JsonTextField";
import { PolicyField } from "./PolicyField";
import { PromptPreview } from "./PromptPreview";
import type { FieldDescriptor, Issue, JsonSchema, NodeType, Rule } from "../types";
import { ConditionEditor, type Upstream } from "./ConditionEditor";

interface Props {
  node: GuardNode;
  nodeType: NodeType;
  issues: Issue[];
  // Condition nodes: detectors that run before this node, and what they output.
  upstream: Upstream[];
  fields: Record<string, FieldDescriptor[]>;
  defaultRules: Record<string, Rule | null>;
  // request/response, from validation (Llama Guard phrases its check per phase).
  phase: "request" | "response";
  onChange: (config: Record<string, unknown>, label: string | null) => void;
  onDelete: () => void;
}

// rjsf/ajv reject unknown keywords such as our "x-secret"; strip them and
// return the paths of secret fields so the UI can hint at vault references.
function prepareSchema(schema: JsonSchema): {
  schema: JsonSchema; secrets: string[]; jsonFields: string[]; policyFields: string[]; textareas: string[];
} {
  const secrets: string[] = [];
  const jsonFields: string[] = [];
  const policyFields: string[] = [];
  const textareas: string[] = [];
  const walk = (s: any, path: string[]): any => {
    if (Array.isArray(s)) return s.map((x) => walk(x, path));
    if (!s || typeof s !== "object") return s;
    const out: any = {};
    for (const [k, v] of Object.entries(s)) {
      if (k.startsWith("x-")) {
        if (k === "x-secret" && v) secrets.push(path.join("."));
        if (k === "x-widget" && v === "textarea") textareas.push(path.join("."));
        continue;
      }
      out[k] = k === "properties"
        ? Object.fromEntries(Object.entries(v as object).map(([pk, pv]) => [
            pk,
            // JSON-edited fields keep their inner schema as-is: its keys are the JSON keys users type.
            (pv as any)?.["x-editor"] === "json" || (pv as any)?.["x-editor"] === "policy"
              ? ((((pv as any)["x-editor"] === "json" ? jsonFields : policyFields)).push([...path, pk].join(".")),
                 titled(stripX(pv), pk))
              : titled(walk(pv, [...path, pk]), pk),
          ]))
        : walk(v, path);
    }
    return out;
  };
  return { schema: walk(schema, []), secrets, jsonFields, policyFields, textareas };
}

function stripX(s: any): any {
  if (Array.isArray(s)) return s.map(stripX);
  if (!s || typeof s !== "object") return s;
  return Object.fromEntries(Object.entries(s).filter(([k]) => !k.startsWith("x-")).map(([k, v]) => [k, stripX(v)]));
}

const ACRONYMS: Record<string, string> = { url: "URL", id: "ID", ms: "(ms)", llm: "LLM", aws: "AWS", api: "API" };

// "on_detect" -> "On detect", unless the schema already has a title.
function titled(schema: any, key: string) {
  if (!schema || typeof schema !== "object" || schema.title) return schema;
  const words = key.split("_").map((w) => ACRONYMS[w] ?? w);
  const title = words.join(" ");
  return { ...schema, title: title.charAt(0).toUpperCase() + title.slice(1) };
}

const FIELDS = { json: JsonTextField, policy: PolicyField };

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

export function Inspector({ node, nodeType, issues, upstream, fields, defaultRules, phase, onChange, onDelete }: Props) {
  const isCondition = node.data.nodeType === "condition";

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
    for (const p of prepared.jsonFields) ui[p] = { "ui:field": "json" };
    for (const p of prepared.policyFields) ui[p] = { "ui:field": "policy" };
    for (const p of prepared.textareas) ui[p] = { "ui:widget": "textarea", "ui:options": { rows: 3 } };
    for (const key of ["message", "block_message"]) {
      if (s.properties?.[key]) ui[key] = { "ui:widget": "textarea", "ui:options": { rows: 2 } };
    }
    return { schema: s as RJSFSchema, uiSchema: ui };
  }, [nodeType]);

  const handle = (data: Record<string, any>) => {
    // rjsf emits the schema defaults as soon as a form mounts. Ignore changes
    // that only add defaults, so selecting a node doesn't mark the pipeline dirty.
    const cfg = withoutDefaults(data, schema, node.data.config);
    if (stable(cfg) === stable(node.data.config)) return;
    onChange(cfg, node.data.label ?? null);
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
      {isCondition ? (
        <ConditionEditor
          config={node.data.config as any}
          upstream={upstream}
          fields={fields}
          defaultRules={defaultRules}
          onChange={(cfg) => onChange(cfg, node.data.label ?? null)}
        />
      ) : Object.keys(schema.properties ?? {}).length > 0 ? (
        <Form
          key={node.id}
          schema={schema}
          uiSchema={uiSchema}
          formData={node.data.config}
          validator={validator}
          fields={FIELDS}
          liveValidate
          showErrorList={false}
          onChange={(e) => handle(e.formData)}
        />
      ) : (
        <p className="muted">No settings.</p>
      )}
      {node.data.nodeType === "llamaguard_safety" && <PromptPreview config={node.data.config} phase={phase} />}
      {removable && (
        <button className="btn danger block" onClick={onDelete}>Delete node</button>
      )}
    </div>
  );
}
