import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { spawnSync } from "node:child_process";

const KERNEL = "__KERNEL__";
const REPO = "__REPO__";

function callKernel(eventName: string, payload: any, cwd: string): any {
  const input = { ...payload, cwd, _orchestrator_event: eventName };
  const r = spawnSync("python3", [KERNEL, "host", "--host", "pi", "--repo", REPO], {
    input: JSON.stringify(input), encoding: "utf8", cwd,
  });
  if (r.status !== 0) return { decision: "deny", reason: r.stderr || "orchestrator kernel failed" };
  try { return JSON.parse(r.stdout || "{}"); } catch { return {}; }
}

export default function (pi: ExtensionAPI) {
  let pendingBootstrap: string | null = null;
  pi.on("session_start", async (event, ctx) => {
    const r = callKernel("session_start", event, ctx.cwd);
    pendingBootstrap = r.additional_context || null;
    if (pendingBootstrap) ctx.ui.notify(pendingBootstrap.slice(0, 500), "info");
  });

  pi.on("before_agent_start", async (event, ctx) => {
    const r = callKernel("prompt_submit", event, ctx.cwd);
    const parts = [pendingBootstrap, r.additional_context].filter(Boolean);
    pendingBootstrap = null;
    if (!parts.length) return;
    return { message: { customType: "coding-orchestrator", content: parts.join("\n\n"), display: true } };
  });

  pi.on("tool_call", async (event, ctx) => {
    const r = callKernel("pre_tool", event, ctx.cwd);
    if (r.decision === "deny") return { block: true, reason: r.reason || "Blocked by orchestrator" };
  });

  pi.on("tool_result", async (event, ctx) => {
    const r = callKernel("post_tool", event, ctx.cwd);
    if (r.additional_context) {
      return { content: [...event.content, { type: "text", text: `\n[Orchestrator] ${r.additional_context}` }] } as any;
    }
  });

  pi.on("agent_end", async (event, ctx) => {
    const messages: any[] = event.messages || [];
    const last = [...messages].reverse().find((m: any) => m?.role === "assistant");
    const text = typeof last?.content === "string" ? last.content : JSON.stringify(last?.content || "");
    const r = callKernel("stop", { ...event, last_assistant_message: text }, ctx.cwd);
    if (r.decision === "deny") {
      if (r.metadata?.retry_recommended === false) {
        ctx.ui.notify(r.reason || "Completion is blocked; resolve the reported constraint.", "warning");
        return;
      }
      pi.sendMessage({ customType: "coding-orchestrator", content: r.reason || "Governance completion is not ready.", display: true }, { triggerTurn: true, deliverAs: "followUp" });
    }
  });
}
