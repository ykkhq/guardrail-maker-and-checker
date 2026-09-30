import { useMemo, useState } from "react";
import type { NodeType } from "../types";
import { KIND_LABEL } from "../graph";

const CATEGORY_ORDER = ["PII", "Safety", "Sentiment", "Classification", "Content Safety", "Prompt shaping", "Control"];

export const DRAG_TYPE = "application/x-guardrail-node";

export function Palette({ types }: { types: NodeType[] }) {
  const [q, setQ] = useState("");
  const groups = useMemo(() => {
    const needle = q.trim().toLowerCase();
    const visible = types.filter(
      (t) =>
        t.kind !== "endpoint" &&
        (!needle || `${t.label} ${t.description} ${t.plugin ?? ""}`.toLowerCase().includes(needle)),
    );
    const by: Record<string, NodeType[]> = {};
    for (const t of visible) (by[t.category] ??= []).push(t);
    return Object.entries(by).sort(
      ([a], [b]) => (CATEGORY_ORDER.indexOf(a) + 99) % 99 - (CATEGORY_ORDER.indexOf(b) + 99) % 99,
    );
  }, [types, q]);

  return (
    <aside className="palette">
      <div className="panel-head">Guardrails</div>
      <input className="search" placeholder="Search…" value={q} onChange={(e) => setQ(e.target.value)} />
      <p className="hint">Drag onto the canvas, then connect.</p>
      {groups.map(([cat, items]) => (
        <section key={cat}>
          <h4>{cat}</h4>
          {items.map((t) => (
            <div
              key={t.type}
              className={`pitem kind-${t.kind}${t.available === false ? " unavailable" : ""}`}
              draggable={t.available !== false}
              onDragStart={(e) => {
                e.dataTransfer.setData(DRAG_TYPE, t.type);
                e.dataTransfer.effectAllowed = "move";
              }}
              title={t.available === false ? `Not installed in the engine (missing: ${t.missing?.join(", ")})` : t.description}
            >
              <div className="pitem-title">{t.label}</div>
              <div className="pitem-kind">
                {t.available === false ? "not installed in engine" : KIND_LABEL[t.kind] + (t.phases.includes("response") ? " · req/resp" : "")}
              </div>
            </div>
          ))}
        </section>
      ))}
      <div className="legend">
        <div><i className="sw kind-native" /> Kong plugin (runs in Kong)</div>
        <div><i className="sw kind-custom" /> Guardrail engine (via DataKit)</div>
        <div><i className="sw kind-control" /> Control (branch / block)</div>
      </div>
    </aside>
  );
}
