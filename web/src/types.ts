// Mirrors guardrail_common (graph.py, catalog.py) and the studio-api responses.

export type Kind = "endpoint" | "native" | "custom" | "control";

export interface NodeType {
  type: string;
  label: string;
  category: string;
  kind: Kind;
  description: string;
  phases: string[];
  plugin: string | null;
  outputs: string[];
  transforms_text: boolean;
  config_schema: JsonSchema;
  // From the engine: false when the detector's Python modules are not installed.
  available?: boolean;
  missing?: string[];
}

// Loose JSON Schema shape; rjsf consumes it directly.
export type JsonSchema = { [key: string]: any };

export interface GraphNode {
  id: string;
  type: string;
  config: Record<string, unknown>;
  label?: string | null;
  position?: { x: number; y: number } | null;
}

export interface GraphEdge {
  source: string;
  target: string;
  sourceHandle?: string | null;
}

export interface PipelineGraph {
  slug: string;
  name: string;
  nodes: GraphNode[];
  edges: GraphEdge[];
}

export interface Issue {
  level: "error" | "warning";
  message: string;
  node: string | null;
  edge: [string, string] | null;
}

export interface PipelineSummary {
  slug: string;
  name: string;
  updated_at: number;
  version: number | null;
  deployed_at: number | null;
}

export type Outcome = "pass" | "flag" | "mask" | "block" | "error_open" | "branch_true" | "branch_false";

export interface TraceStep {
  node: string;
  type: string;
  outcome: Outcome;
  result: Record<string, any>;
  latency_ms: number;
}

export interface SegmentResponse {
  decision: "allow" | "block";
  status: number;
  message: string | null;
  blocked_by: string | null;
  body: { messages?: { role: string; content: unknown }[] };
  trace: TraceStep[];
  latency_ms: number;
}

export interface PlaygroundResult {
  mode: "live" | "dry";
  trace: SegmentResponse | { error: string };
  live?: { status: number; body: any; latency_ms?: number; kong_request_id?: string | null };
}

export interface Status {
  konnect: { ok: boolean; region: string; control_plane: string; control_plane_id: string | null; error?: string | null };
  engine: { ok: boolean; url: string };
  kong: { ok: boolean; url: string };
}

export interface DeployResult {
  ok: boolean;
  version?: number;
  warnings?: string[];
  endpoint?: string;
  stages?: { phase: string; plugin: string; node?: string; nodes?: string[] }[];
  issues?: Issue[];
  error?: string;
  graph?: PipelineGraph;
}
