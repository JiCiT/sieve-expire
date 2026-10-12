# Supported environment and limits

v0.1.0 is a public preview, qualified only for:

| Item | Requirement |
| --- | --- |
| DMS | v16.0.1, Linux ARM64 |
| Exact pristine image | `sha256:d0fe7668defe157aad57ea31b1707ad1e2fb57d7a91bdf17cbdf876549946c86` |
| Dovecot/Pigeonhole packages | `1:2.4.1+dfsg1-6+deb13u6` |
| Dovecot runtime | `2.4.1-4 (7d8c0e5759)` |
| Pigeonhole runtime | `2.4.1-4 (0a86619f)` |
| Storage/accounts | Ordinary directly owned private physical Maildir/maildir++ FILE accounts |
| Integration | Local Compose target, physical writable bind config, bind or named-volume mail/state |
| Host tools | Python3, OpenSSL, rsync HAX, Docker/Compose (qualified Docker29/Compose5) |

Image ID, package revision and five native fingerprints in the signed payload
must match. Neither `latest` nor the displayed upstream runtime version proves
compatibility. The installer refuses other images/backends/architectures; source
availability is not a promise that their binaries are compatible.

Shared/public, virtual and alias identities are excluded. Enabled IMAP hibernation
and unknown asynchronous plugins are refused. imap_sieve/quota/imap_quota coexistence
has scoped evidence. No automatic timestamp reset occurs on Trash entry.

Unqualified: bulk multi-save COPY/MOVE propagation; enabled hibernation; power-loss
and exhaustive races; other targets/backends; production performance or latency SLA;
independent clean-daemon recovery of installer checkpoints; cross-tree hardlinks;
all possible startup-degradation races and arbitrary external service routes.
The approximately five-second worker budget is cooperative. One operation may take
longer, blocking IMAP; delivery FORCE_RESYNC can also add LMTP latency.

Destination-first MOVE may leave a duplicate if source commit fails after the
copy committed. Maintenance uses one message per transaction pair. Stored deadlines
persist after policy changes/removal and can be enforced at activation. No per-account
canary/destructive dry-run. Messages with no deadlines are unaffected.
