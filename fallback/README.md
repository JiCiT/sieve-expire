# Independent no-op fallback

Accepts require ["expire"] and the existing expire/expunge duration syntax and
version0 bytecode operations EXPIRE=0, EXPUNGE=1. Stores/enforces no new deadlines,
performs no mailbox/index maintenance, and never cancels implicit keep. Other
Sieve actions retain ordinary semantics. No backfill for ignored requests.

Native target path: /usr/lib/dovecot/modules/sieve/lib90_sieve_expire_fallback_plugin.so.
Select it through sieve_plugins, never alongside normal common/Sieve/IMAP modules.
Normal-only configuration must be removed when its parser is absent. The public
installer handles mutually exclusive selection and complete process replacement.

The Makefile is independent of normal-project sources/libraries/settings. Build
with matching Dovecot/Pigeonhole headers/libraries; see docs/DEVELOPMENT.md. Existing
stored deadlines remain and normal reactivation can enforce them. External
COPY/MOVE propagation while fallback hooks are absent is not provided/qualified.
Original fallback code is CC0; upstream library/header conditions remain.
