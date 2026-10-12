# sieve-expire

**v0.1.0 — public preview for one exact Docker Mailserver ARM64 target.**

sieve-expire lets you set rules for moving messages to Trash and permanently
deleting them later. You choose those rules in your Sieve mail-filtering script,
using independent expiry and expunge deadlines:

```sieve
require ["expire"];
expire "30d";
expunge "180d";
```

In this example, `expire "30d"` makes a message eligible to move to Trash after
30 days; `expunge "180d"` independently authorizes permanent deletion after
180 days, regardless of folder. Each deadline is calculated when its Sieve
command executes. Enforcement may occur later, when mailbox activity triggers
maintenance. If both deadlines are due, expunge takes precedence. Expiry uses
an eligible, unambiguous special-use Trash; if the message is already in eligible
Trash, expiry does nothing. MOVE preserves both deadlines without resetting them.
These illustrative durations are not a policy installed by this project.

## Before installation

Read [compatibility](docs/COMPATIBILITY.md) and [installation/recovery](docs/INSTALLATION.md).
v0.1.0 supports **only** the exact qualified DMS 16.0.1 ARM64 (`linux/arm64`)
image and corresponding Debian Dovecot/Pigeonhole build. Matching version numbers
alone do not establish compatibility; the installer checks the relevant fingerprints.
Python3, OpenSSL, rsync and compatible Docker Compose are required. No compiler,
Dovecot development knowledge, image rebuild or startup download is required.

Installation briefly stops and recreates DMS, preserving its image and persistent
mail/configuration. **Default installation enables account-wide maintenance.**
Valid existing deadlines may be enforced immediately; committed expunge is
irreversible through software rollback. There is no per-account dry-run/canary.
Bulk multi-save/COPY/MOVE deadline propagation is unqualified in this preview.
No existing Sieve scripts are changed and no deadlines are assigned by installation.
Messages without deadlines remain unaffected. Confirm no outside process writes
mail/state while DMS is stopped and protect the installer's recovery checkpoints.

## Download and install

The following assets become available only after approved publication at
[JiCiT/sieve-expire releases](https://github.com/JiCiT/sieve-expire/releases).
Download the versioned installer archive and `SHA256SUMS`; place them in a new
empty directory. Verify its checksum before extracting:

**Recommended initial installation:** use `--no-maintenance` to make the Sieve
actions available while keeping maintenance disabled.

```sh
sha256sum -c SHA256SUMS
# Continue only if the expected archive reports OK.
tar -xzf sieve-expire-v0.1.0-dms16.0.1-deb13u6-linux-arm64.tar.gz
cd sieve-expire-v0.1.0
./install.sh --help
sudo ./install.sh --no-maintenance --container YOUR_DMS_CONTAINER \
  --test-recipient YOUR_EXISTING_LOCAL_MAILBOX
sudo ./install.sh --status --container YOUR_DMS_CONTAINER
```

The test recipient must be an existing local FILE account whose policy retains
harmless acceptance mail without redirecting it externally. Do not invent an
address. `sudo` is appropriate for a rootful daemon; with rootless Docker, run
as its owner with the required filesystem permissions and select its local socket.
The installer prompts before changes. Do not run downloaded remote scripts directly.

Omitting `--no-maintenance` enables maintenance by default and may immediately
enforce stored deadlines, including irreversible expunge. Later activation uses
`--enable-maintenance` with the same target/recipient; it rechecks compatibility
and qualification. Repeated installation verifies the
current state and avoids recreation when nothing needs changing. See the guide
for status, repair, uninstall, recovery, cleanup, reports and optional notification.

**Checksums detect corruption, not independently authenticated origin.** An
attacker replacing both the archive and checksum can defeat that check. No detached
archive signature is supplied. Obtain both through the intended GitHub repository.
The existing installer signature authenticates the payload after code begins
running; it does not authenticate the outer archive before execution.

## Behavior and qualification

Maintenance is opportunistic on genuine eligible IMAP activity, with a native
300-second start cooldown and roughly five-second cooperative budget. It is
not a periodic timer and individual operations may take longer. Only ordinary
directly owned private physical Maildir mailboxes are supported. No Trash is
created; ambiguous/missing destinations cause conservative skips.

Read [semantics](docs/SEMANTICS.md), [architecture](docs/ARCHITECTURE.md),
[qualification](qualification/SUMMARY.md), and [known limits](docs/COMPATIBILITY.md).
The qualified worker moves one message per transaction pair. Bulk multi-message
COPY/MOVE deadline propagation, enabled hibernation, other platforms/backends and
power-loss durability remain unqualified.

## Developers

[Build and testing instructions](docs/DEVELOPMENT.md) describe a public-input,
matching-header build and disposable rootless integration tests. A Git clone
contains source; qualified binary payloads are release assets. Building locally
produces new artifacts and does not automatically grant release qualification.
[Release preparation](docs/RELEASING.md) distinguishes packaging from qualification.

## About this project

sieve-expire was developed through a collaboration between a human project designer and OpenAI's Codex. The project's requirements, behavior, and design decisions were directed and reviewed by a human, while the implementation code was generated by AI.

The original sieve-expire code is made available under CC0 1.0 Universal, with the intention that anyone may freely use, study, modify, and redistribute it. Third-party components retain their respective licenses.

**Contributions are welcome!** If you find sieve-expire useful, fix a bug, or develop an improvement, please consider sharing your changes with the community through a pull request. There is no obligation to do so, but your contributions can help make the project better for everyone.

See [LICENSE](LICENSE) for the complete CC0 legal text and
[third-party notices](THIRD-PARTY-NOTICES.md) for upstream exceptions and
the texts in [LICENSES/](LICENSES/). CC0 applies to original project material,
to the extent the project's rights holder has rights to dedicate; it does not
relicense upstream code or dependencies. CC0 adds no requirement to publish
modifications or submit a pull request, including for hosted use. Applicable
third-party obligations remain in force. CC0 does not grant patent or trademark
rights and supplies no warranty.
