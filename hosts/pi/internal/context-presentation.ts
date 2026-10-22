import type { ExtensionAPI, ExtensionContext } from "@earendil-works/pi-coding-agent";
import { matchesKey, stripTerminalSequences, Text, truncateToWidth, wrapTextWithAnsi } from "@earendil-works/pi-tui";

type Guidance = { additional_context?: string; reason?: string; decision?: string; diagnostic?: string };
const WIDGET = "coding-agent-orchestrator";

function plain(text: string): string {
  return stripTerminalSequences(text).replace(/[\x00-\x08\x0b-\x1f\x7f-\x9f]/g, "");
}

function line(text: string): string {
  return plain(text).replace(/\s+/g, " ").trim();
}

/** Presentation only: never use this summary to authorize actions or replace model context. */
export function contextSummary(content: string, blocked = false): string[] {
  const text = plain(content);
  const field = (name: string) => text.match(new RegExp(`^${name}: (.+)$`, "m"))?.[1];
  let action: Record<string, unknown> = {};
  const next = text.match(/^## Next Action\s*\n([^\n]+)/m)?.[1];
  if (next) {
    try {
      const parsed = JSON.parse(next);
      if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
        action = parsed;
      }
    } catch { /* Non-JSON guidance still has a bounded text summary. */ }
  }
  const title = field("Work Item") || text.match(/^# Coding Agent Orchestrator (.+)$/m)?.[1] || "Guidance";
  const provider = field("SDD"), step = field("Step");
  const stage = typeof action.stage === "string" ? action.stage : undefined;
  const reason = typeof action.reason === "string" ? action.reason : undefined;
  const instruction = typeof action.instruction === "string" ? action.instruction : undefined;
  const fallback = text.split("\n").map(line).find(value => value && !value.startsWith("#") && !value.startsWith("{"));
  return [
    `CAO · ${blocked ? "Blocked · " : ""}${line(title)}`,
    [stage && `Stage: ${line(stage)}`, provider && `SDD: ${line(provider)}`,
      step && `Step: ${line(step)}`].filter(Boolean).join(" · "),
    `Next: ${line(instruction || reason || fallback || "Context updated")}`,
    "Full context: /cao-context",
  ].filter(Boolean);
}

export function createContextPresentation(pi: ExtensionAPI) {
  const latest = new Map<string, string>();
  const displayed = new Map<string, string>();
  const key = (ctx: ExtensionContext) => JSON.stringify([ctx.cwd, ctx.sessionManager.getSessionId()]);

  // Also collapse older visible CAO messages when an existing conversation is reloaded.
  pi.registerMessageRenderer(WIDGET, (message, options) => {
    const content = typeof message.content === "string" ? message.content : message.content
      .filter(part => part.type === "text").map(part => part.text).join("\n");
    if (options.expanded) {
      return new Text(plain(content), 0, 0);
    }
    return {
      render: width => contextSummary(content).map(value => truncateToWidth(value, Math.max(0, width))),
      invalidate() {},
    };
  });

  pi.registerCommand("cao-context", {
    description: "View the complete CAO context without sending another model message",
    handler: async (_args, ctx) => {
      if (ctx.mode !== "tui") {
        if (ctx.hasUI) {
          ctx.ui.notify("CAO context viewer requires interactive terminal mode.", "info");
        }
        return;
      }
      const content = plain(latest.get(key(ctx)) || "No CAO context recorded for this session.");
      await ctx.ui.custom<void>((tui, _theme, _keys, done) => {
        let offset = 0, pageSize = 1, count = 0;
        return {
          render(width) {
            const columns = Math.max(1, width);
            const rows = content.split("\n").flatMap(value => wrapTextWithAnsi(value, columns));
            count = rows.length;
            pageSize = Math.max(1, tui.terminal.rows - 4);
            offset = Math.min(offset, Math.max(0, count - pageSize));
            return [truncateToWidth("CAO context · ↑/↓ PgUp/PgDn · Esc to close", columns),
              ...rows.slice(offset, offset + pageSize).map(value => truncateToWidth(value, columns)),
              truncateToWidth(`${offset + 1}–${Math.min(count, offset + pageSize)} / ${count}`, columns)];
          },
          handleInput(data) {
            if (matchesKey(data, "escape") || data === "q") {
              done();
              return;
            }
            if (matchesKey(data, "up")) {
              offset--;
            } else if (matchesKey(data, "down")) {
              offset++;
            } else if (matchesKey(data, "pageUp")) {
              offset -= pageSize;
            } else if (matchesKey(data, "pageDown")) {
              offset += pageSize;
            } else if (matchesKey(data, "home")) {
              offset = 0;
            } else if (matchesKey(data, "end")) {
              offset = count;
            }
            offset = Math.max(0, Math.min(offset, Math.max(0, count - pageSize)));
            tui.requestRender();
          },
          invalidate() {},
        };
      });
    },
  });

  return {
    show(result: Guidance, ctx: ExtensionContext) {
      const content = result.additional_context || result.reason || result.diagnostic;
      if (!content) {
        return;
      }
      const full = [result.diagnostic, result.reason].filter((value): value is string => !!value && !content.includes(value))
        .concat(content).join("\n");
      latest.set(key(ctx), full);
      if (ctx.mode === "tui") {
        const summary = contextSummary(full, result.decision === "deny" || !!result.diagnostic);
        if (result.decision === "deny" && result.reason) {
          summary[summary.length - 2] = `Blocked: ${line(result.reason)}`;
        }
        const signature = JSON.stringify(summary);
        if (displayed.get(key(ctx)) === signature) {
          return;
        }
        try {
          ctx.ui.setWidget(WIDGET, () => ({
            render: width => summary.map(value => truncateToWidth(value, Math.max(0, width))),
            invalidate() {},
          }));
          displayed.set(key(ctx), signature);
        } catch { /* A UI failure must not discard the complete model guidance or change a gate decision. */ }
      }
    },
    reset(ctx: ExtensionContext) {
      latest.delete(key(ctx));
      displayed.delete(key(ctx));
      if (ctx.mode === "tui") {
        try {
          ctx.ui.setWidget(WIDGET, undefined);
        } catch { /* Session cleanup must work even if the UI is already shutting down. */ }
      }
    },
  };
}
