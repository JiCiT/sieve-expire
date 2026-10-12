#ifndef SIEVE_EXPIRE_SETTINGS_H
#define SIEVE_EXPIRE_SETTINGS_H

#include "settings-parser.h"

struct mail_user;
struct file_lock;

struct sieve_expire_settings {
        pool_t pool;
        uintmax_t maintenance_cooldown_seconds;
};

extern const struct setting_parser_info sieve_expire_setting_parser_info;
bool sieve_expire_cooldown_admit(struct mail_user *user,
                               struct file_lock *lock, uintmax_t seconds);

#endif
