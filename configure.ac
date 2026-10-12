AC_INIT([sieve-expire], [0.1.0], [nobody@example.invalid])
AC_CONFIG_SRCDIR([src/sieve-expire-plugin.c])
AC_CONFIG_AUX_DIR([build-aux])
AC_CONFIG_MACRO_DIRS([m4])

AM_INIT_AUTOMAKE([foreign])
AC_PROG_CC
LT_INIT

m4_ifndef([DC_DOVECOT], [
  AC_MSG_ERROR([Dovecot autoconf macros not found])
])
m4_ifndef([DC_PIGEONHOLE], [
  AC_MSG_ERROR([Pigeonhole autoconf macros not found])
])

DC_DOVECOT
DC_DOVECOT_MODULEDIR
DC_PIGEONHOLE

CFLAGS="$DOVECOT_CFLAGS $CFLAGS"
LIBS="$DOVECOT_LIBS $LIBS"

AC_CONFIG_FILES([
  Makefile
  src/Makefile
])

AC_OUTPUT
