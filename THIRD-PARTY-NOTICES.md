# Licensing and third-party notices

## Original project material

Original sieve-expire code is made available under **CC0 1.0 Universal**
(`CC0-1.0`). The complete legal text is in [LICENSE](LICENSE), with an identical
copy at [LICENSES/CC0-1.0.txt](LICENSES/CC0-1.0.txt).

The owner's provenance statement describes human-directed and reviewed design
with AI-generated implementation. The CC0 dedication applies only to rights
the project's rights holder has authority to dedicate; it does not assert that
every generated portion is independently copyrightable. No OpenAI, Codex, or
invented personal copyright claim is added.

The scope is original project-specific implementation and scripts, including
the normal plugin, independent fallback and installer. Upstream material below,
any other separately licensed material, and external software retain their own
terms. A directory containing both original and upstream files is not entirely
CC0 merely because the root LICENSE is CC0. Contributions are welcomed
voluntarily; this notice does not purport to dedicate another contributor's
rights without their agreement.

## Included upstream source

| Project file | Origin and applicable terms | Preserved text |
| --- | --- | --- |
| `m4/dovecot.m4` | Dovecot Autoconf macro; Copyright (C) 2010 Dennis Schridde. Its express permission permits copying/distribution with or without modifications provided the notice is preserved. | [LicenseRef-Dovecot-Autoconf-Permission](LICENSES/LicenseRef-Dovecot-Autoconf-Permission.txt) |
| Embedded `warnings.m4` section in `m4/dovecot.m4` | Copyright (C) 2008–2015 Free Software Foundation, Inc.; FSF all-permissive grant. The source also credits Simon Josefsson. | [FSFAP](LICENSES/FSFAP.txt) |
| `m4/dovecot-pigeonhole.m4` | Identical to `dovecot-pigeonhole.m4` in the retained Pigeonhole 2.4.1-4 source archive. Its file has no separate permission notice; upstream COPYING places material without exceptions under LGPLv2.1. | [LGPL-2.1-only](LICENSES/LGPL-2.1-only.txt) |

The macro notices remain in the source files verbatim. The local `LicenseRef`
records the actual Dennis Schridde wording without assuming an exact standard
SPDX match. The Pigeonhole macro is conservatively treated as LGPL-2.1-only;
the license text's example “or later” application notice is not itself a grant
to upgrade this upstream file. These files are not relabeled CC0.

The retained Debian copyright record credits Pigeonhole to Stephan Bosch and
Dovecot authors, Copyright (c) 2002–2021. Upstream AUTHORS names Stephan Bosch
and credits the Dovecot server written by Timo Sirainen. Those are upstream
credits, not copyright assertions for original sieve-expire code.

## External libraries and tools

The plugin uses the Dovecot/Pigeonhole interfaces and native libraries. The
qualified target uses Debian package revision `1:2.4.1+dfsg1-6+deb13u6`, with
upstream runtime source archives named `dovecot-2.4.1-4` and
`dovecot-pigeonhole-2.4.1-4`. Package revision and displayed runtime version are
different identifiers and must not be used interchangeably.

Upstream Dovecot COPYING assigns MIT terms to `src/lib/`, `src/auth/`, and
`src/lib-sql/`, unless a file states an exception; other material is generally
LGPLv2.1 with identified exceptions. Pigeonhole COPYING generally specifies
LGPLv2.1 unless a file says otherwise. The retained Debian copyright record
credits Dovecot authors, Copyright (c) 2001–2022. Copies of the upstream
[MIT](LICENSES/MIT.txt) and [LGPL](LICENSES/LGPL-2.1-only.txt) texts are supplied
for reference and applicable use; file-specific upstream notices still govern.

The normal and fallback Sieve modules dynamically reference Pigeonhole and
system libraries; the IMAP companion also resolves interfaces from the loading
Dovecot process. Native Dovecot/Pigeonhole shared libraries are not bundled in
the small public installer payload. CC0 does not waive their conditions. Where
LGPL linking conditions apply, preserve the required notices and ability to use
modified, interface-compatible libraries through an appropriate build/install
route. Qualification of a particular binary is a safety/support statement,
not a prohibition on modifying the software.

Python and its standard library, libc, Docker/Compose, OpenSSL, rsync, and the
compiler/Autotools toolchain are external dependencies with their respective
licenses. Invocation or use of a tool does not make that tool's license the
license of original sieve-expire scripts. These tools are not included by this
licensing update. If an image, library, tool, or additional upstream source is
redistributed later, its own source/notice obligations must be assessed for
that actual distribution; this file is not a license inventory for all of DMS.

## Provenance and later packaging

The provenance assessment used the retained Dovecot/Pigeonhole source archives
and Debian copyright record for the qualified revision. Source archives:

- `dovecot_2.4.1+dfsg1.orig.tar.gz`: upstream COPYING, COPYING.MIT,
  COPYING.LGPL and AUTHORS.
- `dovecot_2.4.1+dfsg1.orig-pigeonhole.tar.gz`: upstream COPYING, AUTHORS and
  the identical Pigeonhole macro.
- `dovecot_2.4.1+dfsg1-6+deb13u6.debian.tar.xz`: Debian copyright record.

The Dovecot macro retains its explicit permissive notices but differs from the
macro in those retained upstream archives; this notice does not claim an exact
revision match for that file.

This public source tree includes `build-aux/run-test.sh.in`, copied unchanged
from Dovecot 2.4.1-4 and covered by upstream LGPLv2.1 absent a file exception.
Autotools may also
generate or copy helpers such as `missing`, `ltmain.sh`, and `install-sh`; those
have their own GNU/X Consortium notices and, where present, distribution
exceptions. Preserve and inventory the actual helper versions shipped, rather
than treating generated files as original CC0 material.

Historical sealed evidence is retained privately and unchanged. This distribution
includes the legal texts/notices and matching original source/provenance; its
new archive identity is recorded in SHA256SUMS. This is not a license inventory
for a complete redistributed DMS image.

## Public references

- [CC0 legal code](https://creativecommons.org/publicdomain/zero/1.0/legalcode.en)
- [CC0 SPDX identifier](https://spdx.org/licenses/CC0-1.0.html)
- [FSFAP SPDX identifier](https://spdx.org/licenses/FSFAP.html)
- [LGPL-2.1-only SPDX identifier](https://spdx.org/licenses/LGPL-2.1-only.html)
- [Dovecot source](https://github.com/dovecot/core)
- [Pigeonhole source](https://github.com/dovecot/pigeonhole)
