# Workflow acquisition policy

All literature sources use **Zotero Connector first** for new retrieval. Search
APIs discover candidates; locate the actual article page before invoking the real
extension. Use `instsci acquire` or the topic-library entry, with normal source
downloaders as explicit fallbacks. Never emulate Connector browser activity by
posting fabricated connector sessions or bibliographic payloads.

Read back the real Zotero parent, child PDF and actual bytes. Reuse complete
items. If only metadata exists, carry its parent key to attachment-only fallback;
do not create a second parent. A pending/uncertain save pauses retries until its
state is inspected. Extension absence or an unusable translator is an explicit
capability failure, not a successful plugin call.

Keep institution login and human verification in a visible browser, respect the
user's institution and existing Chinese-portal quota ledger. Never read internal
credential tables, export browser cookies, alter authentication or restart live
apps to make a test pass. See `docs/CONNECTOR_FIRST.md` and `InstSCI/AGENTS.md`.
