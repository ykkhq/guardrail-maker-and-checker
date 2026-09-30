import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  addEdge, Background, Controls, MiniMap, ReactFlow, useEdgesState, useNodesInitialized, useNodesState, useReactFlow,
  type Connection, type Edge, type IsValidConnection, type OnBeforeDelete,
} from "@xyflow/react";
import { api } from "./api";
import { CatalogContext } from "./catalog";
import { fromFlow, newPipeline, toFlow, toFlowEdge, uniqueId, type GuardNode } from "./graph";
import { GuardNodeView } from "./components/GuardNodeView";
import { Inspector } from "./components/Inspector";
import { DRAG_TYPE, Palette } from "./components/Palette";
import { Playground } from "./components/Playground";
import { ConfigPanel, IssuesPanel } from "./components/SidePanels";
import type {
  Issue, NodeType, PipelineGraph, PipelineSummary, PlaygroundResult, SegmentResponse, Status, ValidateResult,
} from "./types";

const nodeTypes = { guard: GuardNodeView };
type Tab = "inspector" | "issues" | "config" | "playground";
type Toast = { kind: "ok" | "error" | "info"; text: string };

export default function App() {
  const [types, setTypes] = useState<NodeType[]>([]);
  const catalog = useMemo(() => Object.fromEntries(types.map((t) => [t.type, t])), [types]);
  const [pipelines, setPipelines] = useState<PipelineSummary[]>([]);
  const [slug, setSlug] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [nodes, setNodes, onNodesChange] = useNodesState<GuardNode>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const [saved, setSaved] = useState("");
  const [issues, setIssues] = useState<Issue[]>([]);
  // For the condition editor: result fields and a suggested rule per detector node.
  const [fields, setFields] = useState<ValidateResult["fields"]>({});
  const [defaultRules, setDefaultRules] = useState<ValidateResult["default_rules"]>({});
  const [phases, setPhases] = useState<ValidateResult["phases"]>({});
  const [compiled, setCompiled] = useState<Awaited<ReturnType<typeof api.compile>> | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [tab, setTab] = useState<Tab>("inspector");
  const [result, setResult] = useState<PlaygroundResult | null>(null);
  const [running, setRunning] = useState(false);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<Status | null>(null);
  const [toast, setToast] = useState<Toast | null>(null);
  const [versions, setVersions] = useState<{ version: number; deployed_at: number }[] | null>(null);
  const { screenToFlowPosition, fitView } = useReactFlow();
  const nodesInitialized = useNodesInitialized();
  const [fitPending, setFitPending] = useState(false);
  useEffect(() => {
    // Fit once the loaded nodes have been measured (fitting earlier clips them).
    if (fitPending && nodesInitialized) {
      fitView({ padding: 0.15, maxZoom: 1 });
      setFitPending(false);
    }
  }, [fitPending, nodesInitialized, fitView]);
  const toastTimer = useRef<number>(undefined);

  const notify = useCallback((t: Toast) => {
    setToast(t);
    window.clearTimeout(toastTimer.current);
    toastTimer.current = window.setTimeout(() => setToast(null), t.kind === "error" ? 9000 : 5000);
  }, []);

  const graph: PipelineGraph | null = useMemo(
    () => (slug ? fromFlow(slug, name, nodes, edges) : null),
    [slug, name, nodes, edges],
  );
  const graphJson = useMemo(() => (graph ? JSON.stringify(graph) : ""), [graph]);
  const dirty = !!graph && graphJson !== saved;
  const current = pipelines.find((p) => p.slug === slug);

  // --- loading ---------------------------------------------------------------------

  const load = useCallback(
    (g: PipelineGraph) => {
      const flow = toFlow(g);
      setSlug(g.slug);
      setName(g.name);
      setNodes(flow.nodes);
      setEdges(flow.edges);
      setSaved(JSON.stringify(fromFlow(g.slug, g.name, flow.nodes, flow.edges)));
      setSelected(null);
      setResult(null);
      setVersions(null);
      setFitPending(true);
    },
    [setNodes, setEdges, fitView],
  );

  const refreshList = useCallback(() => api.list().then(setPipelines), []);

  useEffect(() => {
    api.catalog().then(setTypes).catch((e) => notify({ kind: "error", text: `studio-api: ${e.message}` }));
    refreshList().then(() => undefined);
  }, [notify, refreshList]);

  useEffect(() => {
    if (!slug && pipelines.length) api.get(pipelines[0].slug).then(load);
  }, [pipelines, slug, load]);

  useEffect(() => {
    const tick = () => api.status().then(setStatus).catch(() => setStatus(null));
    tick();
    const t = window.setInterval(tick, 15000);
    return () => window.clearInterval(t);
  }, []);

  // --- live validation (debounced) ----------------------------------------------------

  useEffect(() => {
    if (!graph) return;
    const t = window.setTimeout(async () => {
      try {
        const v = await api.validate(graph);
        setIssues(v.issues);
        setFields(v.fields ?? {});
        setDefaultRules(v.default_rules ?? {});
        setPhases(v.phases ?? {});
        setCompiled(v.ok ? await api.compile(graph) : null);
      } catch (e: any) {
        setIssues([{ level: "error", message: e.message, node: null, edge: null }]);
        setCompiled(null);
      }
    }, 300);
    return () => window.clearTimeout(t);
    // graphJson changes exactly when the graph content does
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [graphJson]);

  const errorCount = issues.filter((i) => i.level === "error").length;

  // --- playground overlay: outcomes per node ---------------------------------------

  const outcomes = useMemo(() => {
    const out: Record<string, string> = {};
    const trace = result && "trace" in result.trace ? (result.trace as SegmentResponse) : null;
    if (!trace) return out;
    for (const n of nodes) {
      const kind = catalog[n.data.nodeType]?.kind;
      if (kind === "custom" || kind === "control") out[n.id] = "skipped";
    }
    for (const s of trace.trace) out[s.node] = s.outcome;
    return out;
  }, [result, nodes, catalog]);

  const renderNodes = useMemo(
    () =>
      nodes.map((n) => ({
        ...n,
        data: {
          ...n.data,
          issues: issues.filter((i) => i.node === n.id),
          outcome: (outcomes[n.id] as GuardNode["data"]["outcome"]) ?? null,
        },
      })),
    [nodes, issues, outcomes],
  );

  // --- canvas editing ----------------------------------------------------------------

  const onConnect = useCallback(
    (c: Connection) => {
      setEdges((eds) => addEdge(toFlowEdge({ source: c.source, target: c.target, sourceHandle: c.sourceHandle }), eds));
      // Detector -> empty condition: start it with a rule built from the detector's
      // outputs (for Laya, its first choice question).
      const src = nodes.find((n) => n.id === c.source);
      const dst = nodes.find((n) => n.id === c.target);
      const rules = (dst?.data.config.rules as unknown[] | undefined) ?? [];
      if (src && dst && dst.data.nodeType === "condition" && !rules.length && catalog[src.data.nodeType]?.kind === "custom") {
        const rule = defaultRules[src.id] ?? { node: src.id, field: "detected", op: "eq", value: true };
        setNodes((ns) => ns.map((n) => (n.id === dst.id ? { ...n, data: { ...n.data, config: { ...n.data.config, rules: [rule] } } } : n)));
      }
    },
    [setEdges, setNodes, nodes, catalog, defaultRules],
  );

  // Detector nodes that run before `id`, nearest last (the condition editor's sources).
  const upstreamOf = useCallback(
    (id: string) => {
      const pred: Record<string, string[]> = {};
      for (const e of edges) (pred[e.target] ??= []).push(e.source);
      const seen = new Set<string>();
      const order: string[] = [];
      const visit = (n: string) => {
        for (const p of pred[n] ?? []) {
          if (seen.has(p)) continue;
          seen.add(p);
          visit(p);
          order.push(p);
        }
      };
      visit(id);
      return order
        .map((nid) => nodes.find((n) => n.id === nid))
        .filter((n): n is GuardNode => !!n && catalog[n.data.nodeType]?.kind === "custom")
        .map((n) => ({ id: n.id, label: `${n.data.label || catalog[n.data.nodeType].label} (${n.id})` }));
    },
    [edges, nodes, catalog],
  );

  const isValidConnection: IsValidConnection = useCallback(
    (c) =>
      c.source !== c.target &&
      !edges.some((e) => e.source === c.source && e.target === c.target && (e.sourceHandle ?? null) === (c.sourceHandle ?? null)),
    [edges],
  );

  // Endpoint nodes (Prompt In, LLM, Response Out) cannot be deleted.
  const onBeforeDelete: OnBeforeDelete<GuardNode> = useCallback(
    async ({ nodes: ns, edges: es }) => ({
      nodes: ns.filter((n) => catalog[n.data.nodeType]?.kind !== "endpoint"),
      edges: es,
    }),
    [catalog],
  );

  const onDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      const type = e.dataTransfer.getData(DRAG_TYPE);
      if (!type || !catalog[type]) return;
      const id = uniqueId(type, new Set(nodes.map((n) => n.id)));
      const position = screenToFlowPosition({ x: e.clientX - 95, y: e.clientY - 30 });
      // Only the new node is selected, so Delete never removes an earlier selection too.
      setNodes((ns) => [
        ...ns.map((n) => (n.selected ? { ...n, selected: false } : n)),
        { id, type: "guard", position, data: { nodeType: type, config: {} }, selected: true },
      ]);
      setSelected(id);
      setTab("inspector");
    },
    [catalog, nodes, screenToFlowPosition, setNodes],
  );

  const updateNode = useCallback(
    (id: string, config: Record<string, unknown>, label: string | null) =>
      setNodes((ns) =>
        ns.map((n) => {
          if (n.id !== id) return n;
          // rjsf re-emits unchanged data (defaults) on mount; keep identity then.
          if (JSON.stringify(n.data.config) === JSON.stringify(config) && (n.data.label ?? null) === label) return n;
          return { ...n, data: { ...n.data, config, label } };
        }),
      ),
    [setNodes],
  );

  const deleteNode = useCallback(
    (id: string) => {
      setNodes((ns) => ns.filter((n) => n.id !== id));
      setEdges((es) => es.filter((e) => e.source !== id && e.target !== id));
      setSelected(null);
    },
    [setNodes, setEdges],
  );

  const selectNode = useCallback(
    (id: string) => {
      setSelected(id);
      setNodes((ns) => ns.map((n) => ({ ...n, selected: n.id === id })));
      setTab("inspector");
    },
    [setNodes],
  );

  // --- actions -------------------------------------------------------------------------

  const save = async () => {
    if (!graph) return;
    setBusy(true);
    try {
      await api.save(graph);
      setSaved(graphJson);
      await refreshList();
      notify({ kind: "ok", text: "Saved." });
    } catch (e: any) {
      notify({ kind: "error", text: e.message });
    } finally {
      setBusy(false);
    }
  };

  const deploy = async () => {
    if (!graph) return;
    setBusy(true);
    try {
      const r = await api.deploy(graph);
      if (r.ok) {
        setSaved(graphJson);
        await refreshList();
        notify({ kind: "ok", text: `Deployed v${r.version} to Konnect → ${r.endpoint}` });
        if (r.warnings?.length) setTab("config");
      } else {
        setIssues(r.issues ?? issues);
        notify({ kind: "error", text: r.error ?? "Deploy rejected: fix the errors first." });
        setTab("issues");
      }
    } catch (e: any) {
      notify({ kind: "error", text: e.message });
    } finally {
      setBusy(false);
    }
  };

  const createPipeline = async () => {
    const s = window.prompt("Pipeline slug (lowercase letters, digits, dashes):", "my-pipeline");
    if (!s) return;
    if (!/^[a-z0-9][a-z0-9-]{0,62}$/.test(s)) return notify({ kind: "error", text: "Invalid slug." });
    if (pipelines.some((p) => p.slug === s)) return notify({ kind: "error", text: "That slug already exists." });
    const g = newPipeline(s, window.prompt("Display name:", s) || s);
    await api.save(g);
    await refreshList();
    load(g);
  };

  const removePipeline = async () => {
    if (!slug || !window.confirm(`Delete '${slug}'? Deployed Kong entities are removed from Konnect too.`)) return;
    setBusy(true);
    try {
      await api.remove(slug);
      setSlug(null);
      setNodes([]);
      setEdges([]);
      await refreshList();
      notify({ kind: "ok", text: `Deleted ${slug}.` });
    } catch (e: any) {
      notify({ kind: "error", text: e.message });
    } finally {
      setBusy(false);
    }
  };

  const openVersions = async () => {
    if (!slug) return;
    setVersions(versions ? null : await api.versions(slug));
  };

  const rollback = async (v: number) => {
    if (!slug || !window.confirm(`Roll back '${slug}' to v${v} and redeploy it?`)) return;
    setBusy(true);
    try {
      const r = await api.rollback(slug, v);
      if (r.ok && r.graph) {
        load(r.graph);
        await refreshList();
        notify({ kind: "ok", text: `Rolled back to v${v} (deployed as v${r.version}).` });
      } else notify({ kind: "error", text: r.error ?? "Rollback failed." });
    } finally {
      setBusy(false);
    }
  };

  const run = async (mode: "live" | "dry", text: string) => {
    if (!slug || !graph) return;
    setRunning(true);
    try {
      setResult(await api.playground(slug, mode, [{ role: "user", content: text }], graph));
    } catch (e: any) {
      notify({ kind: "error", text: e.message });
    } finally {
      setRunning(false);
    }
  };

  const selectedNode = nodes.find((n) => n.id === selected) ?? null;

  return (
    <CatalogContext.Provider value={catalog}>
      <div className="app">
        <header className="topbar">
          <div className="brand">
            <span className="logo" aria-hidden>◆</span> Guardrail Studio
          </div>
          <select
            value={slug ?? ""}
            onChange={(e) => {
              if (dirty && !window.confirm("Discard unsaved changes?")) return;
              api.get(e.target.value).then(load);
            }}
          >
            {pipelines.map((p) => (
              <option key={p.slug} value={p.slug}>{p.name} ({p.slug})</option>
            ))}
          </select>
          <button className="btn ghost sm" onClick={createPipeline}>+ New</button>
          {slug && (
            <input className="name" value={name} onChange={(e) => setName(e.target.value)} aria-label="Pipeline name" />
          )}
          <span className={`pill ${errorCount ? "bad" : "good"}`}>
            {errorCount ? `${errorCount} error${errorCount > 1 ? "s" : ""}` : "valid"}
          </span>
          {current?.version ? (
            <span className="muted">v{current.version} deployed</span>
          ) : (
            <span className="muted">not deployed</span>
          )}
          <div className="spacer" />
          <StatusDots status={status} />
          <button className="btn" disabled={!dirty || busy} onClick={save}>{dirty ? "Save" : "Saved"}</button>
          <div className="versions-wrap">
            <button className="btn" disabled={!slug || !current?.version} onClick={openVersions}>Versions</button>
            {versions && (
              <div className="menu">
                {versions.map((v) => (
                  <div key={v.version} className="menu-row">
                    <span>v{v.version}</span>
                    <span className="muted">{new Date(v.deployed_at * 1000).toLocaleString()}</span>
                    <button className="btn ghost sm" disabled={busy || v.version === current?.version}
                            onClick={() => rollback(v.version)}>Roll back</button>
                  </div>
                ))}
              </div>
            )}
          </div>
          <button className="btn primary" disabled={!slug || busy || errorCount > 0 || !status?.konnect.ok} onClick={deploy}
                  title={status?.konnect.ok ? "Save, compile and push to Konnect" : `Konnect not reachable${status?.konnect.error ? ": " + status.konnect.error : ""}`}>
            {busy ? "Working…" : "Deploy"}
          </button>
          <button className="btn ghost sm danger-text" disabled={!slug || busy} onClick={removePipeline} title="Delete pipeline">
            Delete
          </button>
        </header>

        <main className="body">
          <Palette types={types} />
          <div className="canvas" onDragOver={(e) => { e.preventDefault(); e.dataTransfer.dropEffect = "move"; }} onDrop={onDrop}>
            <ReactFlow
              nodes={renderNodes}
              edges={edges}
              nodeTypes={nodeTypes}
              onNodesChange={onNodesChange}
              onEdgesChange={onEdgesChange}
              onConnect={onConnect}
              isValidConnection={isValidConnection}
              onBeforeDelete={onBeforeDelete}
              onNodeClick={(_, n) => { setSelected(n.id); setTab("inspector"); }}
              onPaneClick={() => setSelected(null)}
              deleteKeyCode={["Backspace", "Delete"]}
              fitView
              proOptions={{ hideAttribution: true }}
            >
              <Background gap={20} size={1} />
              <MiniMap pannable zoomable className="minimap" />
              <Controls />
            </ReactFlow>
            {result && (
              <div className="overlay-note">
                Showing {result.mode === "live" ? "live" : "dry-run"} trace ·{" "}
                <button className="linklike" onClick={() => setResult(null)}>clear</button>
              </div>
            )}
          </div>

          <aside className="side">
            <nav className="tabs">
              {(["inspector", "issues", "config", "playground"] as Tab[]).map((t) => (
                <button key={t} className={tab === t ? "active" : ""} onClick={() => setTab(t)}>
                  {t === "issues" && issues.length ? `Issues (${issues.length})` : t[0].toUpperCase() + t.slice(1)}
                </button>
              ))}
            </nav>
            <div className="tab-body">
              {tab === "inspector" &&
                (selectedNode && catalog[selectedNode.data.nodeType] ? (
                  <Inspector
                    node={selectedNode}
                    nodeType={catalog[selectedNode.data.nodeType]}
                    issues={issues.filter((i) => i.node === selectedNode.id)}
                    upstream={selectedNode.data.nodeType === "condition" ? upstreamOf(selectedNode.id) : []}
                    fields={fields}
                    defaultRules={defaultRules}
                    phase={phases[selectedNode.id] === "response" ? "response" : "request"}
                    onChange={(cfg, label) => updateNode(selectedNode.id, cfg, label)}
                    onDelete={() => deleteNode(selectedNode.id)}
                  />
                ) : (
                  <div className="pad muted">
                    <p>Select a node to edit its settings.</p>
                    <p>Drag guardrails from the left, connect <b>Prompt In → … → LLM → Response Out</b>.
                      Condition nodes have <b>true</b>/<b>false</b> outputs.</p>
                    <p>Kong plugins run on every request, so they can't sit inside a branch.</p>
                  </div>
                ))}
              {tab === "issues" && <IssuesPanel issues={issues} onSelect={selectNode} />}
              {tab === "config" && <ConfigPanel config={compiled?.ok ? compiled : null} />}
              {tab === "playground" && (
                <Playground
                  deployed={!!current?.version}
                  dirty={dirty}
                  running={running}
                  result={result}
                  onRun={run}
                  onClear={() => setResult(null)}
                />
              )}
            </div>
          </aside>
        </main>
        {toast && <div className={`toast ${toast.kind}`} onClick={() => setToast(null)}>{toast.text}</div>}
      </div>
    </CatalogContext.Provider>
  );
}

function StatusDots({ status }: { status: Status | null }) {
  const items: [string, boolean | undefined, string][] = [
    ["Konnect", status?.konnect.ok,
      status ? `${status.konnect.control_plane} (${status.konnect.region})${status.konnect.error ? ": " + status.konnect.error : ""}` : "studio-api unreachable"],
    ["Kong", status?.kong.ok, status?.kong.url ?? ""],
    ["Engine", status?.engine.ok, status?.engine.url ?? ""],
  ];
  return (
    <div className="status">
      {items.map(([label, ok, title]) => (
        <span key={label} className={`sdot ${ok ? "up" : "down"}`} title={title}>{label}</span>
      ))}
    </div>
  );
}
