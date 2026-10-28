---
{
  "name": "react-features",
  "description": "Apply react-features when planning, implementing or reviewing the accepted project scope.",
  "metadata": {
    "cao": {
      "schema_version": 1,
      "rule_id": "react-features",
      "rule_version": 1,
      "stack": {
        "language": "typescript",
        "framework": "react"
      },
      "style": "features",
      "scope": "{{scope}}",
      "source_roots": [
        "{{source_root}}"
      ],
      "test_roots": [
        "{{test_root}}"
      ],
      "checks": []
    }
  }
}
---
# react-features

## Applicability
Use for accepted typescript react scopes using features. Preserve existing framework discovery and routing conventions. Adopt existing layouts explicitly; do not automatically migrate code.

## Directory tree
```text
{{source_root}}/
|-- features/orders/components/
|-- features/orders/hooks/
|-- features/orders/api/
`-- shared/ui/
{{test_root}}/ (mirror feature boundaries)
```

## Responsibilities
Put entry/protocol adapters in controller/router/handler/views/Web. Put use-case orchestration and transactions in service/application. Put business invariants in model/domain. Put persistence and external clients in repository/infrastructure/adapters. Frontend components own rendering, hooks/composables own local interactions, api owns transport. Keep framework-specific directories as shown; do not create irrelevant layers.

## Naming
ARCH-NAME-001: Add an order use case inside the order(s) feature. Name files after responsibility; avoid generic manager/utils dumps. Create only files needed by the requirement, with matching tests. Preserve the accepted namespace and framework filename conventions.

## Calls
Request -> protocol adapter -> use-case orchestration -> business model and storage port -> storage adapter. Frontend page -> feature components -> hooks/composables -> API client. Runtime calls do not determine static dependency ownership.

## Dependencies
ARCH-DEP-001: Protocol adapters must not directly access persistence implementations. Depend through application services. Domain/Model in clean or DDD styles must not import outer application, web or infrastructure layers. Infrastructure implements inward-owned ports. In conventional layered styles services may depend on repositories. Avoid circular feature dependencies.

## Data conversion
ARCH-DATA-001: Convert transport request/response schemas at the entry/application boundary. Keep persistence entities inside persistence adapters for clean/DDD styles. Map domain objects explicitly. Do not expose database credentials, internal entities or server-only objects in frontend responses.

## Module boundaries
ARCH-MOD-001: Call another feature through its public application contract or events. Do not access another feature's internal repositories. Shared code needs an identified cross-feature responsibility; preserve scope boundaries.

## Constraints
ARCH-STRUCT-001: Put new implementation under the accepted source roots and matching feature directories. Do not flatten all new business code into the source root. Do not create empty services or redundant interface/Impl pairs.

## Tests
Mirror feature responsibilities under the accepted test roots. Run the project's real build/type/load/test checks. Run architecture check for supported structural constraints. Provider review must assess responsibility, conversion and module boundaries; an import check alone cannot prove architectural correctness.

## Exceptions
Record a concrete rule ID, reason and affected paths in the native design/review. Resolve conflict with accepted project rules before implementation; exceptions never silently rewrite the project contract. Template upgrades require reviewed draft/diff/apply.
