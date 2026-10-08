# Host Adapters

This directory contains thin integrations for coding-agent runtimes.

Current adapters:

- `claude-code/`
- `codex/`
- `pi/`

## Design rule

Keep the adapter thin:

```
native event
  ↓
canonical event
  ↓
scripts/enforcement_kernel.py
  ↓
native response
```

Do not add governance decisions to a host adapter. The shared kernel is the only runtime
enforcement decision point.

## Install

From the Skill/repository root:

```bash
python scripts/install_host_adapter.py --repo . --host auto --apply
```

`auto` installs only the adapter for the currently running agent when the runtime can be
identified. Explicit `--host` remains available for deterministic provisioning. Use
`--host all` when building a repository intended to be opened by multiple agents over time.

## Add a new host

Create:

```
hosts/<agent>/
  README.md
  <adapter template>
```

Then update:

```
references/host-capabilities.yaml
references/host-adapter-contract.md
scripts/install_host_adapter.py
tests/
```

The adapter should translate:

- session lifecycle → `session_start`
- prompt boundary → `prompt_submit`
- mutation preflight → `pre_tool`
- mutation result → `post_tool`
- external change observation → `file_changed` when available
- subagent lifecycle → `subagent_start` / `subagent_stop`
- completion boundary → `stop`

## Capability degradation

A host may lack one of these events. Do not silently turn a required gate into advisory
behavior. The canonical state guard and CI must remain authoritative.

## Testing

At minimum test:

- event normalization;
- deny/block mapping;
- additional-context mapping;
- session/bootstrap behavior;
- completion continuation;
- failure behavior when the kernel cannot be executed;
- host switching without reinitializing project governance state.
