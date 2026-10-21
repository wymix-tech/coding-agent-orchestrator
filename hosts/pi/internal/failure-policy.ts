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

// Exact command strings, no paths, flags, interpolation, redirects or chaining.
// All other classification remains owned by the Python core when it is healthy.
const RECOVERY_COMMANDS = new Set([
  "pwd", "cao --help", "cao status", "cao host status", "cao host status --host pi",
  "cao host install --host pi",
]);
const READ_TOOLS = new Set(["read", "ls", "grep", "find"]);

export function offlineAllowed(event: { toolName: string; input: unknown }): boolean {
  if (READ_TOOLS.has(event.toolName)) {
    return true;
  }
  if (event.toolName !== "bash" || !event.input || typeof event.input !== "object") {
    return false;
  }
  const input = event.input as Record<string, unknown>;
  // Avoid alternate cwd/env or execution parameters extending a literal exception.
  if (Object.keys(input).some(key => !["command", "timeout"].includes(key))) {
    return false;
  }
  return typeof input.command === "string" && RECOVERY_COMMANDS.has(input.command);
}
