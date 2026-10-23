"""Shared Agent guidance for technology choices; does not infer user authorization."""


def technology_instruction(command: str) -> str:
    return (
        "For a new provider installation, choose/install the provider and restart the host BEFORE asking technology questions. "
        "After restart restore the original request, then clarify only unknown stack choices once. "
        "Technology clarification comes BEFORE committing technology choices to requirements, native plans or scaffolding. "
        "Restore the existing Work, decisions and handoff after a host restart. Reuse choices whose original authorization is recorded; do not ask again merely because the host/session changed. "
        "Distinguish user-specified choices, repository-established product facts, user-delegated choices, and an Agent proposal. "
        "For an existing product, preserve its established stack; ask before a material stack change. Host/SDD installations and caches are not product-stack evidence. "
        "For a new project, honor choices already specified by the user; ask the user ONE bundled question about only the remaining material language/framework/build/test/storage or delivery choices. "
        "Offer a concrete recommendation as a proposal and WAIT for the user's answer before selecting it. If unable to ask, report TECH_STACK_CLARIFICATION_REQUIRED and stop stack-dependent planning, manifests, wrappers and implementation. "
        "Explicit delegation such as 'you choose' authorizes selection only within its stated scope; choosing an SDD provider, restarting a host or saying 'continue' is not delegation. "
        "An Agent-written requirement, SPEC, manifest or decision saying 'confirmed' is not user authorization; generated files for this request cannot retroactively authorize the stack. "
        "Project background must be saved before starting a product Work; do not fill unknown choices with invented defaults. "
        f'Before Work creation, inspect `{command} project inspect`, then save authorized fields using `{command} project context set --source <source> --evidence <observed-evidence-id-or-product-path>`. '
        "Project technology is not a Work decision. Existing tasks inherit the accepted background; do not repeat selection. "
        "Keep proposals pending and preserve their status in the handoff; never relabel them as user-confirmed. "
    )
