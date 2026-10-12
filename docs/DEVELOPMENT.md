# Building and isolated tests

Original code is CC0; upstream macros/build helpers retain their notices. Native
sources remain standalone, not a Dovecot/Pigeonhole fork. Build inputs and source
checksums are recorded in `qualification/native-build.json`.

## Native build from a fresh checkout

Use an ARM64 Linux host and an isolated rootless daemon. Build/test commands must
never point at real mail or an operational daemon. Example ROOTLESS_SOCKET is a
local socket belonging to your disposable development daemon.

```sh
export DOCKER_HOST="unix://$ROOTLESS_SOCKET"
python3 -B tools/fetch-build-inputs.py --output tools/build-inputs
docker build --pull=false -f tools/Dockerfile.build -t sieve-expire-builder:v0.1.0 .
mkdir build-output
docker run --rm --network none \
  -v "$PWD:/source:ro" -v "$PWD/build-output:/output" \
  sieve-expire-builder:v0.1.0 \
  /source/tools/build-native.sh /source /output
```

Fetch uses public Debian URLs and exact reviewed SHA256/size pins, including the
matching development package and source/patch archives. Builder uses the pristine
immutable DMS base, holds runtime packages, and installs native tools/matching dev
headers. Vendored run-test.sh.in completes Autoconf bootstrap. No private builder,
workbench, diagnostic helper or archived compiled output is required. Standard
build tools may evolve; the recipe provides a repeatable build, not a promise of
byte-identical results across different toolchains/build paths. Two clean builds
were byte-identical under the recorded public builder/path conditions. Debug paths affect
hashes. A new compiled module is not the accepted binary by assumption.

The script builds normal and fallback separately in an output copy. Direct native
builds can use autoreconf -fi, the same configure flags, make, and make -C fallback
with matching packages/headers. Runtime install places common bytes in both core
and Sieve locations plus companion/settings; ordinary make install alone does not
replace the full public installation/verification process.

## Controls and existing-payload verification

A source clone intentionally ignores generated `installer/release/` binary payloads.
For tests against the accepted release, use the downloaded archive's payload:

```sh
SIEVE_EXPIRE_TEST_RELEASE=/absolute/extracted/installer/release \
  python3 -B -m unittest discover -s installer/tests -p test_controls.py
python3 -B -m unittest discover -s dms-installation/tests -p model.py
```

The controls verify signed source bindings, tamper/qualification refusals, safe
path/cleanup/report handling, directory comparison, space/access and metadata.
Some negatives mock host predicates; they are not rootful runtime proofs.
The public read-only verifier accepts --release, verifies its signature/current
installer components/public records/native source hashes, and never signs or
qualifies an arbitrary rebuild. Use the same public source release as the payload.

## Disposable DMS integration

Runtime harness uses only the established rootless socket and synthetic .test
accounts. It generates a random password and private short-lived TLS key at runtime;
these outputs must never be published. New container name and empty output required:

```sh
export SIEVE_EXPIRE_TEST_OUTPUT=/absolute/new/private/test-output
export SIEVE_EXPIRE_TEST_NAME=sieve-expire-preview-test-unique
export SIEVE_EXPIRE_TEST_RELEASE=/absolute/extracted/installer/release
python3 -B installer/tests/runtime.py setup
python3 -B installer/tests/runtime.py metadata
python3 -B installer/tests/runtime.py activation
python3 -B installer/tests/runtime.py uninstall
```

Ordered phases cover actual delivery/index fields, worker movement/expunge, TLS
IMAP, preservation and full recreation. The fault harness can then run
verification_failure, startup_failure, failed_rollback and interrupted using the
same environment after normal installation is restored. These use test-only method
substitution/SIGKILL, never a product bypass. Retain named result reports and inspect
actual persisted Message-ID/hash/UID/deadline data. Additional public phases lifecycle/negatives cover owned repair/cleanup and
actual tamper/config refusals without any signing key. Distinct-release upgrade
and valid signed non-PASS fixtures retain prior evidence; no new claim is made
without an appropriate independently qualified alternate release. No private key
is required to run these public tests.

Do not overwrite outputs or automatically delete checkpoints/unrelated resources.
Keep test mail/config/TLS/keys and command logs outside Git. Worker policy remains
one-message MOVE, source/destination reconciliation and cooperative safe boundaries.
