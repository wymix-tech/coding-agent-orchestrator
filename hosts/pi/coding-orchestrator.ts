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
  if (r.status !== 0) return { decision: "deny", reason: r.stderr || "CAO failed" };
  try { return JSON.parse(r.stdout || "{}"); } catch { return { decision: "deny", reason: "Invalid CAO response" }; }
}

export default function (pi: ExtensionAPI) {
  pi.on("session_start", async (event, ctx) => {
    const r = callCao("session_start", event, ctx.cwd);
    if (r.additional_context) ctx.ui.notify(r.additional_context, "info");
  });

  pi.on("before_agent_start", async (event, ctx) => {
    const r = callCao("prompt_submit", event, ctx.cwd);
    if (r.additional_context) {
      return { message: { customType: "coding-agent-orchestrator", content: r.additional_context, display: true } };
    }
  });

  pi.on("tool_call", async (event, ctx) => {
    const r = callCao("pre_tool", event, ctx.cwd);
    if (r.decision === "deny") return { block: true, reason: r.reason };
  });

  pi.on("tool_result", async (event, ctx) => {
    const r = callCao("post_tool", event, ctx.cwd);
    if (r.additional_context) {
      return { content: [...event.content, { type: "text", text: `[CAO] ${r.additional_context}` }] } as any;
    }
  });

  pi.on("agent_end", async (event, ctx) => {
    const r = callCao("stop", { ...event, last_assistant_message: "" }, ctx.cwd);
    if (r.decision === "deny") {
      pi.sendMessage({ customType: "coding-agent-orchestrator", content: r.reason || "CAO completion gate blocked", display: true }, { triggerTurn: false, deliverAs: "followUp" });
    }
  });
}
