dovecot_module_LTLIBRARIES = lib90_sieve_expire_plugin.la lib91_imap_sieve_expire_plugin.la

lib90_sieve_expire_plugin_la_LDFLAGS = \
        -module -avoid-version \
        -Wl,-rpath,/usr/lib/dovecot \
        -Wl,--no-as-needed \
        -Wl,-u,sieve_storage_get_last_error

AM_CPPFLAGS = \
        -DHAVE_CONFIG_H \
        $(LIBDOVECOT_INCLUDE) \
        $(LIBSIEVE_INCLUDE)

lib90_sieve_expire_plugin_la_SOURCES = \
        sieve-expire-plugin.c sieve-expire-maintenance.c \
        sieve-expire-cooldown.c sieve-expire-settings.c

noinst_HEADERS = \
        sieve-expire-plugin.h sieve-expire-settings.h

lib90_sieve_expire_plugin_la_LIBADD = \
        -L/usr/lib/dovecot -ldovecot-sieve


# IMAP exports these symbols; non-IMAP module remains service independent.
lib91_imap_sieve_expire_plugin_la_SOURCES = imap-sieve-expire-plugin.c
lib91_imap_sieve_expire_plugin_la_LDFLAGS = -module -avoid-version

# doveconf/config discover *_set_infos only in their settings module directory.
# This module has no storage, Sieve or IMAP symbol dependencies.
settingsdir = $(dovecot_moduledir)/settings
settings_LTLIBRARIES = libsieve_expire_settings.la
libsieve_expire_settings_la_SOURCES = sieve-expire-settings.c
libsieve_expire_settings_la_CPPFLAGS = $(AM_CPPFLAGS) -DSIEVE_EXPIRE_CONFIG_MODULE
libsieve_expire_settings_la_LDFLAGS = -module -avoid-version
