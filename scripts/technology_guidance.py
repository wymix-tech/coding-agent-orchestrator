"""Shared Agent guidance for technology choices; does not infer user authorization."""


def technology_instruction(command: str) -> str:
    return (
        "For a new provider installation, choose/install the provider and restart the host BEFORE asking technology questions. "
        "After restart restore the original request, then clarify only unknown stack choices once. "
        "Technology clarification comes BEFORE committing technology choices to requirements, native plans or scaffolding. "
        "Restore the existing Work, decisions and handoff after a host restart. Reuse choices whose original authorization is recorded; do not ask again merely because the host/session changed. "
        "Distinguish user-specified choices, an existing product stack supported by repository evidence, explicit user delegation, and an Agent proposal. "
        "For an existing product, preserve its established stack; ask before a material stack change. Host/SDD installations and caches are not product-stack evidence. "
        "For a new project, honor choices already specified by the user; ask the user ONE bundled question about only the remaining material language/framework/build/test/storage or delivery choices. "
        "Offer a concrete recommendation as a proposal and WAIT for the user's answer before selecting it. If unable to ask, report TECH_STACK_CLARIFICATION_REQUIRED and stop stack-dependent planning, manifests, wrappers and implementation. "
        "Explicit delegation such as 'you choose' authorizes selection only within its stated scope; choosing an SDD provider, restarting a host or saying 'continue' is not delegation. "
        "An Agent-written requirement, SPEC, manifest or decision saying 'confirmed' is not user authorization; generated files for this request cannot retroactively authorize the stack. "
        "Work registration may proceed with unknown choices marked pending; do not fill them with invented defaults. "
        f'Once a Work exists and the choice is authorized, record it with `{command} decision --scope technology --decision "<actual choice>" --reason "<user-specified|repository-established|user-delegated; actual user quote or pre-existing product paths; delegation scope>"`. '
        "Keep proposals pending and preserve their status in the handoff; never relabel them as user-confirmed. "
    )
