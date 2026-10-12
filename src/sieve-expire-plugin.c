/*
 * sieve-expire - standalone Pigeonhole Sieve extension for Dovecot 2.4
 *
 * Sieve syntax:
 *
 *   require ["expire"];
 *   expire "30d";
 *
 * The action records an expiration time on the message saved by Sieve.
 * The expiration record is stored as a Dovecot mail-index extension.
 *
 * Expiration maintenance is deliberately separate from the Sieve action.
 * Expiration is stored in a mail-index extension, copied with the message,
 * and enforced by a guarded IMAP owner-loop maintenance opportunity.
 */

#include "lib.h"
#include "array.h"
#include "str.h"
#include "ioloop.h"
#include "mail-storage.h"
#include "mail-storage-private.h"
#include "mail-namespace.h"
#include "mail-storage-hooks.h"
#include "mail-index.h"
#include "mail-index-private.h"
#include "mailbox-list-iter.h"
#include "module-context.h"
#include "sieve.h"

#include "sieve-common.h"
#include "sieve-extensions.h"
#include "sieve-commands.h"
#include "sieve-code.h"
#include "sieve-validator.h"
#include "sieve-generator.h"
#include "sieve-interpreter.h"
#include "sieve-result.h"
#include "sieve-actions.h"
#include "sieve-dump.h"
#include "sieve-objects.h"

#include "sieve-expire-plugin.h"

#include <errno.h>
#include <inttypes.h>
#include <limits.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

/*
 * Mailbox module context.
 *
 * mailbox_module_context is backed by mail_storage_module_register in
 * Dovecot 2.4.x.  Keep the definition here rather than in the public
 * header so the header never needs mail-storage-private.h.
 */
struct sieve_expire_pending_save {
	struct mailbox_transaction_context *transaction;
	uint32_t seq;
	struct sieve_expire_record record;
	struct sieve_expire_pending_save *next;
};

struct sieve_expire_mailbox {
	union mailbox_module_context module_ctx;
	uint32_t expire_ext_id;
	uint32_t legacy_expire_ext_id;
	struct sieve_expire_record pending_record;
	bool pending_expire_set;
	struct sieve_expire_pending_save *pending_saves;
};

MODULE_CONTEXT_DEFINE_INIT(sieve_expire_mailbox_module,
				  &mail_storage_module_register);

static const struct sieve_extension_def sieve_expire_extension;
static const struct sieve_side_effect_def sieve_expire_side_effect;

/*
 * --------------------------------------------------------------------------
 * Duration parsing
 * --------------------------------------------------------------------------
 */

static bool
sieve_expire_parse_duration(const char *value, int64_t *seconds_r)
{
	char *end;
	int64_t number;
	int64_t multiplier = 1;

	if (value == NULL || *value == '\0')
		return FALSE;

	errno = 0;
	number = strtoll(value, &end, 10);
	if (errno != 0 || end == value || number < 0)
		return FALSE;

	if (*end != '\0') {
		if (end[1] != '\0')
			return FALSE;

		switch (*end) {
		case 's':
			multiplier = 1;
			break;
		case 'm':
			multiplier = 60;
			break;
		case 'h':
			multiplier = 60 * 60;
			break;
		case 'd':
			multiplier = 24 * 60 * 60;
			break;
		case 'w':
			multiplier = 7 * 24 * 60 * 60;
			break;
		default:
			return FALSE;
		}
	}

	if (number > INT64_MAX / multiplier)
		return FALSE;

	*seconds_r = number * multiplier;
	return TRUE;
}

/*
 * --------------------------------------------------------------------------
 * Result context
 * --------------------------------------------------------------------------
 */

struct sieve_expire_result_context {
	struct sieve_expire_record record;
	bool expire_set;
	bool expunge_set;
};

static struct sieve_expire_result_context *
sieve_expire_get_result_context(const struct sieve_extension *ext,
				struct sieve_result *result)
{
	struct sieve_expire_result_context *ctx;

	ctx = (struct sieve_expire_result_context *)
		sieve_result_extension_get_context(result, ext);
	if (ctx == NULL) {
		ctx = p_new(sieve_result_pool(result),
			    struct sieve_expire_result_context, 1);
		sieve_result_extension_set_context(result, ext, ctx);
	}
	return ctx;
}

/*
 * --------------------------------------------------------------------------
 * Sieve action
 * --------------------------------------------------------------------------
 */

struct act_expire_context {
	const char *duration;
	bool expunge;
};

static void
act_expire_print(const struct sieve_action *action,
		 const struct sieve_result_print_env *rpenv,
		 bool *keep)
{
	struct act_expire_context *ctx =
		(struct act_expire_context *)action->context;

	sieve_result_action_printf(rpenv, "set %s time to %s",
				   ctx->expunge ? "expunge" : "expiration",
				   ctx->duration);
	/*
	 * expire is metadata only.  It must never decide implicit keep.
	 */
	*keep = TRUE;
}

static int
act_expire_execute(const struct sieve_action_exec_env *aenv,
		   void *tr_context ATTR_UNUSED, bool *keep ATTR_UNUSED)
{
	struct act_expire_context *act =
		(struct act_expire_context *)aenv->action->context;
	const struct sieve_extension *ext = aenv->action->ext;
	struct sieve_expire_result_context *rctx;
	int64_t duration;
	int64_t now;

	if (act == NULL || ext == NULL ||
	    !sieve_expire_parse_duration(act->duration, &duration)) {
		sieve_result_error(aenv, "invalid expire duration");
		return SIEVE_EXEC_FAILURE;
	}

	now = (int64_t)time(NULL);
	if (duration > INT64_MAX - now) {
		sieve_result_error(aenv, "expire duration is too large");
		return SIEVE_EXEC_FAILURE;
	}

	rctx = sieve_expire_get_result_context(ext, aenv->result);
	if (act->expunge) {
		rctx->record.expunge_at = now + duration;
		rctx->expunge_set = TRUE;
	} else {
		rctx->record.expire_at = now + duration;
		rctx->expire_set = TRUE;
	}

	e_debug(aenv->event,
		"sieve-expire: %s time set to %"PRId64,
		act->expunge ? "expunge" : "expiration",
		act->expunge ? rctx->record.expunge_at : rctx->record.expire_at);

	return SIEVE_EXEC_OK;
}

static const struct sieve_action_def act_expire = {
	.name = "expire",
	.flags = 0,
	.print = act_expire_print,
	.execute = act_expire_execute
};

static const struct sieve_action_def act_expunge = {
	.name = "expunge",
	.flags = 0,
	.print = act_expire_print,
	.execute = act_expire_execute
};

/*
 * --------------------------------------------------------------------------
 * Mailbox save hook
 * --------------------------------------------------------------------------
 */

struct sieve_expire_pending {
	struct mailbox *box;
	struct sieve_expire_record record;
	struct sieve_expire_pending *next;
};

static struct sieve_expire_pending *sieve_expire_pending_head;

static void
sieve_expire_pending_set(struct mailbox *box,
			 const struct sieve_expire_record *record)
{
	struct sieve_expire_pending *pending;

	for (pending = sieve_expire_pending_head; pending != NULL;
	     pending = pending->next) {
		if (pending->box == box) {
			pending->record = *record;
			return;
		}
	}
	pending = i_new(struct sieve_expire_pending, 1);
	pending->box = box;
	pending->record = *record;
	pending->next = sieve_expire_pending_head;
	sieve_expire_pending_head = pending;
}

static bool
sieve_expire_pending_take(struct mailbox *box,
			  struct sieve_expire_record *record_r)
{
	struct sieve_expire_pending **link = &sieve_expire_pending_head;

	while (*link != NULL) {
		struct sieve_expire_pending *pending = *link;
		if (pending->box == box) {
			*link = pending->next;
			*record_r = pending->record;
			i_free(pending);
			return TRUE;
		}
		link = &pending->next;
	}
	return FALSE;
}

static void
sieve_expire_pending_remove(struct mailbox *box)
{
	struct sieve_expire_record unused;
	(void)sieve_expire_pending_take(box, &unused);
}

static void
sieve_expire_pending_save_add(struct sieve_expire_mailbox *mbox,
                              struct mailbox_transaction_context *transaction,
                              uint32_t seq,
                              const struct sieve_expire_record *record)
{
    struct sieve_expire_pending_save *pending =
        i_new(struct sieve_expire_pending_save, 1);

    pending->transaction = transaction;
    pending->seq = seq;
    pending->record = *record;
    pending->next = mbox->pending_saves;
    mbox->pending_saves = pending;
}

static struct sieve_expire_pending_save *
sieve_expire_pending_save_take(struct sieve_expire_mailbox *mbox,
                               struct mailbox_transaction_context *transaction)
{
    struct sieve_expire_pending_save **link = &mbox->pending_saves;

    while (*link != NULL) {
        struct sieve_expire_pending_save *pending = *link;
        if (pending->transaction == transaction) {
            *link = pending->next;
            pending->next = NULL;
            return pending;
        }
        link = &pending->next;
    }
    return NULL;
}

/* Read both the old one-timestamp record and the current two-timestamp form. */
static bool
sieve_expire_record_lookup(struct mail_index_view *view, uint32_t seq,
			   uint32_t ext_id, uint32_t legacy_ext_id,
			   struct sieve_expire_record *record_r)
{
	const void *data;
	uint32_t hdr_size;
	uint16_t record_size = 0, record_align;
	bool expunged;

	/* Dovecot 2.4 requires all three size output pointers, even when
	   the extension is registered but absent from the on-disk index. */
	mail_index_ext_get_size(view->map, ext_id, &hdr_size, &record_size,
				&record_align);
	mail_index_lookup_ext(view, seq, ext_id, &data, &expunged);
	if (data != NULL && !expunged && record_size >= sizeof(*record_r)) {
		*record_r = *(const struct sieve_expire_record *)data;
		if (record_r->expire_at != 0 || record_r->expunge_at != 0)
			return TRUE;
	}
	if (legacy_ext_id == UINT32_MAX)
		return FALSE;
	mail_index_ext_get_size(view->map, legacy_ext_id, &hdr_size, &record_size,
				&record_align);
	mail_index_lookup_ext(view, seq, legacy_ext_id, &data, &expunged);
	if (data == NULL || expunged || record_size < sizeof(time_t))
		return FALSE;
	i_zero(record_r);
	record_r->expire_at = (int64_t)*(const time_t *)data;
	return TRUE;
}

static int
sieve_expire_save_finish(struct mail_save_context *save_ctx)
{
    struct mailbox *box = save_ctx->transaction->box;
    struct sieve_expire_mailbox *mbox =
        MODULE_CONTEXT(box, sieve_expire_mailbox_module);
	struct mail *dest_mail;
	struct sieve_expire_record record;
    bool pending;
    int ret;

    if (mbox == NULL)
        return -1;

    /* Consume the staged value for this save, including failed saves. */
    pending = mbox->pending_expire_set;
	record = mbox->pending_record;
	mbox->pending_expire_set = FALSE;

	ret = mbox->module_ctx.super.save_finish(save_ctx);
	if (ret < 0 || !pending)
		return ret;
    dest_mail = mailbox_save_get_dest_mail(save_ctx);
    if (dest_mail == NULL || dest_mail->seq == 0 ||
        mbox->expire_ext_id == UINT32_MAX)
        return -1;

	/* Save the provisional sequence now; Maildir assigns its UID later. */
    sieve_expire_pending_save_add(mbox, save_ctx->transaction,
                                  dest_mail->seq, &record);
    return 0;
}

static int
sieve_expire_transaction_save_commit_pre(struct mail_save_context *save_ctx)
{
    struct mailbox *box = save_ctx->transaction->box;
    struct sieve_expire_mailbox *mbox =
        MODULE_CONTEXT(box, sieve_expire_mailbox_module);
    struct sieve_expire_pending_save *pending;
    int ret;

    pending = sieve_expire_pending_save_take(mbox, save_ctx->transaction);
	ret = mbox->module_ctx.super.transaction_save_commit_pre(save_ctx);
    if (ret < 0 || pending == NULL) {
        if (pending != NULL)
            i_free(pending);
        return ret;
    }

	/* The backend commit-pre hook has assigned stable UIDs to new records. */
    mail_index_update_ext(save_ctx->transaction->itrans, pending->seq,
                          mbox->expire_ext_id, &pending->record, NULL);
    i_free(pending);
    return ret;
}

static void
sieve_expire_transaction_save_rollback(struct mail_save_context *save_ctx)
{
    struct mailbox *box = save_ctx->transaction->box;
    struct sieve_expire_mailbox *mbox =
        MODULE_CONTEXT(box, sieve_expire_mailbox_module);
    struct sieve_expire_pending_save *pending =
        sieve_expire_pending_save_take(mbox, save_ctx->transaction);

    if (pending != NULL)
        i_free(pending);
    mbox->module_ctx.super.transaction_save_rollback(save_ctx);
}

static struct mailbox_transaction_context *
sieve_expire_transaction_begin(struct mailbox *box,
                               enum mailbox_transaction_flags flags,
                               const char *reason)
{
	struct sieve_expire_mailbox *mbox =
		MODULE_CONTEXT(box, sieve_expire_mailbox_module);
	struct mailbox_transaction_context *transaction;

	if (mbox != NULL && mbox->pending_expire_set)
		flags |= MAILBOX_TRANSACTION_FLAG_ASSIGN_UIDS;
	transaction = mbox->module_ctx.super.transaction_begin(box, flags, reason);
	return transaction;
}

static int
sieve_expire_mailbox_copy(struct mail_save_context *save_ctx, struct mail *mail)
{
	struct mailbox *dest_box = save_ctx->transaction->box;
	struct sieve_expire_mailbox *dest_mbox =
		MODULE_CONTEXT(dest_box, sieve_expire_mailbox_module);
	struct mail *src_mail = save_ctx->copy_src_mail;
	struct sieve_expire_mailbox *src_mbox;
	struct sieve_expire_record record;
	int ret;

	ret = dest_mbox->module_ctx.super.copy(save_ctx, mail);
	if (ret < 0 || src_mail == NULL || dest_mbox->expire_ext_id == UINT32_MAX)
		return ret;
	src_mbox = MODULE_CONTEXT(src_mail->box, sieve_expire_mailbox_module);
	if (src_mbox == NULL || src_mbox->expire_ext_id == UINT32_MAX)
		return ret;
	if (sieve_expire_record_lookup(src_mail->transaction->view,
				       src_mail->seq, src_mbox->expire_ext_id,
				       src_mbox->legacy_expire_ext_id,
				       &record)) {
		/* Defer until commit-pre, after the backend assigns a stable UID. */
		sieve_expire_pending_save_add(dest_mbox,
					      save_ctx->transaction,
					      save_ctx->dest_mail->seq, &record);
	}
	return ret;
}

bool
sieve_expire_read_record(struct mailbox *box, struct mail_index_view *view,
                         uint32_t seq, struct sieve_expire_record *record)
{
        struct sieve_expire_mailbox *mbox =
                MODULE_CONTEXT(box, sieve_expire_mailbox_module);
        return mbox != NULL && mbox->expire_ext_id != UINT32_MAX &&
                sieve_expire_record_lookup(view, seq, mbox->expire_ext_id,
                                           mbox->legacy_expire_ext_id, record);
}

static void
sieve_expire_mailbox_opened(struct mailbox *box)
{
	struct sieve_expire_mailbox *mbox =
		MODULE_CONTEXT(box, sieve_expire_mailbox_module);
	if (mbox != NULL && box->index != NULL &&
	    mbox->expire_ext_id == UINT32_MAX)
	mbox->expire_ext_id = mail_index_ext_register(
			box->index, "sieve-expire-v2", 0,
			sizeof(struct sieve_expire_record),
			_Alignof(struct sieve_expire_record));
	if (mbox != NULL && box->index != NULL)
		mbox->legacy_expire_ext_id = mail_index_ext_register(
			box->index, "sieve-expire", 0,
			sizeof(time_t), _Alignof(time_t));
	sieve_expire_activity(box);
}

static void
sieve_expire_mailbox_allocated(struct mailbox *box)
{
	struct mailbox_vfuncs *v = box->vlast;
	struct sieve_expire_mailbox *mbox;

	mbox = p_new(box->pool, struct sieve_expire_mailbox, 1);
	mbox->module_ctx.super = *v;
	mbox->expire_ext_id = UINT32_MAX;
	mbox->legacy_expire_ext_id = UINT32_MAX;
	box->vlast = &mbox->module_ctx.super;

	v->save_finish = sieve_expire_save_finish;
	v->copy = sieve_expire_mailbox_copy;
	v->transaction_begin = sieve_expire_transaction_begin;
	v->transaction_save_commit_pre =
		sieve_expire_transaction_save_commit_pre;
	v->transaction_save_rollback =
		sieve_expire_transaction_save_rollback;

	MODULE_CONTEXT_SET_SELF(box, sieve_expire_mailbox_module,
				&mbox->module_ctx);
	if (sieve_expire_pending_take(box, &mbox->pending_record)) {
		mbox->pending_expire_set = TRUE;
	}
}

/*
 * --------------------------------------------------------------------------
 * Store side effect
 *
 * It is attached to both explicit store/fileinto actions and the implicit
 * keep action.  The side effect is registered during interpreter
 * initialization, before Sieve actions are created.
 * --------------------------------------------------------------------------
 */

static int
sieve_expire_side_effect_pre_execute(
	const struct sieve_side_effect *seffect,
	const struct sieve_action_exec_env *aenv,
	void *tr_context, void **se_tr_context ATTR_UNUSED)
{
	const struct sieve_expire_result_context *rctx;
	struct act_store_transaction *store =
		(struct act_store_transaction *)tr_context;
	struct sieve_expire_mailbox *mbox;

	if (store == NULL || store->disabled || store->box == NULL)
		return SIEVE_EXEC_OK;

	rctx = (const struct sieve_expire_result_context *)
		sieve_result_extension_get_context(
			aenv->result, seffect->object.ext);
	if (rctx == NULL || (!rctx->expire_set && !rctx->expunge_set))
		return SIEVE_EXEC_OK;
	if (store->error_code != MAIL_ERROR_NONE)
		return store->error_code == MAIL_ERROR_TEMP ?
			SIEVE_EXEC_TEMP_FAILURE : SIEVE_EXEC_FAILURE;

	/* Reconcile the destination before its save transaction is opened.
	   Maildir's save-only fast path trusts the index's cached uidlist stat
	   and next_uid. An inconsistent index can describe a uidlist containing
	   a message that hasn't reached the index, allowing its UID to be reused.
	   Commit-pre UID assignment alone cannot repair that.
	   Force the backend to read and reconcile this mailbox, including when
	   the cached directory/uidlist stats still match. */
	if (mailbox_sync(store->box, MAILBOX_SYNC_FLAG_FORCE_RESYNC) < 0) {
		enum mail_error error;

		(void)mailbox_get_last_error(store->box, &error);
		return error == MAIL_ERROR_TEMP ?
			SIEVE_EXEC_TEMP_FAILURE : SIEVE_EXEC_FAILURE;
	}

	mbox = MODULE_CONTEXT(store->box, sieve_expire_mailbox_module);
	if (mbox == NULL) {
			sieve_expire_pending_set(store->box, &rctx->record);
	}
	else {
		mbox->pending_record = rctx->record;
		mbox->pending_expire_set = TRUE;
	}
	return SIEVE_EXEC_OK;
}

static void
sieve_expire_side_effect_rollback(
	const struct sieve_side_effect *seffect ATTR_UNUSED,
	const struct sieve_action_exec_env *aenv ATTR_UNUSED,
	void *tr_context, void *se_tr_context ATTR_UNUSED,
	bool success ATTR_UNUSED)
{
	struct act_store_transaction *store =
		(struct act_store_transaction *)tr_context;
	struct sieve_expire_mailbox *mbox;

	if (store == NULL || store->box == NULL)
		return;
	sieve_expire_pending_remove(store->box);
	mbox = MODULE_CONTEXT(store->box, sieve_expire_mailbox_module);
	if (mbox != NULL)
		mbox->pending_expire_set = FALSE;
}

static const struct sieve_side_effect_def sieve_expire_side_effect = {
	.obj_def = {
		.identifier = "expire",
		.operand = NULL,
		.code = 0
	},
	.precedence = 0,
	.to_action = &act_store,
	.pre_execute = sieve_expire_side_effect_pre_execute,
	.rollback = sieve_expire_side_effect_rollback
};

/*
 * Register the implicit side effect before any Sieve action is added.
 */
static int
sieve_expire_interpreter_run(const struct sieve_extension *ext,
			     const struct sieve_runtime_env *renv,
			     void *context ATTR_UNUSED,
			     bool deferred ATTR_UNUSED)
{
	sieve_result_add_implicit_side_effect(
		renv->result, &act_store, FALSE, ext,
		&sieve_expire_side_effect, NULL);

	sieve_result_add_implicit_side_effect(
		renv->result, NULL, TRUE, ext,
		&sieve_expire_side_effect, NULL);

	return SIEVE_EXEC_OK;
}

static const struct sieve_interpreter_extension
sieve_expire_interpreter_extension = {
	.ext_def = &sieve_expire_extension,
	.run = sieve_expire_interpreter_run
};

/*
 * --------------------------------------------------------------------------
 * Sieve command / operation
 * --------------------------------------------------------------------------
 */

static const struct sieve_command_def expire_command;
static const struct sieve_command_def expunge_command;
static bool sieve_expire_operation_dump(const struct sieve_dumptime_env *denv,
				       sieve_size_t *address);
static int sieve_expire_operation_execute(const struct sieve_runtime_env *renv,
					 sieve_size_t *address);
static const struct sieve_operation_def expire_operation = {
	.code = 0,
	.mnemonic = "EXPIRE",
	.ext_def = &sieve_expire_extension,
	.dump = sieve_expire_operation_dump,
	.execute = sieve_expire_operation_execute
};
static const struct sieve_operation_def expunge_operation = {
	.code = 1,
	.mnemonic = "EXPUNGE",
	.ext_def = &sieve_expire_extension,
	.dump = sieve_expire_operation_dump,
	.execute = sieve_expire_operation_execute
};
static const struct sieve_operation_def *expire_operations[] = {
	&expire_operation,
	&expunge_operation
};

static bool
sieve_expire_validator_load(const struct sieve_extension *ext,
			    struct sieve_validator *valdtr)
{
	sieve_validator_register_command(valdtr, ext, &expire_command);
	sieve_validator_register_command(valdtr, ext, &expunge_command);
	return TRUE;
}

static bool
sieve_expire_interpreter_load(const struct sieve_extension *ext,
			      const struct sieve_runtime_env *renv,
			      sieve_size_t *address ATTR_UNUSED)
{
	sieve_interpreter_extension_register(
		renv->interp, ext, &sieve_expire_interpreter_extension, NULL);
	return TRUE;
}

static const struct sieve_extension_def sieve_expire_extension = {
	.name = "expire",
	.validator_load = sieve_expire_validator_load,
	.interpreter_load = sieve_expire_interpreter_load,
	SIEVE_EXT_DEFINE_OPERATIONS(expire_operations)
};

static bool
cmd_expire_validate(struct sieve_validator *valdtr,
		    struct sieve_command *cmd)
{
	struct sieve_ast_argument *arg = cmd->first_positional;

	if (!sieve_validate_positional_argument(
		    valdtr, cmd, arg, "duration", 1, SAAT_STRING))
		return FALSE;

	return sieve_validator_argument_activate(valdtr, cmd, arg, FALSE);
}

static bool
cmd_expire_generate(const struct sieve_codegen_env *cgenv,
		    struct sieve_command *cmd)
{
	sieve_operation_emit(cgenv->sblock, cmd->ext, expire_operations[0]);
	return sieve_generate_arguments(cgenv, cmd, NULL);
}

static bool
cmd_expunge_generate(const struct sieve_codegen_env *cgenv,
		     struct sieve_command *cmd)
{
	sieve_operation_emit(cgenv->sblock, cmd->ext, expire_operations[1]);
	return sieve_generate_arguments(cgenv, cmd, NULL);
}

static const struct sieve_command_def expire_command = {
	.identifier = "expire",
	.type = SCT_COMMAND,
	.positional_args = 1,
	.subtests = 0,
	.block_allowed = FALSE,
	.block_required = FALSE,
	.validate = cmd_expire_validate,
	.generate = cmd_expire_generate
};

static const struct sieve_command_def expunge_command = {
	.identifier = "expunge",
	.type = SCT_COMMAND,
	.positional_args = 1,
	.subtests = 0,
	.block_allowed = FALSE,
	.block_required = FALSE,
	.validate = cmd_expire_validate,
	.generate = cmd_expunge_generate
};

static bool
sieve_expire_operation_dump(const struct sieve_dumptime_env *denv,
			    sieve_size_t *address)
{
	sieve_code_dumpf(denv, "%s",
			 sieve_operation_mnemonic(denv->oprtn));
	sieve_code_descend(denv);

	if (!sieve_opr_string_dump(denv, address, "duration"))
		return FALSE;

	return TRUE;
}

static int
sieve_expire_operation_execute(const struct sieve_runtime_env *renv,
			       sieve_size_t *address)
{
        const struct sieve_operation *oprtn = renv->oprtn;
        const struct sieve_extension *ext = oprtn->ext;
	struct act_expire_context *act;
	string_t *duration;
	pool_t pool;
	int ret;

	ret = sieve_opr_string_read(renv, address, "duration", &duration);
	if (ret <= 0)
		return ret;

	pool = sieve_result_pool(renv->result);
	act = p_new(pool, struct act_expire_context, 1);
	act->duration = p_strdup(pool, str_c(duration));
	act->expunge = oprtn->def == expire_operations[1];

	if (sieve_result_add_action(
		    renv, ext, act->expunge ? "expunge" : "expire",
		    act->expunge ? &act_expunge : &act_expire,
		    NULL, act, 1, FALSE) < 0)
		return SIEVE_EXEC_FAILURE;

	return SIEVE_EXEC_OK;
}

/*
 * --------------------------------------------------------------------------
 * Plugin entry points
 * --------------------------------------------------------------------------
 */

struct mail_storage_hooks sieve_expire_mail_storage_hooks = {
	.mailbox_allocated = sieve_expire_mailbox_allocated,
	.mailbox_opened = sieve_expire_mailbox_opened
};

int sieve_expire_plugin_load(struct sieve_instance *svinst, void **context)
{
        const struct sieve_extension *ext;

        if (sieve_extension_register(svinst, &sieve_expire_extension,
                                     FALSE, &ext) < 0)
                return -1;
        *context = (void *)ext;
        return 0;
}

void sieve_expire_plugin_unload(struct sieve_instance *svinst ATTR_UNUSED,
                                void *context)
{
        const struct sieve_extension *ext = context;
        if (ext != NULL)
                sieve_extension_unregister(ext);
}

bool sieve_expire_hooks_registered;

void sieve_expire_plugin_init(void)
{
	/* The mailbox context ID is an exported shared symbol between the
	   Dovecot and Pigeonhole module copies. Register the storage hooks
	   once per process, even when both module loaders initialize it. */
	if (!sieve_expire_hooks_registered) {
		mail_storage_hooks_add_internal(&sieve_expire_mail_storage_hooks);
		mail_storage_hooks_add_internal(&sieve_expire_maintenance_hooks);
		sieve_expire_hooks_registered = TRUE;
	}
}

void sieve_expire_plugin_deinit(void)
{
	/* The Dovecot mail-plugin reference can outlive a Sieve instance. */
}

const char *sieve_expire_plugin_version = PIGEONHOLE_ABI_VERSION;
