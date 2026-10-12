/* Native Dovecot 2.4 settings, shared by the runtime and config-only module. */
#include "lib.h"
#include "settings.h"
#include "sieve-expire-settings.h"

static const struct setting_define sieve_expire_setting_defines[] = {
        SETTING_DEFINE_STRUCT_UINTMAX("sieve_expire_maintenance_cooldown_seconds",
                maintenance_cooldown_seconds, struct sieve_expire_settings),
        SETTING_DEFINE_LIST_END
};
static const struct sieve_expire_settings sieve_expire_defaults = {
        .maintenance_cooldown_seconds = 0
};
const struct setting_parser_info sieve_expire_setting_parser_info = {
        .name = "sieve_expire",
        .defines = sieve_expire_setting_defines,
        .defaults = &sieve_expire_defaults,
        .struct_size = sizeof(struct sieve_expire_settings),
        .pool_offset1 = 1 + offsetof(struct sieve_expire_settings, pool)
};

#ifdef SIEVE_EXPIRE_CONFIG_MODULE
const char *sieve_expire_settings_version = DOVECOT_ABI_VERSION;
const struct setting_parser_info *sieve_expire_settings_set_infos[] = {
        &sieve_expire_setting_parser_info, NULL
};
#endif
