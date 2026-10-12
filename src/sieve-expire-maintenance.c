/* Service-independent bounded maintenance, Dovecot 2.4.1 Maildir only. */
#include "lib.h"
#include "array.h"
#include "str.h"
#include "file-lock.h"
#include "mail-user.h"
#include "mail-storage-hooks.h"
#include "mail-namespace.h"
#include "mail-storage-private.h"
#include "mailbox-list-iter.h"
#include "mail-index.h"
#include "module-context.h"
#include "sieve-expire-plugin.h"
#include "settings.h"
#include "sieve-expire-settings.h"
#include <time.h>

#define MAINTENANCE_LOCK "dovecot.sieve-expire-maintenance.lock"
#define BUDGET_SECONDS 5

struct expire_user {
        union mail_user_module_context module;
        unsigned int activity_depth, storage_depth;
        bool pending, running, teardown;
        uintmax_t cooldown_seconds;
        void (*cancel)(void *);
        void *cancel_context;
        /* Owned hints only: never retain namespace, mailbox, view or sequence. */
        char *cursor_namespace, *cursor_name;
        uint32_t cursor_uidvalidity, cursor_uid;
};
struct expire_box {
        union mailbox_module_context module;
        unsigned int sync_count;
};
MODULE_CONTEXT_DEFINE_INIT(expire_user_module, &mail_user_module_register);
MODULE_CONTEXT_DEFINE_INIT(expire_box_module, &mail_storage_module_register);

static struct expire_user *user_context(struct mail_user *user)
{
        return MODULE_CONTEXT(user, expire_user_module);
}
static struct mail_user *box_user(struct mailbox *box)
{
        return mail_storage_get_user(box->storage);
}
bool sieve_expire_box_eligible(struct mailbox *box)
{
        struct mail_user *user = box_user(box);
        struct mail_namespace *ns = mailbox_get_namespace(box);
        return user != NULL && ns->type == MAIL_NAMESPACE_TYPE_PRIVATE &&
                ns->owner == user && ns->alias_for == NULL &&
                box->virtual_vfuncs == NULL && !box->mailbox_not_original &&
                strcmp(box->storage->name, "maildir") == 0 &&
                strcmp(mailbox_list_get_driver_name(box->list), "maildir++") == 0;
}
bool sieve_expire_user_ready(struct mail_user *user)
{
        struct expire_user *ctx = user_context(user);
        return ctx != NULL && !ctx->running && !ctx->teardown &&
                !user->deinitializing && !user->autocreated &&
                !user->anonymous && !user->dsyncing && !user->admin;
}
void sieve_expire_activity_begin(struct mail_user *user)
{
        struct expire_user *ctx = user_context(user);
        if (ctx != NULL) ctx->activity_depth++;
}
void sieve_expire_activity_end(struct mail_user *user)
{
        struct expire_user *ctx = user_context(user);
        if (ctx != NULL) { i_assert(ctx->activity_depth > 0); ctx->activity_depth--; }
}
void sieve_expire_activity(struct mailbox *box)
{
        struct mail_user *user = box_user(box);
        struct expire_user *ctx = user_context(user);
        if (sieve_expire_user_ready(user) && ctx->activity_depth != 0 &&
            sieve_expire_box_eligible(box)) ctx->pending = TRUE;
}
bool sieve_expire_pending(struct mail_user *user)
{
        struct expire_user *ctx = user_context(user);
        return ctx != NULL && ctx->pending;
}
void sieve_expire_consume(struct mail_user *user)
{
        struct expire_user *ctx = user_context(user);
        if (ctx != NULL) ctx->pending = FALSE;
}
void sieve_expire_teardown(struct mail_user *user)
{
        struct expire_user *ctx = user_context(user);
        if (ctx != NULL) { ctx->teardown = TRUE; ctx->pending = FALSE;
                if (ctx->cancel != NULL) ctx->cancel(ctx->cancel_context); }
}
void sieve_expire_set_cancel(struct mail_user *user, void (*cancel)(void *), void *context)
{
        struct expire_user *ctx = user_context(user);
        if (ctx != NULL) { ctx->cancel = cancel; ctx->cancel_context = context; }
}
static void user_deinit_pre(struct mail_user *user)
{
        struct expire_user *ctx = user_context(user);
        sieve_expire_teardown(user);
        i_free(ctx->cursor_namespace);
        i_free(ctx->cursor_name);
        ctx->module.super.deinit_pre(user);
}
void sieve_expire_user_created(struct mail_user *user)
{
        struct expire_user *ctx = p_new(user->pool, struct expire_user, 1);
        struct mail_user_vfuncs *v = user->vlast;
        ctx->module.super = *v;
        user->vlast = &ctx->module.super;
        v->deinit_pre = user_deinit_pre;
        MODULE_CONTEXT_SET_SELF(user, expire_user_module, &ctx->module);
        const struct sieve_expire_settings *set;
        const char *error;
        if (settings_get(user->event, &sieve_expire_setting_parser_info, 0,
                         &set, &error) < 0) {
                user->error = p_strdup(user->pool, error);
                return;
        }
        ctx->cooldown_seconds = set->maintenance_cooldown_seconds;
        settings_free(set);
        e_debug(user->event, "sieve-expire: common hooks installed at user creation");
}

/* Track stack depth AND sync-context lifetimes. Transactions retain core's
   transaction_count until their backend finish has fully returned. */
static void storage_enter(struct mailbox *box)
{ user_context(box_user(box))->storage_depth++; }
static void storage_leave(struct mailbox *box)
{ struct expire_user *ctx = user_context(box_user(box)); i_assert(ctx->storage_depth > 0); ctx->storage_depth--; }
static int box_open(struct mailbox *box)
{
        struct expire_box *ctx = MODULE_CONTEXT(box, expire_box_module);
        storage_enter(box);
        int ret = ctx->module.super.open(box);
        /* Public mailbox_open would autocreate configured Trash after NOTFOUND.
           A disappearing worker target must fail instead, including that race. */
        if (ret < 0 && user_context(box_user(box))->running &&
            box->storage->error == MAIL_ERROR_NOTFOUND)
                mail_storage_set_error(box->storage, MAIL_ERROR_TEMP,
                        "sieve-expire: existing maintenance mailbox disappeared");
        storage_leave(box);
        return ret;
}
static void box_close(struct mailbox *box)
{
        struct expire_box *ctx = MODULE_CONTEXT(box, expire_box_module);
        storage_enter(box); ctx->module.super.close(box); storage_leave(box);
}
static void box_free(struct mailbox *box)
{
        struct expire_box *ctx = MODULE_CONTEXT(box, expire_box_module);
        struct expire_user *user = user_context(box_user(box));
        user->storage_depth++;
        ctx->module.super.free(box);
        /* box is destroyed, while mailbox_free still owns its storage reference. */
        i_assert(user->storage_depth > 0); user->storage_depth--;
}
static struct mailbox_sync_context *sync_init(struct mailbox *box, enum mailbox_sync_flags flags)
{
        struct expire_box *ctx = MODULE_CONTEXT(box, expire_box_module);
        storage_enter(box);
        struct mailbox_sync_context *sync = ctx->module.super.sync_init(box, flags);
        if (sync != NULL) ctx->sync_count++;
        storage_leave(box);
        return sync;
}
static bool sync_next(struct mailbox_sync_context *sync, struct mailbox_sync_rec *rec)
{
        struct mailbox *box = sync->box;
        struct expire_box *ctx = MODULE_CONTEXT(box, expire_box_module);
        storage_enter(box);
        bool ret = ctx->module.super.sync_next(sync, rec);
        storage_leave(box); return ret;
}
static int sync_deinit(struct mailbox_sync_context *sync, struct mailbox_sync_status *status)
{
        struct mailbox *box = sync->box;
        struct expire_box *ctx = MODULE_CONTEXT(box, expire_box_module);
        storage_enter(box);
        int ret = ctx->module.super.sync_deinit(sync, status);
        i_assert(ctx->sync_count > 0); ctx->sync_count--;
        storage_leave(box); return ret;
}
static struct mailbox_transaction_context *transaction_begin(struct mailbox *box, enum mailbox_transaction_flags flags, const char *reason)
{
        struct expire_box *ctx = MODULE_CONTEXT(box, expire_box_module);
        storage_enter(box);
        struct mailbox_transaction_context *t = ctx->module.super.transaction_begin(box, flags, reason);
        storage_leave(box); return t;
}
static int transaction_commit(struct mailbox_transaction_context *t, struct mail_transaction_commit_changes *changes)
{
        struct mailbox *box = t->box;
        struct expire_box *ctx = MODULE_CONTEXT(box, expire_box_module);
        storage_enter(box);
        int ret = ctx->module.super.transaction_commit(t, changes);
        storage_leave(box); return ret;
}
static void transaction_rollback(struct mailbox_transaction_context *t)
{
        struct mailbox *box = t->box;
        struct expire_box *ctx = MODULE_CONTEXT(box, expire_box_module);
        storage_enter(box); ctx->module.super.transaction_rollback(t); storage_leave(box);
}
void sieve_expire_track_mailbox(struct mailbox *box)
{
        if (user_context(box_user(box)) == NULL) return;
        struct expire_box *ctx = p_new(box->pool, struct expire_box, 1);
        struct mailbox_vfuncs *v = box->vlast;
        ctx->module.super = *v; box->vlast = &ctx->module.super;
        v->open = box_open; v->close = box_close; v->free = box_free;
        v->sync_init = sync_init; v->sync_next = sync_next; v->sync_deinit = sync_deinit;
        v->transaction_begin = transaction_begin;
        v->transaction_commit = transaction_commit; v->transaction_rollback = transaction_rollback;
        MODULE_CONTEXT_SET_SELF(box, expire_box_module, &ctx->module);
}
bool sieve_expire_storage_idle(struct mail_user *user)
{
        struct expire_user *ctx = user_context(user);
        if (!sieve_expire_user_ready(user) || ctx->storage_depth != 0 || ctx->activity_depth != 0) return FALSE;
        /* Supported plugin set has been inspected. Unknown suspended ownership
           cannot be established by probing/opening another mailbox.
           imap_sieve is an explicitly investigated exception: its synchronous
           script/event ownership is contained by the command/transaction gates;
           accepted deferred entry has no active IMAPSieve event transaction, so
           internal maintenance does not generate its events. This does not
           generalize to other plugins or arbitrary Sieve extensions. */
        const char *plugin;
        array_foreach_elem(&user->set->mail_plugins, plugin) {
                if (strcmp(plugin, "quota") != 0 && strcmp(plugin, "sieve_expire") != 0 &&
                    strcmp(plugin, "imap_quota") != 0 && strcmp(plugin, "imap_sieve_expire") != 0 &&
                    strcmp(plugin, "imap_sieve") != 0)
                        return FALSE;
        }
        struct mail_storage *storage;
        for (storage = user->storages; storage != NULL; storage = storage->next) {
                struct mailbox *box;
                for (box = storage->mailboxes; box != NULL; box = box->next) {
                        struct expire_box *b = MODULE_CONTEXT(box, expire_box_module);
                        if (b == NULL || !sieve_expire_box_eligible(box) || b->sync_count != 0 || box->transaction_count != 0 ||
                            box->attribute_iter_count != 0 || box->tmp_sync_view != NULL ||
                            box->creating || box->deleting || box->mailbox_undeleting ||
                            (box->flags & MAILBOX_FLAG_KEEP_LOCKED) != 0 ||
                            (box->index != NULL && mail_index_is_locked(box->index)) ||
                            (box->index_pvt != NULL && mail_index_is_locked(box->index_pvt)) ||
                            box->virtual_vfuncs != NULL || box->mailbox_not_original ||
                            (array_is_created(&box->search_results) && array_count(&box->search_results) != 0))
                                return FALSE;
                }
        }
        return TRUE;
}

struct catalog_box {
        struct mail_namespace *ns;
        const char *name;
        bool trash;
};
ARRAY_DEFINE_TYPE(catalog_box, struct catalog_box);
struct catalog_namespace {
        struct mail_namespace *ns;
        unsigned int trash_count;
        const struct catalog_box *trash;
};
ARRAY_DEFINE_TYPE(catalog_namespace, struct catalog_namespace);
struct pass {
        struct mail_user *user;
        pool_t pool;
        ARRAY_TYPE(catalog_box) boxes;
        ARRAY_TYPE(catalog_namespace) namespaces;
        struct timespec deadline;
        unsigned int examined, moved, expunged;
};
static bool budget_expired(struct pass *pass)
{
        struct timespec now;
        if (clock_gettime(CLOCK_MONOTONIC, &now) < 0) return TRUE;
        return now.tv_sec > pass->deadline.tv_sec ||
                (now.tv_sec == pass->deadline.tv_sec && now.tv_nsec >= pass->deadline.tv_nsec);
}
static bool namespace_eligible(struct mail_user *user, struct mail_namespace *ns)
{
        return ns->type == MAIL_NAMESPACE_TYPE_PRIVATE && ns->owner == user && ns->alias_for == NULL;
}
/* Never open an uncreated/autocreate box, nor allow open to create it. */
static struct mailbox *existing_box(struct mail_user *user, struct mail_namespace *ns, const char *name, bool *uncertain)
{
        struct mailbox *box = mailbox_alloc(ns->list, name, 0);
        enum mailbox_existence existence;
        if (!namespace_eligible(user, ns) || mailbox_get_namespace(box) != ns ||
            !sieve_expire_box_eligible(box)) {
                mailbox_free(&box); return NULL;
        }
        if (mailbox_exists(box, FALSE, &existence) < 0) {
                if (uncertain != NULL) *uncertain = TRUE;
                mailbox_free(&box); return NULL;
        }
        if (existence != MAILBOX_EXISTENCE_SELECT) {
                mailbox_free(&box); return NULL;
        }
        return box;
}
static bool catalog_build(struct pass *pass)
{
        struct mail_namespace *ns;
        for (ns = pass->user->namespaces; ns != NULL; ns = ns->next) {
                if (!namespace_eligible(pass->user, ns)) continue;
                struct catalog_namespace item = { .ns = ns };
                /* User INBOX is named "INBOX", including when its namespace
                   has a nonempty prefix. A prefix-only pattern omits it. */
                const char *patterns[] = { t_strconcat(ns->prefix, "*", NULL),
                        (ns->flags & NAMESPACE_FLAG_INBOX_USER) != 0 &&
                        ns->prefix[0] != '\0' ? "INBOX" : NULL, NULL };
                struct mailbox_list_iterate_context *iter = mailbox_list_iter_init_multiple(ns->list,
                        patterns, MAILBOX_LIST_ITER_RETURN_SPECIALUSE |
                        MAILBOX_LIST_ITER_NO_AUTO_BOXES | MAILBOX_LIST_ITER_SKIP_ALIASES);
                const struct mailbox_info *info;
                bool complete = TRUE;
                while ((info = mailbox_list_iter_next(iter)) != NULL) {
                        if (budget_expired(pass)) { complete = FALSE; break; }
                        if (info->ns != ns || (info->flags & (MAILBOX_NOSELECT | MAILBOX_NONEXISTENT)) != 0) continue;
                        bool uncertain = FALSE;
                        struct mailbox *box = existing_box(pass->user, ns, info->vname, &uncertain);
                        if (uncertain) { complete = FALSE; break; }
                        if (box == NULL) continue;
                        mailbox_free(&box);
                        struct catalog_box entry = { .ns = ns, .name = p_strdup(pass->pool, info->vname),
                                .trash = info->special_use != NULL &&
                                str_array_icase_find(t_strsplit_spaces(info->special_use, " "), "\\Trash") };
                        array_push_back(&pass->boxes, &entry);
                        if (entry.trash) item.trash_count++;
                }
                if (mailbox_list_iter_deinit(&iter) < 0) {
                        e_warning(pass->user->event, "sieve-expire: incomplete mailbox catalog"); complete = FALSE;
                }
                if (!complete) return FALSE; /* no inferred uniqueness from a partial catalog */
                e_debug(pass->user->event, "sieve-expire: namespace %s existing eligible Trash count=%u",
                        ns->set->name, item.trash_count);
                array_push_back(&pass->namespaces, &item);
        }
        /* Set pointers only after arrays stop growing. */
        struct catalog_namespace *item;
        array_foreach_modifiable(&pass->namespaces, item) {
                const struct catalog_box *box;
                array_foreach(&pass->boxes, box) {
                        if (box->ns == item->ns && box->trash) item->trash = box;
                }
        }
        return TRUE;
}
static const struct catalog_box *trash_for(struct pass *pass, const struct catalog_box *source)
{
        struct mail_namespace *inbox = mail_namespace_find_inbox(pass->user->namespaces);
        if (inbox != NULL && inbox->alias_for != NULL) inbox = inbox->alias_for;
        const struct catalog_namespace *item, *fallback = NULL;
        array_foreach(&pass->namespaces, item) {
                if (item->ns == source->ns && item->trash_count != 0)
                        return item->trash_count == 1 ? item->trash : NULL;
                if (item->ns == inbox) fallback = item;
        }
        return fallback != NULL && fallback->trash_count == 1 ? fallback->trash : NULL;
}
static bool force_sync(struct mailbox *box, const char *role)
{
        if (mailbox_sync(box, MAILBOX_SYNC_FLAG_FORCE_RESYNC) == 0) {
                e_debug(box->event, "sieve-expire: %s FORCE_RESYNC succeeded flags=%u", role,
                        (unsigned int)MAILBOX_SYNC_FLAG_FORCE_RESYNC);
                return TRUE;
        }
        e_warning(box->event, "sieve-expire: %s FORCE_RESYNC failed: %s", role, mailbox_get_last_internal_error(box, NULL));
        return FALSE;
}
static bool due(int64_t deadline, int64_t now)
{ return deadline != 0 && deadline <= now; }
/* Every call has independent objects and one message per transaction pair. */
static void apply_one(struct pass *pass, const struct catalog_box *entry,
                      uint32_t expected_uv, uint32_t uid)
{
        struct mailbox *source = existing_box(pass->user, entry->ns, entry->name, NULL), *dest = NULL;
        struct mailbox_transaction_context *src_t = NULL, *dst_t = NULL;
        struct mail *mail = NULL;
        struct mail_save_context *save = NULL;
        struct mailbox_status status;
        struct sieve_expire_record record;
        if (source == NULL) return;
        if (mailbox_open(source) < 0 || !sieve_expire_box_eligible(source) || mailbox_is_readonly(source) || !force_sync(source, "source") ||
            mailbox_get_status(source, STATUS_UIDVALIDITY, &status) < 0) goto out;
        if (status.uidvalidity != expected_uv) {
                e_debug(source->event, "sieve-expire: UIDVALIDITY changed; candidate discarded"); goto out;
        }
        /* Inspect post-force view before opening the mutation transactions. */
        uint32_t seq;
        if (!mail_index_lookup_seq(source->view, uid, &seq) ||
            !sieve_expire_read_record(source, source->view, seq, &record)) goto out;
        int64_t now = time(NULL);
        bool expunge = due(record.expunge_at, now);
        if (!expunge && (!due(record.expire_at, now) || entry->trash)) goto out;
        if (!expunge) {
                const struct catalog_box *target = trash_for(pass, entry);
                if (target == NULL) goto out;
                if (mailbox_equals(source, target->ns, target->name)) goto out;
                dest = existing_box(pass->user, target->ns, target->name, NULL);
                if (dest == NULL || mailbox_open(dest) < 0 || !sieve_expire_box_eligible(dest) || mailbox_is_readonly(dest) || !force_sync(dest, "destination")) goto out;
                dst_t = mailbox_transaction_begin(dest, MAILBOX_TRANSACTION_FLAG_EXTERNAL |
                        MAILBOX_TRANSACTION_FLAG_ASSIGN_UIDS, "sieve-expire MOVE destination");
        }
        src_t = mailbox_transaction_begin(source, MAILBOX_TRANSACTION_FLAG_REFRESH, "sieve-expire source");
        mail = mail_alloc(src_t, 0, NULL);
        if (!mail_set_uid(mail, uid) || mail->expunged ||
            mail_index_get_header(src_t->view)->uid_validity != expected_uv ||
            !sieve_expire_read_record(source, src_t->view, mail->seq, &record)) goto out;
        now = time(NULL);
        /* A concurrent policy change must not silently change the operation. */
        if (expunge ? !due(record.expunge_at, now) :
            (due(record.expunge_at, now) || !due(record.expire_at, now))) goto out;
        if (expunge) mail_expunge(mail);
        else {
                save = mailbox_save_alloc(dst_t);
                mailbox_save_copy_flags(save, mail);
                if (mailbox_move(&save, mail) < 0 || mail->expunged) {
                        e_warning(source->event, "sieve-expire: MOVE copy failed; source rolled back");
                        goto out;
                }
        }
        mail_free(&mail);
        if (dst_t != NULL && mailbox_transaction_commit(&dst_t) < 0) {
                e_warning(dest->event, "sieve-expire: destination commit failed; source rolled back"); goto out;
        }
        if (mailbox_transaction_commit(&src_t) < 0) {
                e_warning(source->event, "sieve-expire: source commit failed%s", expunge ? "" : "; destination may contain a duplicate"); goto out;
        }
        if (mailbox_sync(source, MAILBOX_SYNC_FLAG_EXPUNGE) < 0)
                e_warning(source->event, "sieve-expire: post-operation EXPUNGE sync failed");
        if (expunge) pass->expunged++; else pass->moved++;
 out:
        if (save != NULL) mailbox_save_cancel(&save);
        if (mail != NULL) mail_free(&mail);
        if (dst_t != NULL) mailbox_transaction_rollback(&dst_t);
        if (src_t != NULL) mailbox_transaction_rollback(&src_t);
        if (dest != NULL) mailbox_free(&dest);
        mailbox_free(&source);
}
static void cursor_set(struct expire_user *ctx, const struct catalog_box *box, uint32_t uv, uint32_t uid)
{
        i_free(ctx->cursor_namespace); i_free(ctx->cursor_name);
        ctx->cursor_namespace = i_strdup(box->ns->set->name);
        ctx->cursor_name = i_strdup(box->name);
        ctx->cursor_uidvalidity = uv; ctx->cursor_uid = uid;
}
static void scan_box(struct pass *pass, const struct catalog_box *entry, struct expire_user *ctx, bool resume)
{
        struct mailbox *box = existing_box(pass->user, entry->ns, entry->name, NULL);
        if (box == NULL) {
                /* An unavailable continuation must not pin every later mailbox. */
                cursor_set(ctx, entry, 0, UINT32_MAX);
                return;
        }
        struct mailbox_status status;
        if (mailbox_open(box) < 0 || !sieve_expire_box_eligible(box) || mailbox_is_readonly(box) || !force_sync(box, "scan source") ||
            mailbox_get_status(box, STATUS_UIDVALIDITY, &status) < 0) {
                cursor_set(ctx, entry, 0, UINT32_MAX);
                mailbox_free(&box); return;
        }
        uint32_t after = resume && ctx->cursor_uidvalidity == status.uidvalidity ? ctx->cursor_uid : 0;
        uint32_t uv = status.uidvalidity;
        /* Copy UID hints, release scan object before any destructive operation. */
        ARRAY_TYPE(seq_range) candidates;
        t_array_init(&candidates, 16);
        uint32_t count = mail_index_view_get_messages_count(box->view), uid;
        bool complete = TRUE;
        for (uint32_t seq = 1; seq <= count; seq++) {
                if (budget_expired(pass)) { complete = FALSE; break; }
                mail_index_lookup_uid(box->view, seq, &uid);
                if (uid <= after) continue;
                struct sieve_expire_record record;
                pass->examined++;
                cursor_set(ctx, entry, uv, uid);
                if (sieve_expire_read_record(box, box->view, seq, &record) &&
                    (due(record.expunge_at, time(NULL)) || (due(record.expire_at, time(NULL)) && !entry->trash)))
                        seq_range_array_add(&candidates, uid);
                /* Bounded hint batch; no multi-message save transaction. */
                if (seq_range_count(&candidates) >= 32) { complete = FALSE; break; }
        }
        mailbox_free(&box);
        struct seq_range_iter iter;
        seq_range_array_iter_init(&iter, &candidates);
        unsigned int n = 0;
        while (seq_range_array_iter_nth(&iter, n++, &uid)) {
                if (budget_expired(pass)) {
                        /* Revisit unprocessed hint on the next genuine pass. */
                        cursor_set(ctx, entry, uv, uid - 1); return;
                }
                apply_one(pass, entry, uv, uid);
        }
        if (complete) cursor_set(ctx, entry, uv, UINT32_MAX);
}
void sieve_expire_maintenance_run(struct mail_user *user)
{
        struct expire_user *ctx = user_context(user);
        if (!sieve_expire_pending(user) || !sieve_expire_storage_idle(user)) { sieve_expire_consume(user); return; }
        ctx->pending = FALSE; ctx->running = TRUE;
        struct timespec deadline;
        if (clock_gettime(CLOCK_MONOTONIC, &deadline) < 0) {
                e_warning(user->event, "sieve-expire: monotonic clock unavailable; skipped");
                ctx->running = FALSE; return;
        }
        deadline.tv_sec += BUDGET_SECONDS;
        struct file_lock *lock = NULL;
        const char *error;
        int ret = mail_user_lock_file_create(user, MAINTENANCE_LOCK, 0, &lock, &error);
        if (ret < 0) e_warning(user->event, "sieve-expire: maintenance lock failed: %s", error);
        else if (ret == 0) e_debug(user->event, "sieve-expire: maintenance lock contended; skipped");
        if (ret <= 0) { ctx->running = FALSE; return; }
        e_debug(user->event, "sieve-expire: maintenance lock acquired path=%s", file_lock_get_path(lock));
        if (!sieve_expire_cooldown_admit(user, lock, ctx->cooldown_seconds)) {
                file_lock_free(&lock);
                ctx->running = FALSE;
                return;
        }
        struct pass pass = { .user = user, .pool = pool_alloconly_create("sieve-expire pass", 4096), .deadline = deadline };
        p_array_init(&pass.boxes, pass.pool, 32); p_array_init(&pass.namespaces, pass.pool, 4);
        if (!catalog_build(&pass)) goto out;
        unsigned int count = array_count(&pass.boxes), start = 0;
        if (count == 0) goto out;
        const struct catalog_box *boxes = array_front(&pass.boxes);
        bool resume = FALSE;
        for (unsigned int i = 0; i < count; i++) {
                if (ctx->cursor_name != NULL && strcmp(ctx->cursor_name, boxes[i].name) == 0 &&
                    strcmp(ctx->cursor_namespace, boxes[i].ns->set->name) == 0) {
                        start = ctx->cursor_uid == UINT32_MAX ? (i + 1) % count : i;
                        resume = ctx->cursor_uid != UINT32_MAX; break;
                }
        }
        for (unsigned int i = 0; i < count && !budget_expired(&pass); i++) {
                unsigned int j = (start + i) % count;
                T_BEGIN {
                        scan_box(&pass, &boxes[j], ctx, i == 0 && resume);
                } T_END;
                if (ctx->cursor_name != NULL && strcmp(ctx->cursor_name, boxes[j].name) == 0 &&
                    ctx->cursor_uid != UINT32_MAX) break;
        }
 out:
        e_debug(user->event, "sieve-expire: maintenance pass examined=%u moved=%u expunged=%u%s",
                pass.examined, pass.moved, pass.expunged, budget_expired(&pass) ? " (budget reached)" : "");
        pool_unref(&pass.pool);
        file_lock_free(&lock);
        ctx->running = FALSE;
}

struct mail_storage_hooks sieve_expire_maintenance_hooks = {
        .mail_user_created = sieve_expire_user_created,
        .mailbox_allocated = sieve_expire_track_mailbox
};
