// Conversion between the saved pipeline graph and React Flow state.
import type { Edge, Node } from "@xyflow/react";
import type { GraphEdge, GraphNode, NodeType, Outcome, PipelineGraph } from "./types";

export interface GuardData extends Record<string, unknown> {
  nodeType: string;
  config: Record<string, unknown>;
  label?: string | null;
  // Set by the app for rendering; not saved.
  issues?: { level: "error" | "warning"; message: string }[];
  outcome?: Outcome | "skipped" | null;
}

export type GuardNode = Node<GuardData, "guard">;

export function toFlow(g: PipelineGraph): { nodes: GuardNode[]; edges: Edge[] } {
  const auto = layout(g);
  const nodes = g.nodes.map((n) => ({
    id: n.id,
    type: "guard" as const,
    position: n.position ?? auto[n.id],
    data: { nodeType: n.type, config: n.config ?? {}, label: n.label },
  }));
  const edges = g.edges.map((e) => toFlowEdge(e));
  return { nodes, edges };
}

export function toFlowEdge(e: GraphEdge): Edge {
  const handle = e.sourceHandle ?? null;
  return {
    id: `${e.source}${handle ? ":" + handle : ""}->${e.target}`,
    source: e.source,
    target: e.target,
    sourceHandle: handle,
    // true/false edges are colored; the handle itself carries the label.
    className: handle ? `edge-${handle}` : undefined,
  };
}

export function fromFlow(slug: string, name: string, nodes: GuardNode[], edges: Edge[]): PipelineGraph {
  return {
    slug,
    name,
    nodes: nodes.map((n): GraphNode => ({
      id: n.id,
      type: n.data.nodeType,
      config: n.data.config,
      label: n.data.label ?? null,
      position: { x: Math.round(n.position.x), y: Math.round(n.position.y) },
    })),
    edges: edges.map((e): GraphEdge => ({
      source: e.source,
      target: e.target,
      sourceHandle: e.sourceHandle ?? null,
    })),
  };
}

export const COL_W = 230;
export const ROW_H = 120;

// Top-to-bottom layered layout for graphs saved without positions (e.g.
// samples): rank = longest path from a root, siblings spread horizontally.
export function layout(g: PipelineGraph): Record<string, { x: number; y: number }> {
  const rank: Record<string, number> = {};
  const incoming: Record<string, number> = {};
  for (const n of g.nodes) incoming[n.id] = 0;
  for (const e of g.edges) incoming[e.target] = (incoming[e.target] ?? 0) + 1;
  const queue = g.nodes.filter((n) => !incoming[n.id]).map((n) => n.id);
  for (const id of queue) rank[id] = 0;
  while (queue.length) {
    const id = queue.shift()!;
    for (const e of g.edges.filter((x) => x.source === id)) {
      rank[e.target] = Math.max(rank[e.target] ?? 0, rank[id] + 1);
      if (--incoming[e.target] === 0) queue.push(e.target);
    }
  }
  const rows: Record<number, string[]> = {};
  for (const n of g.nodes) (rows[rank[n.id] ?? 0] ??= []).push(n.id);
  const pos: Record<string, { x: number; y: number }> = {};
  for (const [r, ids] of Object.entries(rows)) {
    ids.forEach((id, i) => {
      pos[id] = { x: (i - (ids.length - 1) / 2) * COL_W, y: Number(r) * ROW_H };
    });
  }
  return pos;
}

export function uniqueId(base: string, taken: Set<string>): string {
  const stem = base.replace(/^ai_/, "").replace(/[^a-z0-9_]/g, "_");
  for (let i = 1; ; i++) {
    const id = i === 1 ? stem : `${stem}_${i}`;
    if (!taken.has(id)) return id;
  }
}

export function newPipeline(slug: string, name: string): PipelineGraph {
  return {
    slug,
    name,
    nodes: [
      { id: "in", type: "prompt_in", config: {}, position: { x: 0, y: 0 } },
      {
        id: "llm", type: "llm", position: { x: 0, y: 360 },
        config: { provider: "openai", model: "gpt-4o-mini", auth_header_value: "{vault://env/OPENAI_AUTH_HEADER}" },
      },
      { id: "out", type: "response_out", config: {}, position: { x: 0, y: 480 } },
    ],
    edges: [
      { source: "in", target: "llm" },
      { source: "llm", target: "out" },
    ],
  };
}

export const KIND_LABEL: Record<NodeType["kind"], string> = {
  endpoint: "Endpoint",
  native: "Kong plugin",
  custom: "Guardrail engine",
  control: "Control",
};
