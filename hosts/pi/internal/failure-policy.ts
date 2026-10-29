/** Offline fallback is deliberately narrower than the Python tool policy.
 * Never parse arbitrary shell syntax when the authority is unavailable.
 */
import { lstatSync } from "node:fs";
import path from "node:path";

export type Enrollment = "enabled" | "disabled" | "unknown";

export function enrollment(cwd: string): Enrollment {
  let directory = path.resolve(cwd);
  while (true) {
    try {
      // A missing or corrupt config inside an existing state directory is a fault.
      lstatSync(path.join(directory, ".orchestrator"));
      return "enabled";
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== "ENOENT") {
        return "unknown";
      }
    }
    try {
      // Do not inherit another project's state above this Git boundary.
      lstatSync(path.join(directory, ".git"));
      return "disabled";
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== "ENOENT") {
        return "unknown";
      }
    }
    const parent = path.dirname(directory);
    if (parent === directory) {
      return "disabled";
    }
    directory = parent;
  }
}

// Exact command strings, no interpolation, redirects or chaining.
// All other classification remains owned by the Python core when it is healthy.
const RECOVERY_COMMANDS = new Set([
  "cao diagnose", "cao lifecycle recover --check", "pwd", "cao --help", "cao status", "cao host status", "cao host status --host pi",
  "cao host install --host pi",
]);
const READ_TOOLS = new Set(["read", "ls", "grep", "find", "search_code"]);

function recoveryCommand(command: string, root: string): boolean {
  if (RECOVERY_COMMANDS.has(command)) {
    return true;
  }
  // Only this running adapter's bundled launcher is trusted. A matching
  // basename or a path supplied in a tool argument is not an installation.
  const launcher = path.join(root, "cao");
  const quoted = "'" + launcher.replaceAll("'", "'\\''") + "'";
  const launchers = [quoted, "sh " + quoted];
  if (/^[A-Za-z0-9_./:-]+$/.test(launcher)) {
    launchers.push(launcher, "sh " + launcher);
  }
  if (!/[\$`"\\\n\r]/.test(launcher)) {
    launchers.push('"' + launcher + '"', 'sh "' + launcher + '"');
  }
  for (const prefix of ["cao", ...launchers]) {
    const suffix = command.startsWith(prefix + " ") ? command.slice(prefix.length + 1) : "";
    if (RECOVERY_COMMANDS.has("cao " + suffix)
      || /^(?:status|diagnose|lifecycle recover --check) --work [A-Za-z0-9][A-Za-z0-9._-]*$/.test(suffix)) {
      return true;
    }
  }
  return false;
}

export function offlineAllowed(event: { toolName: string; input: unknown }, root: string): boolean {
  if (READ_TOOLS.has(event.toolName)) {
    return true;
  }
  if (event.input && typeof event.input === "object") {
    const input = event.input as Record<string, unknown>;
    if (event.toolName === "contact_supervisor") {
      return Object.keys(input).every(key => ["reason", "message"].includes(key))
        && ["need_decision", "progress_update"].includes(String(input.reason)) && typeof input.message === "string";
    }

  }
  if (event.toolName !== "bash" || !event.input || typeof event.input !== "object") {
    return false;
  }
  const input = event.input as Record<string, unknown>;
  // Avoid alternate cwd/env or execution parameters extending a literal exception.
  if (Object.keys(input).some(key => !["command", "timeout"].includes(key))) {
    return false;
  }
  return typeof input.command === "string" && recoveryCommand(input.command, root);
}
