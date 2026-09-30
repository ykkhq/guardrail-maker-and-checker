import type {
  DeployResult, Issue, NodeType, PipelineGraph, PipelineSummary, PlaygroundResult, Status,
} from "./types";

const BASE = "/api/v1";

async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(BASE + path, {
    method,
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  // 422/502 carry structured issues/errors the UI shows; everything else >= 400 throws.
  if (!res.ok && res.status !== 422 && res.status !== 502) {
    throw new Error(data?.detail ?? `${method} ${path} failed (${res.status})`);
  }
  return data as T;
}

export const api = {
  catalog: () => call<{ node_types: NodeType[] }>("GET", "/catalog").then((r) => r.node_types),
  status: () => call<Status>("GET", "/status"),
  validate: (g: PipelineGraph) =>
    call<{ ok: boolean; issues: Issue[]; phases: Record<string, string> }>("POST", "/validate", g),
  compile: (g: PipelineGraph) =>
    call<{ ok: boolean; result?: unknown; stages?: DeployResult["stages"]; warnings?: string[]; issues?: Issue[] }>(
      "POST", "/compile", { graph: g, format: "deck" }),
  list: () => call<{ pipelines: PipelineSummary[] }>("GET", "/pipelines").then((r) => r.pipelines),
  get: (slug: string) => call<PipelineGraph>("GET", `/pipelines/${slug}`),
  save: (g: PipelineGraph) => call<{ ok: boolean }>("PUT", `/pipelines/${g.slug}`, g),
  remove: (slug: string) => call<{ ok: boolean }>("DELETE", `/pipelines/${slug}`),
  deploy: (g: PipelineGraph) => call<DeployResult>("POST", `/pipelines/${g.slug}/deploy`, g),
  versions: (slug: string) =>
    call<{ versions: { version: number; deployed_at: number }[] }>("GET", `/pipelines/${slug}/versions`)
      .then((r) => r.versions),
  rollback: (slug: string, v: number) => call<DeployResult>("POST", `/pipelines/${slug}/rollback/${v}`),
  playground: (slug: string, mode: "live" | "dry", messages: unknown[], graph?: PipelineGraph) =>
    call<PlaygroundResult>("POST", "/playground", { slug, mode, messages, graph }),
};
