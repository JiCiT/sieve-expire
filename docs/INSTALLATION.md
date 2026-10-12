# Installation, verification and recovery

Use the complete versioned archive. The host manager installs an offline payload
in the existing writable DMS configuration bind mount and chains existing
administrator user-patches. It does not rebuild DMS, alter retention scripts, or
change unrelated services. It recreates only DMS with the exact existing image
and `--no-deps --force-recreate --pull never --no-build`.

## Prerequisites and target selection

See [compatibility](COMPATIBILITY.md). Host tools: Python3 standard library,
OpenSSL, rsync with HAX support, Docker and Compose supporting `!reset`,
`--pull never`, `--no-build`, `--force-recreate` (qualified Docker29/Compose5).
Use an account authorized for the selected local Unix Docker daemon and able to
preserve config ownership/metadata. Configuration must be a physical writable
bind directory mounted at `/tmp/docker-mailserver`; mail/state may be bind
folders or named volumes. The exact image must be cached. Remote daemons are refused.

Discovery accepts one unambiguous DMS Compose target. Specify `--container` when
needed, repeated `--compose-file` for original Compose inputs, and `--docker-host`
for a local socket. Original inputs must match running mounts/access/environment/
restart/entrypoint. No symlink canonicalization or arbitrary migration is attempted.

Before changing anything, establish that no external host process writes the
stores while DMS is stopped. Other-container writer checks do not prove host-writer
absence. Review existing deadlines: default/enable can enforce them immediately.

## Commands

Run `./install.sh --help` for exact interfaces. State-changing commands require
an existing `--test-recipient` whose script retains harmless local mail:

- Default: install, upgrade, repair or verify; enable maintenance if authenticated.
- `--no-maintenance`: normal metadata-only selection; complete DMS recreation when changing state.
- `--enable-maintenance`: safely activate an existing compatible normal installation.
- `--status`: inspect current native/config/selection/qualification/health state; no test mail.
- `--uninstall`: fully remove owned integration if supported Sieve source closure proves safe; otherwise retain the no-op fallback.
- `--uninstall --remove-fallback`: require reliable proof that complete removal is safe, otherwise refuse.
- `--keep-artifacts`: retain owned temporary resources for investigation.
- `--cleanup`: remove only positively owned temporary resources; retain reports and recovery backups.
- `--report FILE`: write an additional protected JSON report to an existing nonsymlink parent.
- `--notification-mailbox ADDRESS`: optional existing local startup-degradation recipient.

`--yes` accepts the existing-deadline and exclusive-writer responsibilities without
an interactive prompt. Do not use it until those responsibilities are understood.

## Installation order

The manager authenticates the payload and component hashes, checks the exact
image/package/architecture/runtime fingerprints, native configuration and effective
plugin/hibernation gates. It parses future persistent configuration prospectively.
Disk space and original-image preservation are checked before stopping DMS.

With DMS stopped, it copies config using `rsync -aHAX --numeric-ids`. Verification
requires exit0 AND empty difference output. Offline read-only exact-image helpers
copy and compare mail/state content/metadata, including ownership, modes, mtime,
xattrs/ACL representation, hardlinks, symlinks, FIFO/socket nodes. Socket nodes are
not resumable sessions. DMS stays stopped through package/hook staging.

The native settings parser is installed before configuration using its key is
parsed. Normal installation uses all four paths:

```text
/usr/lib/dovecot/modules/lib90_sieve_expire_plugin.so
/usr/lib/dovecot/modules/sieve/lib90_sieve_expire_plugin.so
/usr/lib/dovecot/modules/lib91_imap_sieve_expire_plugin.so
/usr/lib/dovecot/modules/settings/libsieve_expire_settings.so
```

The profile is native IMAP cooldown300; zero disables cooldown, not maintenance.
The independent fallback belongs only at
`/usr/lib/dovecot/modules/sieve/lib90_sieve_expire_fallback_plugin.so`.
Normal/fallback selections are mutually exclusive. The startup hook runs on
fresh/recreated DMS startup, not as a runtime monitor or every ordinary restart.

Verification covers installed hashes, parser/config, distinct Sieve bytecode,
Dovecot/Postfix, native IMAP and harmless LMTP receipt. These native checks are
not a guarantee of every external TLS/auth route; isolated qualification also
used authenticated TLS IMAP. Successful repeat avoids unnecessary recreation.

## Reports and recovery

Reports identify actual selection, metadata/enforcement, compatibility, health,
backup/rollback, cleanup and action required. Exit0 means requested state verified;
2 means refusal/failure, 3 incomplete recovery, 130 interruption. A failed install
with successful rollback still reports failure and the restored state.

Cleanup-only invocations do not run health checks. Their generic `operational_state`
may say failed-or-partially-completed even when cleanup passed; this is an
existing reporting limitation, not proof of a stopped service. Follow cleanup
with `--status` for authoritative installed selection and health.

Private `<CONFIG>/.sieve-expire-manager/` contains journal, per-run JSON/diagnostics,
original image/Compose identity, cold config/mail/state inventories and protected
checkpoints. These can contain secrets: do not commit or upload them unredacted.

Recovery is software-first. It restores only positively owned integration inputs
and recreates original software; it NEVER automatically overwrites current mail/state
with earlier data. Mail received after the checkpoint is preserved. External edits
can make recovery unsafe; preserve current data and the report/journal, resolve the
stated problem, and rerun. An interrupted staged operation is recovered before new work.

If recovery remains incomplete, stop automatic attempts and review the protected
original Compose/image inputs. Older mail restoration requires a separate deliberate
administrator decision assessing post-checkpoint loss. Expunge cannot be reversed
by pause or software rollback. No cross-mailbox atomicity/exactly-once MOVE is promised.

Emergency pause: invoke `--no-maintenance` and verify metadata-only status. Settings
parser/common support remain installed; simply setting cooldown0 does not pause work.
Complete removal preserves user scripts, mail and existing metadata; dependent or
unknown scripts retain qualified fallback, which ignores both deadline actions.

Checkpoints support same-host software rollback, not universal disaster recovery.
External readonly TLS/timezone dependencies must remain available and are not copied.
Independent clean-daemon restoration, cached-image-missing loading and power-loss
branches are not newly qualified. Backups are never automatically deleted;
reports warn after more than5 and refuse insufficient space. Review retention manually.

Corrupt/future/unreadable cooldown sidecars fail closed for maintenance, while
ordinary mail continues. Pause, preserve the affected account's performance sidecar,
review clock/permissions, and recover it deliberately. Do not delete mail/indexes or
lock parents. Removing only the sidecar permits a fresh admitted pass; it does not
repair historical wrong-message deadline metadata.
