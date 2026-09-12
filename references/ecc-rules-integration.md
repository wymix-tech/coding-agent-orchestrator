# ECC Rules Integration

V6.1 can treat an installed/local ECC `rules/` tree as an **external guidance source**.

## Why only a source

ECC's rule model has useful properties for this project: common rules plus language/framework packs, path-scoped rule metadata, and a clear split between broad rules and on-demand skills. V6 adopts those structural ideas but keeps project Engineering Policy authoritative.

External ECC rules:

- may be discovered and routed into context;
- may provide defaults/checklists/reference material;
- MUST NOT automatically become a blocking project gate;
- MUST NOT lower or override project `MUST` rules;
- are promoted to blocking behavior only when the repository creates an explicit local policy/enforcement mapping.

## Adapter

```bash
python scripts/ecc_rules_adapter.py .claude/rules/ecc \
  --files src/main/java/com/acme/web/UserController.java \
  --packs common java
```

The adapter returns a catalog with rule paths and matching metadata, without copying the full rule bodies into the active context.

## Recommended setup

For a Java project, install/select only ECC `common` plus `java` (and a framework pack when actually relevant). Avoid treating a full multi-language rule tree as always-on prompt context.

## Precedence

```text
project-specific machine policy
  > repository/framework policy
  > language/common project policy
  > ECC external guidance/defaults
```

This follows the orchestrator principle that external tooling can contribute context/evidence, but governance authority remains in the project policy layer.

## Attribution

ECC (`affaan-m/ECC`) is MIT-licensed. This package does not redistribute ECC rule bodies; it provides an adapter and an integration pattern. See `THIRD_PARTY_NOTICES.md`.
