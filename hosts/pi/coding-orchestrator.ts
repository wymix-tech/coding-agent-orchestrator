import type { AgentEndEvent, ExtensionAPI, ExtensionContext } from "@earendil-works/pi-coding-agent";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { openSync, readSync, closeSync } from "node:fs";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
type CaoReply = { decision?: "allow" | "deny"; additional_context?: string; reason?: string; diagnostic?: string };
type Invocation = { code: number | null; out: string; error?: string; missing?: boolean };

function invoke(command: string, args: string[], input: string, cwd: string): Promise<Invocation> {
  return new Promise(resolve => {
    const child = spawn(command, args, { cwd, stdio: ["pipe", "pipe", "pipe"], detached: process.platform !== "win32" });
    let out = "", err = "", failure = "";
    function abort(reason: string) {
      if (failure) return;
      failure = reason;
      clearTimeout(timer);
      // POSIX descendants can retain pipes after their parent exits; kill the owned group.
      if (process.platform !== "win32" && child.pid) {
        try { process.kill(-child.pid, "SIGKILL"); } catch { child.kill("SIGKILL"); }
      } else child.kill("SIGKILL");
      child.stdin.destroy(); child.stdout.destroy(); child.stderr.destroy();
      // Do not depend on 'close': inherited pipes must not extend the deadline.
      resolve({ code: null, out: "", error: reason });
    }
    const timer = setTimeout(() => abort("CAO_TIMEOUT"), 5000);
    child.stdout.setEncoding("utf8");
    child.stderr.setEncoding("utf8");
    child.stdout.on("data", chunk => {
      if (failure) return;
      out += chunk;
      if (Buffer.byteLength(out) > 262144) abort("CAO_OUTPUT_LIMIT");
    });
    child.stderr.on("data", chunk => { err = (err + chunk).slice(-4000); });
    child.on("error", error => {
      clearTimeout(timer);
      resolve({ code: null, out: "", error: error.message,
        missing: (error as NodeJS.ErrnoException).code === "ENOENT" });
    });
    child.on("close", code => {
      clearTimeout(timer);
      resolve({ code, out, error: failure || (code !== 0 ? err || `CAO_EXIT_${code}` : undefined) });
    });
    child.stdin.on("error", () => {});
    child.stdin.end(input);
  });
}

async function callCao(event: string, payload: object, cwd: string): Promise<CaoReply> {
  const args = ["host-event", "--repo", cwd, "--host", "pi"];
  const input = JSON.stringify({ cwd, ...payload, event });
  let result = await invoke("cao", args, input, cwd);
  if (result.missing) result = await invoke("sh", [path.join(ROOT, "cao"), ...args], input, cwd);
  if (result.error || result.code !== 0) return { diagnostic: result.error || "CAO_UNAVAILABLE" };
  try {
    const value = JSON.parse(result.out);
    if (!value || typeof value !== "object" || Array.isArray(value)
      || !["allow", "deny"].includes(value.decision)
      || ["additional_context", "reason", "diagnostic"].some(key => value[key] !== undefined && typeof value[key] !== "string")) {
      throw Error("CAO_PROTOCOL_INVALID");
    }
    return value as CaoReply;
  } catch { return { diagnostic: "CAO_PROTOCOL_INVALID" }; }
}

function lastAssistantMessage(event: AgentEndEvent): string {
  for (const message of [...event.messages].reverse()) {
    if (message.role === "assistant") {
      return message.content.filter(content => content.type === "text").map(content => content.text).join("\n");
    }
  }
  return "";
}

function nativeSessionId(ctx: ExtensionContext): string {
  return ctx.sessionManager.getSessionId();
}

function sessionLineage(ctx: ExtensionContext): { kind: "primary" | "subagent"; parent_session_id?: string; subagent_id?: string } {
  // Read host session metadata, never the prompt or the agent's task text.
  const manager = ctx.sessionManager as unknown as { getHeader?: () => { parentSession?: string } | null };
  const parentFile = manager.getHeader?.()?.parentSession;
  // Detached pi-subagents runners advertise their child role even when an old
  // session archive has no parentSession header. Never treat them as primary.
  const runnerChild = process.env.PI_SUBAGENT_CHILD === "1";
  if (!parentFile && !runnerChild) return { kind: "primary" };
  let parent: string | undefined;
  let fd: number | undefined;
  try {
    if (!parentFile) throw Error("No parent archive");
    fd = openSync(path.resolve(ctx.cwd, parentFile), "r");
    const buffer = Buffer.alloc(8192);
    const count = readSync(fd, buffer, 0, buffer.length, 0);
    const header = JSON.parse(buffer.subarray(0, count).toString("utf8").split("\n", 1)[0]);
    if (header.type === "session" && typeof header.id === "string") parent = header.id;
  } catch { /* Still a child when a parent archive is missing; never upgrade it. */ }
  finally { if (fd !== undefined) closeSync(fd); }
  if (!parent && runnerChild) parent = process.env.PI_SUBAGENT_PARENT_SESSION;
  return { kind: "subagent", subagent_id: nativeSessionId(ctx), ...(parent ? { parent_session_id: parent } : {}) };
}

export default function (pi: ExtensionAPI) {
  const diagnostics = new Set<string>(), reminders = new Set<string>(), continued = new Set<string>();

  async function request(event: string, payload: object, ctx: ExtensionContext): Promise<CaoReply> {
    const id = nativeSessionId(ctx);
    const lineage = sessionLineage(ctx);
    const mapped = lineage.kind === "subagent" ? ({ session_start: "subagent_start", session_end: "subagent_stop" } as Record<string, string>)[event] || event : event;
    const hostTools = typeof pi.getAllTools === "function" ? pi.getAllTools().map(tool => tool.name) : undefined;
    const result = await callCao(mapped, { ...payload, ...lineage, session_id: id, host_tools: hostTools }, ctx.cwd);
    if (result.diagnostic) {
      const key = JSON.stringify([id, result.diagnostic]);
      if (!diagnostics.has(key)) {
        diagnostics.add(key);
        if (ctx.hasUI) ctx.ui.notify(`[CAO] ${result.diagnostic}`, "warning");
        else process.stderr.write(`[CAO] ${result.diagnostic}\n`);
      }
    }
    return result;
  }

  function remind(result: CaoReply, ctx: ExtensionContext) {
    if (!result.additional_context) return;
    const key = JSON.stringify([nativeSessionId(ctx), result.additional_context]);
    if (reminders.has(key)) return;
    reminders.add(key);
    pi.sendMessage({ customType: "coding-agent-orchestrator", content: result.additional_context, display: true },
      { triggerTurn: false, deliverAs: "steer" });
    if (ctx.hasUI) ctx.ui.notify(result.additional_context, "info");
  }

  pi.on("session_start", async (event, ctx) => {
    diagnostics.clear(); reminders.clear(); continued.clear();
    remind(await request("session_start", event, ctx), ctx);
  });

  pi.on("before_agent_start", async (event, ctx) => {
    continued.delete(nativeSessionId(ctx));
    reminders.clear();
    const result = await request("prompt_submit", event, ctx);
    if (result.additional_context) {
      return { message: { customType: "coding-agent-orchestrator", content: result.additional_context, display: true } };
    }
  });

  pi.on("tool_call", async (event, ctx) => {
    const result = await request("pre_tool", event, ctx);
    remind(result, ctx);
    if (result.decision === "deny") return { block: true, reason: result.reason };
  });

  pi.on("tool_result", async (event, ctx) => {
    // Guidance is a separate context message: preserve content, details and structuredContent.
    remind(await request("post_tool", event, ctx), ctx);
  });

  pi.on("agent_end", async (event, ctx) => {
    remind(await request("turn_end", { last_assistant_message: lastAssistantMessage(event) }, ctx), ctx);
  });

  pi.on("agent_before_settle", async (event, ctx) => {
    const id = nativeSessionId(ctx);
    const result = await request("before_settle", {}, ctx);
    remind(result, ctx);
    if (result.decision === "deny" && event.context.canContinue && !continued.has(id)) {
      continued.add(id);
      return { entries: [{ type: "custom_message" as const, customType: "coding-agent-orchestrator",
        content: result.reason || "CAO requires correction", display: true }], continue: true };
    }
  });

  pi.on("session_shutdown", async (event, ctx) => {
    await request("session_end", { reason: event.reason }, ctx);
    diagnostics.clear(); reminders.clear(); continued.clear();
  });
}
