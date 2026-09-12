# Spring Boot Policy Enforcement

For layered Spring Boot projects, use the V6 starter policy for routing/context and use ArchUnit as the preferred deterministic dependency-direction gate.

## Canonical direction

```text
web/controller -> service -> dao/repository
```

The exact package names are project policy. Adjust `.orchestrator/policies/manifest.yaml` instead of teaching each Agent a different convention.

## ArchUnit example

Adapt package patterns to the repository:

```java
@AnalyzeClasses(packages = "com.acme")
class LayerArchitectureTest {

    @ArchTest
    static final ArchRule layers = layeredArchitecture()
        .consideringAllDependencies()
        .layer("Web").definedBy("..web..", "..controller..")
        .layer("Service").definedBy("..service..")
        .layer("DAO").definedBy("..dao..", "..repository..")
        .whereLayer("Web").mayOnlyAccessLayers("Service")
        .whereLayer("Service").mayOnlyAccessLayers("DAO")
        .whereLayer("DAO").mayNotAccessAnyLayer();
}
```

Treat this as a project example, not a universal architecture law. Some codebases use domain/application/infrastructure, ports-and-adapters, CQRS, or generated clients; encode the repository's actual intended boundaries.

## V6 lightweight checker vs ArchUnit

`scripts/policy_engine.py evaluate` detects direct Java imports for changed files. It is deliberately conservative and cheap. It does not replace Java type resolution.

Use it for fast feedback, then require ArchUnit (or an equivalent project architecture test) as the authoritative build/CI gate when layering is a `MUST`.

```text
Policy Router
   -> exact layered rules
   -> lightweight changed-file check
   -> ArchUnit gate
   -> V5 quality gate evidence
```

## Responsibilities

Dependency direction is mechanically enforceable. Responsibilities such as “business logic belongs in service/domain code” are harder to prove structurally, so route them to code review/static analysis until the project has a reliable custom rule.
