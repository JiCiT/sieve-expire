# Release preparation and publication

v0.1.0 is a public preview for JiCiT/sieve-expire and one exact qualified target.
End users download the versioned installer archive rather than compile. The source
repository includes implementation/build/test tools; generated release payloads
and caches are excluded from Git. The installer archive includes corresponding
original source, upstream notices, docs/tools and the accepted binary payload.

## Integrity model

SHA256SUMS detects corruption. If checksum and archive come from the same compromised
source it does not independently authenticate origin. No new archive-signing key
or detached archive signature is introduced. Obtain releases through the intended
repository; do not run unreviewed remote scripts.

The existing RSA/SHA256 signature in installer/release/manifest.sig authenticates
binaries/startup/component hashes and qualification constraints, after installer
code starts. The pinned public key does not prevent replacement of the complete
installer and key. This is neither outer-archive authentication nor proof of latest
release/revocation. Private signer material is not shipped or required by users/tests.

The existing key originated as a development signer. Its continuing secure
custody/backup/rotation remains a maintainer responsibility; this preparation
does not create or replace it, store it in GitHub Actions, or publish it.

The public verifier and package-release.py validate already approved signed
artifacts; they cannot manufacture qualification for new binaries. Raw historical
reports stay private. Concise summaries distinguish unchanged exact-binary evidence
from current public packaging tests. New source/target/runtime changes require
relevant fresh qualification and reviewed manifest/component hashes. Never relabel
an unsigned editable PASS as qualified. Public package changes preserve all checks.

## Packaging an existing accepted payload

From a clean source tree containing its matching accepted installer/release:

```sh
python3 -B installer/qualification.py
python3 -B tools/package-release.py --output /absolute/new/release-directory
```

The checked-in public-files.json allowlist defines every archive member. The tool
refuses to overwrite a release and emits SHA256SUMS. No rebuilding, private-key
operation or remote action occurs. Exact identities are in qualification records.
Review licenses/helper notices and source completeness for the actual release.

A public source tag alone does not supply the generated installer/release payload.
The GitHub installer asset is the intended runnable distribution. Do not copy local
unqualified build output into that payload.

## Eventual GitHub publication (review first; not executed here)

Confirm the GitHub remote and inspect the sanitized allowlist/diff before adding.
Create the public repository only after separate approval. Suggested sequence:

```sh
git init -b main
# Set your reviewed public commit identity before committing (not private defaults).
git config user.name "YOUR_PUBLIC_NAME"
git config user.email "YOUR_PUBLIC_COMMIT_EMAIL"
git add .gitignore README.md LICENSE LICENSES THIRD-PARTY-NOTICES.md CHANGELOG.md CONTRIBUTING.md configure.ac Makefile.am src fallback m4 build-aux install.sh installer dms-installation deployment docs tools qualification
# Inspect staged paths and confirm ignored payload/caches/outputs are absent.
git diff --cached --stat
git status --short
git commit -m "Prepare sieve-expire v0.1.0 public preview"
git tag -a v0.1.0 -m "v0.1.0 public preview: qualified DMS16.0.1 ARM64 target"
# Run only after explicit publication authorization:
gh repo create JiCiT/sieve-expire --public --source=. --remote=origin
git push -u origin main
git push origin v0.1.0
gh release create v0.1.0 /absolute/releases/sieve-expire-v0.1.0-dms16.0.1-deb13u6-linux-arm64.tar.gz /absolute/releases/SHA256SUMS --repo JiCiT/sieve-expire --prerelease --title "v0.1.0 public preview" --notes-file docs/RELEASE-NOTES.md
```

If the repository already exists, use its reviewed origin instead of creating it.
The archive must be the exact tested checksum, not a regenerated substitute. An
annotated tag here is not a cryptographic signature or authorization to publish.
