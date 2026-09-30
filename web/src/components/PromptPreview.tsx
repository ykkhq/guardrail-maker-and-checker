import { useState } from "react";
import { api } from "../api";

/** Shows the exact Llama Guard prompt for the node's current config. */
export function PromptPreview({ config, phase }: { config: Record<string, unknown>; phase: "request" | "response" }) {
  const [text, setText] = useState("<message>");
  const [prompt, setPrompt] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const load = async () => {
    try {
      setErr(null);
      setPrompt((await api.previewLlamaGuard(config, phase, text)).prompt);
    } catch (e: any) {
      setErr(e.message);
    }
  };
  return (
    <details className="preview" onToggle={(e) => (e.currentTarget as HTMLDetailsElement).open && load()}>
      <summary>Preview prompt ({phase === "request" ? "checks User messages" : "checks Agent answers"})</summary>
      <div className="row">
        <input value={text} onChange={(e) => setText(e.target.value)} aria-label="Sample message" />
        <button type="button" className="btn sm" onClick={load}>Refresh</button>
      </div>
      {err && <p className="error">{err}</p>}
      {prompt !== null && <pre className="code">{prompt}</pre>}
    </details>
  );
}
