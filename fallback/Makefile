# Independent build: no normal sieve-expire sources, libraries or schema.
DOVECOT_CONFIG ?= /usr/lib/dovecot/dovecot-config
PIGEONHOLE_INCLUDE ?= /usr/include/dovecot/sieve
CC = cc
CFLAGS ?= -O2 -g
MODULE = lib90_sieve_expire_fallback_plugin.so

.PHONY: all clean install
all: $(MODULE)

$(MODULE): sieve-expire-fallback-plugin.c $(DOVECOT_CONFIG)
	. "$(DOVECOT_CONFIG)"; \
	$(CC) $$DOVECOT_CFLAGS $(CFLAGS) $(CPPFLAGS) -DHAVE_CONFIG_H \
	  $$LIBDOVECOT_INCLUDE -I"$(PIGEONHOLE_INCLUDE)" -fPIC -shared \
	  $(LDFLAGS) -Wl,-rpath,$$dovecot_pkglibdir -L$$dovecot_pkglibdir \
	  -o $@ $< -ldovecot-sieve -ldl

# The fallback belongs only in Pigeonhole's module directory.
install: $(MODULE)
	. "$(DOVECOT_CONFIG)"; \
	install -d "$(DESTDIR)$$dovecot_moduledir/sieve"; \
	install -m 0644 $(MODULE) "$(DESTDIR)$$dovecot_moduledir/sieve/$(MODULE)"

clean:
	rm -f $(MODULE)
