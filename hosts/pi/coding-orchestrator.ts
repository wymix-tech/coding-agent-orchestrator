import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import path from "node:path";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const CAO_FALLBACK = path.join(ROOT, "cao");

function callCao(event: string, payload: any, cwd: string) {
  const input = JSON.stringify({ event, ...payload });
  const args = ["host-event", "--repo", cwd, "--host", "pi"];
  let r = spawnSync("cao", args, {
    cwd,
    input,
    encoding: "utf8",
  });
  // If CAO is not installed as a console command, support plugin-from-checkout use.
  if (r.error && (r.error as NodeJS.ErrnoException).code === "ENOENT") {
    r = spawnSync("sh", [CAO_FALLBACK, ...args], {
      cwd,
      input,
      encoding: "utf8",
    });
  }
  // Fail open. Returning a deny here meant a broken or missing CAO binary
  // blocked every tool call, including the `cao bootstrap` an Agent must run
  // to make any progress. CAO advises; it never blocks the Agent's tools.
  if (r.error || r.status !== 0) return {};
  try { return JSON.parse(r.stdout || "{}"); } catch { return {}; }
}

// Pi does not expose one documented "last assistant message" field, so collect
// every plausible name. Sending an empty string would silently disable CAO's
// completion gate, because `cao host-event` only gates a stop when a message is
// present.
function lastAssistantMessage(event: any): string {
  const candidates = [
    event?.last_assistant_message,
    event?.lastAssistantMessage,
    event?.final_message,
    event?.finalMessage,
    event?.message,
  ];
  for (const value of candidates) {
    if (typeof value === "string" && value.trim()) return value;
    if (value && typeof value.text === "string" && value.text.trim()) return value.text;
  }
  return "";
}

function nativeSessionId(event: any, ctx: any): string | undefined {
  const candidates = [
    event?.session_id,
    event?.sessionId,
    (ctx as any)?.sessionId,
    (ctx as any)?.session?.id,
  ];
  for (const value of candidates) {
    if (typeof value === "string" && value.trim()) return value;
  }
  return undefined;
}

export default function (pi: ExtensionAPI) {
  pi.on("session_start", async (event, ctx) => {
    const r = callCao("session_start", { ...event, session_id: nativeSessionId(event, ctx) }, ctx.cwd);
    if (r.additional_context) ctx.ui.notify(r.additional_context, "info");
  });

  pi.on("before_agent_start", async (event, ctx) => {
    const r = callCao("prompt_submit", { ...event, session_id: nativeSessionId(event, ctx) }, ctx.cwd);
    if (r.additional_context) {
      return { message: { customType: "coding-agent-orchestrator", content: r.additional_context, display: true } };
    }
  });

  pi.on("tool_call", async (event, ctx) => {
    const r = callCao("pre_tool", { ...event, session_id: nativeSessionId(event, ctx) }, ctx.cwd);
    // CAO never blocks tool calls; when it has guidance (e.g. the native SDD
    // workflow has not run yet), surface it instead of dropping it.
    if (r.additional_context && ctx?.ui?.notify) ctx.ui.notify(r.additional_context, "info");
    if (r.decision === "deny") return { block: true, reason: r.reason };
  });

  pi.on("tool_result", async (event, ctx) => {
    const r = callCao("post_tool", { ...event, session_id: nativeSessionId(event, ctx) }, ctx.cwd);
    if (r.additional_context) {
      return { content: [...event.content, { type: "text", text: `[CAO] ${r.additional_context}` }] } as any;
    }
  });

  pi.on("agent_end", async (event, ctx) => {
    const r = callCao(
      "stop",
      { ...event, session_id: nativeSessionId(event, ctx), last_assistant_message: lastAssistantMessage(event) },
      ctx.cwd,
    );
    if (r.decision === "deny") {
      pi.sendMessage({ customType: "coding-agent-orchestrator", content: r.reason || "CAO completion gate blocked", display: true }, { triggerTurn: false, deliverAs: "followUp" });
    }
  });
}
