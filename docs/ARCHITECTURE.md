# Implementation architecture

The common storage/Sieve module is service independent. The IMAP companion owns
command/lifecycle hooks and deferred owner-loop callbacks. The settings module
provides native configuration parsing before runtime selection. Initial supported
backend is the tested physical private Maildir shape.

Delivery stages independent deadlines as a Sieve store side effect, FORCE_RESYNCs
its destination before the actual save transaction opens, and associates the full
record with the transaction append sequence. Commit-pre writes using that mailbox's
own index-extension registration. Sequence numbers are view/transaction-local,
not durable UIDs. Legacy/current lookup uses valid Dovecot output storage.

Maintenance tracks per-user pending/running/teardown, existing storage ownership
and compatibility. Deferred work requires a live matching client/user/loop, no
incompatible command/storage/transaction locks or unknown suspended work. Normal
entry requires an empty command queue. Only the verified sole core IDLE WAIT_INPUT
shape admits a narrow exception after its sync context clears. Successful hibernation
cancels work; restoration alone is not an opportunity. No worker loop pumping.

The lock suppresses duplicate work, not normal writers or cross-mailbox atomicity.
Under it, optional native cooldown state is checked/updated at admitted start.
Per-user in-memory fairness can continue later; no persistent traversal index/cursor.
Index deadline discovery scans records, not message bodies.

Before destructive MOVE/expunge, independently open source and successfully
FORCE_RESYNC without FAST/TRYLOCK, then resolve mailbox/UIDVALIDITY/UID and reread
current deadlines in the authoritative view/transaction. Earlier hints are discarded.
MOVE also FORCE_RESYNCs destination before save. One message per transaction pair:
destination EXTERNAL|ASSIGN_UIDS, source REFRESH, mailbox_move, destination commit
first, rollback source on destination failure, source commit, source EXPUNGE sync,
full resource cleanup. Both deadlines propagate through the common copy hook.

Internal mailbox API moves do not thereby trigger IMAPSieve learning. imap_sieve
is allowed by exact plugin name only; other safe-entry gates remain. The worker
implements expiry-in-Trash no-op and independent expunge precedence, not Trash reset.

Publication keeps the qualified host/startup manager source. Its previously imported
private deployment module is reduced to the same verbatim directory-bind comparison
and supporting definitions. Removing unrelated code changes its authenticated hash;
publication packaging/install/recovery tests cover that dependency substitution.
Native C/H/binaries remain unchanged. No production policy/deployment authority
is implied by technical release qualification.
