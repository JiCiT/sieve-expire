#ifndef SIEVE_EXPIRE_PLUGIN_H
#define SIEVE_EXPIRE_PLUGIN_H

#include "lib.h"

struct mail_storage_hooks;
struct mail_user;
struct mailbox;
struct mail_index_view;

struct sieve_expire_record {
	int64_t expire_at;
	int64_t expunge_at;
};

extern struct mail_storage_hooks sieve_expire_mail_storage_hooks;
extern struct mail_storage_hooks sieve_expire_maintenance_hooks;

struct sieve_instance;
int sieve_expire_plugin_load(struct sieve_instance *svinst, void **context);
void sieve_expire_plugin_unload(struct sieve_instance *svinst, void *context);
void sieve_expire_plugin_init(void);
void sieve_expire_plugin_deinit(void);

/* Common storage entry points; no IMAP types or symbol dependencies. */
void sieve_expire_user_created(struct mail_user *user);
void sieve_expire_track_mailbox(struct mailbox *box);
bool sieve_expire_box_eligible(struct mailbox *box);
bool sieve_expire_read_record(struct mailbox *box, struct mail_index_view *view,
                             uint32_t seq, struct sieve_expire_record *record);
bool sieve_expire_user_ready(struct mail_user *user);
void sieve_expire_activity_begin(struct mail_user *user);
void sieve_expire_activity_end(struct mail_user *user);
void sieve_expire_activity(struct mailbox *box);
bool sieve_expire_pending(struct mail_user *user);
void sieve_expire_consume(struct mail_user *user);
bool sieve_expire_storage_idle(struct mail_user *user);
void sieve_expire_teardown(struct mail_user *user);
void sieve_expire_set_cancel(struct mail_user *user, void (*cancel)(void *), void *context);
void sieve_expire_maintenance_run(struct mail_user *user);

#endif
