# Qualified behavior and publication scope

The original public installer completed19 scoped acceptance criteria in disposable
rootless DMS environments. These prior results apply to unchanged native bytes and
unchanged installer paths; publication-specific evidence supplements the modified
helper dependency, public build/tests and packaging. Raw diagnostics, mail, keys,
configuration and backups are retained privately rather than published.

| # | Accepted installer criterion | Prior result |
| --- | --- | --- |
| 1 | Release qualification establishes A/B/C/D | PASS |
| 2 | Actual Gate D movement/expunge | PASS |
| 3 | Qualified artifacts/manifests | PASS |
| 4 | No user development qualification | PASS |
| 5 | Default enables expiration/expunge | PASS |
| 6 | Unqualified/incompatible cannot enable | PASS |
| 7 | Metadata-only / enable transition | PASS |
| 8 | Repeat idempotent | PASS |
| 9 | Interrupted recovery | PASS |
| 10 | Upgrade / repair | PASS |
| 11 | Accurate status | PASS |
| 12 | Safe uninstall and Sieve compatibility | PASS |
| 13 | Owned cleanup only | PASS |
| 14 | Protected recovery backups | PASS |
| 15 | Failure / rollback | PASS |
| 16 | Console / machine reporting | PASS |
| 17 | Public docs | PASS |
| 18 | Isolated acceptance | PASS |
| 19 | Production untouched | PASS |

A/B: exact ABI/loading/config/native distinct Sieve operations, correct real LMTP
message metadata and fallback bytecode. C: persistent startup/recreation, failures,
repair/interruption and software recovery. D: actual persisted one-message MOVE,
independent expunge/precedence, already-Trash no-op, multiple private namespaces,
Trash catalog/ambiguity, source/destination reconciliation, lock/budget/cooldown.
These release-behavior gates are distinct from human authorization to modify
any operational mail system.

All19 prior criteria passed within the declared scope; no broader coverage is
inferred. Focused public-source control tests originally total19; host permission/
space negatives include mocked predicates. Isolated runtime additionally used
real LMTP/index/Message-ID/hash evidence and authenticated TLS IMAP. Signed byte
identity and public source correspondence are recorded in the public manifest and
native-build.json. This is not a claim that a locally rebuilt binary passed the
same suite. See accepted-release.json for original exact-binary provenance.

Limitations: exact supported image/packages/arm64 and ordinary private physical
Maildir only. Bulk multi-message propagation, enabled hibernation, other platforms/
backends, exhaustive races, power-loss, production performance and independent
clean-daemon restore of the installer checkpoint remain unqualified. Cached-image-
missing rollback loading and every external network/auth route are not newly tested.
Budget is cooperative; late source failure may leave duplicates; no Trash reset.
Source scripts/deadlines persist; activation can affect legacy/preexisting values.

Publication build/package/runtime results are recorded separately before release
selection. A matching upstream runtime string does not establish ABI identity.

## v0.1.0 publication verification

The public archive/source preparation passed19 installer controls and11 startup/
hook controls. It independently built four AArch64 modules from public exact
inputs, including a source-only clone with no payload or private build history.
Two clean builds were byte-identical in the recorded builder/path conditions;
rebuilt modules were not substituted or claimed runtime-qualified.

The extracted archive passed fresh metadata-only/default installation, enable,
status/repeat without recreation, clean stop/start, LMTP/authenticated TLS IMAP,
actual MOVE/expunge with both fields unchanged on the intended message, Trash/
neighbor preservation, fallback/full removal, repair, pre-shutdown refusals and
software rollback preserving newer mail. Startup failure, deliberately unavailable
recovery plus ordinary resume, and actual staged SIGKILL plus ordinary resume passed.
See [PUBLICATION-RESULTS.json](PUBLICATION-RESULTS.json) for sanitized dispositions.

The runtime dependency change removed unrelated private deployment code and retained
its directory safety definitions verbatim. Its source hash changed, requiring a
new authenticated manifest; existing signing mechanism/key and native module bytes
remain. Relevant public control/installation/recovery paths were reverified. No
new archive signature or production/publishing action occurred.

Known reporting limitation: cleanup-only lacks a health probe and may display a
generic failed/unresolved state label; follow it with status for actual health.
The underlying manager behavior was preserved rather than redesigned. All earlier
NOT TESTED limitations remain. Outer checksums alone do not establish origin.
