#!/bin/sh
# Build in a writable copy; leave the checkout and release payload unchanged.
set -eu
if [ "$#" -ne 2 ]; then echo "usage: $0 SOURCE_DIRECTORY OUTPUT_DIRECTORY" >&2; exit 2; fi
source_dir=$(cd "$1" && pwd)
mkdir -p "$2"
output_dir=$(cd "$2" && pwd)
if [ -e "$output_dir/project" ]; then echo 'output already contains a build; choose a new directory' >&2; exit 2; fi
mkdir "$output_dir/project"
for name in configure.ac Makefile.am src m4 build-aux fallback; do cp -a "$source_dir/$name" "$output_dir/project/"; done
cd "$output_dir/project"
autoreconf -fi
./configure --with-dovecot=/usr/lib/dovecot --with-pigeonhole=/usr/include/dovecot/sieve --with-moduledir=/usr/lib/dovecot/modules
make -j2 V=1
make -C fallback
mkdir "$output_dir/modules"
cp src/.libs/lib90_sieve_expire_plugin.so src/.libs/lib91_imap_sieve_expire_plugin.so src/.libs/libsieve_expire_settings.so fallback/lib90_sieve_expire_fallback_plugin.so "$output_dir/modules/"
sha256sum "$output_dir"/modules/*.so
# Builds are not automatically release-qualified. Do not replace shipped modules.
