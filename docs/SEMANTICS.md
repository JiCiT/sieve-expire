# Deadlines and maintenance policy

`require ["expire"]` enables distinct EXPIRE=0 and EXPUNGE=1 operations.
Durations use existing suffixes s/m/h/d/w, with nonnegative seconds by default;
there is no +duration syntax. Actions stage independent `expire_at`/`expunge_at`
values and do not cancel keep or discard delivery merely because a deadline is due.

```text
if expunge_at != 0 and expunge_at <= now: ordinary expunge
else if expire_at != 0 and expire_at <= now: attempt expiry
else: nothing
```

Expiry already in an eligible special-use Trash is satisfied: no change. Otherwise
count existing/selectable eligible Trash in the complete source private namespace
catalog. Exactly one is used; multiple means skip without fallback. With none,
only the canonical private namespace owning configured INBOX may supply its sole
eligible Trash. No creating/subscribing or guessing by name/prefix/ordering.
Due explicit expunge needs no Trash lookup. Both fields follow MOVE unchanged.

Opportunities arise from genuine external eligible mailbox activity. Worker-created
access does not arm more work. Work is deferred to the guarded IMAP owner-loop
boundary; a busy/unsafe opportunity is consumed, not repeatedly polled. The current
model is opportunistic rather than scheduled. Cooldown300 is start-to-start across
connections/processes for the account, admitted under the nonblocking lock. Lock
contention or rejected opportunities do not reset it. Native cooldown default0 adds
no sidecar-state I/O. Deadline comparisons use Unix wall time; cooperative elapsed
budget uses CLOCK_MONOTONIC. Stop only at completed operation boundaries.

Legacy `sieve-expire` stores one deadline; current `sieve-expire-v2` stores both.
No separate sieve-expunge extension. The no-op fallback accepts existing syntax
and version0 bytecode, but stores/enforces no new deadlines and does not backfill
ignored requests. Existing stored fields remain, and normal reactivation may enforce them.
