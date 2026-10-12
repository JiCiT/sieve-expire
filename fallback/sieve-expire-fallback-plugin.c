/* Independent no-op implementation of the version-0 "expire" extension.
 * Keep the operation table and operand encoding compatible with the normal
 * plugin. There are deliberately no mail-storage, index or maintenance hooks.
 */
#include "lib.h"
#include "str.h"
#include "sieve.h"
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

#include <dlfcn.h>
#include <errno.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

int sieve_expire_fallback_plugin_load(struct sieve_instance *, void **);
void sieve_expire_fallback_plugin_unload(struct sieve_instance *, void *);
void sieve_expire_fallback_plugin_init(void);
void sieve_expire_fallback_plugin_deinit(void);
extern const char *sieve_expire_fallback_plugin_version;

static const struct sieve_extension_def fallback_extension;
static const struct sieve_command_def expire_command, expunge_command;

/* Match the normal parser, including units and overflow checks; do not invent
 * fallback-specific syntax or silently accept invalid duration arguments. */
static bool parse_duration(const char *value, int64_t *seconds_r)
{
	char *end;
	int64_t number, multiplier = 1;
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
		case 's': multiplier = 1; break;
		case 'm': multiplier = 60; break;
		case 'h': multiplier = 60 * 60; break;
		case 'd': multiplier = 24 * 60 * 60; break;
		case 'w': multiplier = 7 * 24 * 60 * 60; break;
		default: return FALSE;
		}
	}
	if (number > INT64_MAX / multiplier)
		return FALSE;
	*seconds_r = number * multiplier;
	return TRUE;
}

struct ignored_action {
	const char *duration;
	bool expunge;
};

static void action_print(const struct sieve_action *action,
			const struct sieve_result_print_env *penv, bool *keep)
{
	const struct ignored_action *ctx = action->context;
	sieve_result_action_printf(penv, "ignore %s deadline %s (fallback)",
		ctx->expunge ? "expunge" : "expire", ctx->duration);
	/* Printer accounting only: this action does not cancel implicit keep. */
	*keep = TRUE;
}

static int action_execute(const struct sieve_action_exec_env *aenv,
			  void *tr_context ATTR_UNUSED, bool *keep ATTR_UNUSED)
{
	const struct ignored_action *ctx = aenv->action->context;
	int64_t duration, now;
	if (ctx == NULL || !parse_duration(ctx->duration, &duration)) {
		sieve_result_error(aenv, "invalid expire duration");
		return SIEVE_EXEC_FAILURE;
	}
	now = (int64_t)time(NULL);
	if (duration > INT64_MAX - now) {
		sieve_result_error(aenv, "expire duration is too large");
		return SIEVE_EXEC_FAILURE;
	}
	/* No result side effects, timestamp assignment, keep override or I/O. */
	return SIEVE_EXEC_OK;
}

static const struct sieve_action_def expire_action = {
	.name = "expire", .print = action_print, .execute = action_execute
};
static const struct sieve_action_def expunge_action = {
	.name = "expunge", .print = action_print, .execute = action_execute
};

static bool operation_dump(const struct sieve_dumptime_env *denv,
			   sieve_size_t *address)
{
	sieve_code_dumpf(denv, "%s", sieve_operation_mnemonic(denv->oprtn));
	sieve_code_descend(denv);
	return sieve_opr_string_dump(denv, address, "duration");
}

static int operation_execute(const struct sieve_runtime_env *renv,
			     sieve_size_t *address)
{
	string_t *duration;
	struct ignored_action *ctx;
	pool_t pool;
	int ret = sieve_opr_string_read(renv, address, "duration", &duration);
	if (ret <= 0)
		return ret;
	pool = sieve_result_pool(renv->result);
	ctx = p_new(pool, struct ignored_action, 1);
	ctx->duration = p_strdup(pool, str_c(duration));
	ctx->expunge = renv->oprtn->def->code == 1;
	if (sieve_result_add_action(renv, renv->oprtn->ext,
		ctx->expunge ? "expunge" : "expire",
		ctx->expunge ? &expunge_action : &expire_action,
		NULL, ctx, 1, FALSE) < 0)
		return SIEVE_EXEC_FAILURE;
	return SIEVE_EXEC_OK;
}

/* Version-0 ABI requires exactly TWO entries: a single-operation table would
 * stop consuming the selector byte in already compiled normal binaries. */
static const struct sieve_operation_def expire_operation = {
	.code = 0, .mnemonic = "EXPIRE", .ext_def = &fallback_extension,
	.dump = operation_dump, .execute = operation_execute
};
static const struct sieve_operation_def expunge_operation = {
	.code = 1, .mnemonic = "EXPUNGE", .ext_def = &fallback_extension,
	.dump = operation_dump, .execute = operation_execute
};
static const struct sieve_operation_def *operations[] = {
	&expire_operation, &expunge_operation
};

static bool command_validate(struct sieve_validator *validator,
			     struct sieve_command *cmd)
{
	struct sieve_ast_argument *arg = cmd->first_positional;
	if (!sieve_validate_positional_argument(validator, cmd, arg,
					       "duration", 1, SAAT_STRING))
		return FALSE;
	return sieve_validator_argument_activate(validator, cmd, arg, FALSE);
}

static bool command_generate(const struct sieve_codegen_env *cgenv,
			     struct sieve_command *cmd)
{
	sieve_operation_emit(cgenv->sblock, cmd->ext,
		operations[cmd->def == &expunge_command ? 1 : 0]);
	return sieve_generate_arguments(cgenv, cmd, NULL);
}

static const struct sieve_command_def expire_command = {
	.identifier = "expire", .type = SCT_COMMAND, .positional_args = 1,
	.validate = command_validate, .generate = command_generate
};
static const struct sieve_command_def expunge_command = {
	.identifier = "expunge", .type = SCT_COMMAND, .positional_args = 1,
	.validate = command_validate, .generate = command_generate
};

static bool validator_load(const struct sieve_extension *ext,
			   struct sieve_validator *validator)
{
	sieve_validator_register_command(validator, ext, &expire_command);
	sieve_validator_register_command(validator, ext, &expunge_command);
	return TRUE;
}

static const struct sieve_extension_def fallback_extension = {
	.name = "expire", .version = 0, .validator_load = validator_load,
	SIEVE_EXT_DEFINE_OPERATIONS(operations)
};

static bool normal_module_present(void)
{
	/* Target module-dir opens ALL selected modules RTLD_GLOBAL before calling
 * initializers/Sieve load callbacks. Check symbols, not enabled extensions:
 * get_by_name() hides a registered-but-not-yet-enabled normal extension.
 * This also rejects the normal module loaded through mail_plugins.
 */
	return dlsym(RTLD_DEFAULT, "sieve_expire_plugin_load") != NULL;
}

static void announce_fallback(void)
{
	static bool announced;
	if (!announced) {
		i_warning("sieve-expire fallback active: expire/expunge are no-ops; no deadlines are stored or enforced");
		announced = TRUE;
	}
}

int sieve_expire_fallback_plugin_load(struct sieve_instance *svinst,
				      void **context)
{
	const struct sieve_extension *ext;
	const struct sieve_extension *const *all;
	unsigned int count, i;
	*context = NULL;
	if (normal_module_present()) {
		i_error("sieve-expire fallback: normal implementation is also loaded; select exactly one");
		return -1;
	}
	all = sieve_extensions_get_all(svinst, &count);
	for (i = 0; i < count; i++) {
		if (all[i]->def != NULL && all[i]->def != &fallback_extension &&
		    strcmp(all[i]->def->name, "expire") == 0) {
			i_error("sieve-expire fallback: another expire extension is registered; refusing coexistence");
			return -1;
		}
	}
	if (sieve_extension_register(svinst, &fallback_extension, FALSE, &ext) < 0)
		return -1;
	*context = (void *)ext;
	/* Pigeonhole's Sieve-only loader calls _load, not module_dir_init(). */
	announce_fallback();
	return 0;
}

void sieve_expire_fallback_plugin_unload(struct sieve_instance *svinst ATTR_UNUSED,
					 void *context)
{
	if (context != NULL)
		sieve_extension_unregister(context);
}

void sieve_expire_fallback_plugin_init(void)
{
	if (normal_module_present())
		i_fatal("sieve-expire fallback: normal implementation is also loaded; select exactly one");
	announce_fallback();
}

void sieve_expire_fallback_plugin_deinit(void) {}

const char *sieve_expire_fallback_plugin_version = PIGEONHOLE_ABI_VERSION;
