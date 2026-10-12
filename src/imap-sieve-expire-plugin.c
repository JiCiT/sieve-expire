/* Active IMAP adapter: opportunity signals are never worker entry points. */
#include "lib.h"
#include "array.h"
#include "ioloop.h"
#include "module-context.h"
#include "module-dir.h"
#include "mail-user.h"
#include "imap-client.h"
#include "imap-commands.h"
#include "imap-common.h"
#include "sieve-expire-plugin.h"
#include <unistd.h>

struct expire_client {
        union imap_module_context module;
        struct client *client;
        struct mail_user *user;
        struct ioloop *owner;
        struct ioloop_context *io_context;
        struct timeout *timeout;
        unsigned int hook_depth;
        bool ready, teardown;
};
struct expire_command {
        union imap_module_context module;
        command_func_t *idle_continue;
        bool core_idle, external, opportunity_seen;
};
MODULE_CONTEXT_DEFINE_INIT(expire_imap_module, &imap_module_register);
void imap_sieve_expire_plugin_init(struct module *module);
void imap_sieve_expire_plugin_deinit(void);
static struct module *expire_module;
static void (*next_created)(struct client **client);

static struct expire_client *client_context(struct client *client)
{ return MODULE_CONTEXT(client, expire_imap_module); }

static bool safe_entry(struct expire_client *ctx)
{
        struct client *client = ctx->client;
        if (client->user != ctx->user || !ctx->ready || ctx->teardown || client->destroyed || client->disconnected ||
            client->hibernated || client->logged_out || client->user->deinitializing ||
            !sieve_expire_pending(client->user) || ctx->owner != current_ioloop ||
            ctx->io_context == NULL || ctx->io_context != io_loop_get_current_context(ctx->owner) ||
            geteuid() != client->user->uid || getegid() != client->user->gid ||
            ctx->hook_depth != 0 || client->handling_input || client->syncing ||
            client->output_cmd_lock != NULL || client->mailbox_change_lock != NULL ||
            client->notify_ctx != NULL ||
            (array_is_created(&client->search_updates) && array_count(&client->search_updates) != 0) ||
            !sieve_expire_storage_idle(client->user)) return FALSE;
        struct client_command_context *cmd = client->command_queue;
        if (cmd == NULL) return client->command_queue_size == 0 && client->input_lock == NULL;
        if (client->command_queue_size != 1 || cmd->next != NULL || cmd->executing || cmd->cancel ||
            cmd->state != CLIENT_COMMAND_STATE_WAIT_INPUT || client->input_lock != cmd) return FALSE;
        struct expire_command *command = MODULE_CONTEXT(cmd, expire_imap_module);
        /* Only core's verified continuation establishes that its private sync
           context is cleared and sync_pending drained before WAIT_INPUT. */
        const struct command *registered = command_find("IDLE");
        return command != NULL && command->core_idle && command->idle_continue != NULL &&
                cmd->func == command->idle_continue && registered != NULL && registered->func == cmd_idle;
}
static void maintenance_timeout(struct expire_client *ctx)
{
        timeout_remove(&ctx->timeout);
        if (!safe_entry(ctx)) {
                sieve_expire_consume(ctx->client->user);
                e_debug(ctx->client->event, "sieve-expire: unsafe maintenance opportunity consumed");
                return;
        }
        sieve_expire_maintenance_run(ctx->client->user);
}
static void command_pre(struct client_command_context *cmd)
{
        struct expire_client *ctx = client_context(cmd->client);
        if (ctx == NULL) return;
        ctx->hook_depth++;
        struct expire_command *command = MODULE_CONTEXT(cmd, expire_imap_module);
        if (command == NULL) {
                command = p_new(cmd->pool, struct expire_command, 1);
                MODULE_CONTEXT_SET_SELF(cmd, expire_imap_module, &command->module);
                const struct command *registered = command_find("IDLE");
                command->core_idle = cmd->func == cmd_idle && registered != NULL && registered->func == cmd_idle;
        }
        command->external = !command->opportunity_seen && ctx->ready && !ctx->teardown && !cmd->cancel && !cmd->internal &&
                !cmd->client->disconnected && !cmd->client->hibernated &&
                /* imap_state_import_idle_cmd_tag invokes command_exec without
                   cmd->internal. Core clears this flag inside restored cmd_idle. */
                !cmd->client->state_import_idle_continue &&
                sieve_expire_user_ready(cmd->client->user);
        /* Claim at most one opportunity per command, including an imported or
           cancelled command. Output/continuation re-entry is not fresh activity
           and must not turn an unsafe opportunity into a zero-delay retry loop. */
        command->opportunity_seen = TRUE;
        if (command->external) sieve_expire_activity_begin(cmd->client->user);
}
static void command_post(struct client_command_context *cmd)
{
        struct expire_client *ctx = client_context(cmd->client);
        if (ctx == NULL) return;
        struct expire_command *command = MODULE_CONTEXT(cmd, expire_imap_module);
        if (command->core_idle && command->idle_continue == NULL && cmd->func != cmd_idle)
                command->idle_continue = cmd->func;
        if (command->external) {
                /* Activity on an already-selected mailbox is a real opportunity;
                   initialization, restoration, cancellation and internal work are not. */
                if (!cmd->cancel && !cmd->client->disconnected && cmd->client->mailbox != NULL)
                        sieve_expire_activity(cmd->client->mailbox);
                sieve_expire_activity_end(cmd->client->user);
                command->external = FALSE;
        }
        i_assert(ctx->hook_depth > 0); ctx->hook_depth--;
        if (cmd->cancel || cmd->client->disconnected || cmd->client->hibernated) {
                sieve_expire_consume(cmd->client->user);
                timeout_remove(&ctx->timeout);
                return;
        }
        if (ctx->ready && !ctx->teardown && sieve_expire_pending(cmd->client->user) && ctx->timeout == NULL &&
            current_ioloop == ctx->owner && ctx->io_context == io_loop_get_current_context(ctx->owner))
                ctx->timeout = timeout_add_to(ctx->owner, 0, maintenance_timeout, ctx);
}
static void cancel_pending(void *context)
{
        struct expire_client *ctx = context;
        ctx->teardown = TRUE; ctx->ready = FALSE;
        timeout_remove(&ctx->timeout);
}
static void client_init(struct client *client)
{
        struct expire_client *ctx = client_context(client);
        ctx->module.super.init(client);
        ctx->ready = sieve_expire_user_ready(client->user);
}
static void expire_client_destroy(struct client *client, const char *reason)
{
        struct expire_client *ctx = client_context(client);
        ctx->teardown = TRUE; ctx->ready = FALSE;
        timeout_remove(&ctx->timeout);
        sieve_expire_teardown(client->user);
        /* Includes successful hibernation. Never run a final/transfer-time pass. */
        ctx->module.super.destroy(client, reason);
}
static void client_created(struct client **clientp)
{
        struct client *client = *clientp;
        if (mail_user_is_plugin_loaded(client->user, expire_module)) {
                struct expire_client *ctx = p_new(client->pool, struct expire_client, 1);
                ctx->module.super = client->v;
                ctx->client = client; ctx->user = client->user; ctx->owner = current_ioloop;
                sieve_expire_set_cancel(client->user, cancel_pending, ctx);
                ctx->io_context = io_loop_get_current_context(ctx->owner);
                client->v.init = client_init; client->v.destroy = expire_client_destroy;
                MODULE_CONTEXT_SET_SELF(client, expire_imap_module, &ctx->module);
        }
        if (next_created != NULL) next_created(clientp);
}
void imap_sieve_expire_plugin_init(struct module *module)
{
        expire_module = module;
        next_created = imap_client_created_hook_set(client_created);
        command_hook_register(command_pre, command_post);
}
void imap_sieve_expire_plugin_deinit(void)
{
        command_hook_unregister(command_pre, command_post);
        imap_client_created_hook_set(next_created);
}
const char *imap_sieve_expire_plugin_version = DOVECOT_ABI_VERSION;
const char *imap_sieve_expire_plugin_dependencies[] = { "sieve_expire", NULL };
const char imap_sieve_expire_plugin_binary_dependency[] = "imap";
