/* Account-scoped state is serialized by the existing maintenance lock.
   Never reopen/replace its inode: closing another fd loses fcntl locks. */
#include "lib.h"
#include "str.h"
#include "str-parse.h"
#include "safe-mkstemp.h"
#include "write-full.h"
#include "file-lock.h"
#include "mail-user.h"
#include "sieve-expire-settings.h"
#include <fcntl.h>
#include <sys/stat.h>
#include <unistd.h>
#include <time.h>
#include <stdio.h>

#define STATE_LIMIT 64

static bool valid_file(const struct stat *st)
{
        return S_ISREG(st->st_mode) && st->st_uid == geteuid() &&
                (st->st_mode & 077) == 0 && st->st_nlink == 1;
}

static int read_start(const char *path, uintmax_t *last_r, const char **error_r)
{
        struct stat before;
        if (lstat(path, &before) < 0) {
                if (errno == ENOENT) return 0;
                *error_r = t_strdup_printf("lstat failed: %m"); return -1;
        }
        /* Reject hardlinks before opening: a sidecar linked to the coordination
           inode must not be reopened even on a subsequently rejected pass. */
        if (!valid_file(&before)) {
                *error_r = "unexpected file type, ownership, permissions or hardlinks";
                return -1;
        }
        int fd = open(path, O_RDONLY | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC);
        if (fd < 0) {
                *error_r = t_strdup_printf("open failed: %m"); return -1;
        }
        struct stat st;
        char data[STATE_LIMIT + 1];
        size_t used = 0;
        int ret = -1;
        if (fstat(fd, &st) < 0) {
                *error_r = t_strdup_printf("fstat failed: %m"); goto out;
        }
        if (!valid_file(&st) || st.st_ino != before.st_ino ||
            st.st_dev != before.st_dev) {
                *error_r = "unexpected file identity, type, ownership, permissions or hardlinks";
                goto out;
        }
        while (used < sizeof(data)) {
                ssize_t n = read(fd, data + used, sizeof(data) - used);
                if (n < 0) {
                        if (errno == EINTR) continue;
                        *error_r = t_strdup_printf("read failed: %m"); goto out;
                }
                if (n == 0) break;
                used += n;
        }
        if (used < 5 || used > STATE_LIMIT || memcmp(data, "v1 ", 3) != 0 ||
            data[used-1] != '\n' || memchr(data, '\0', used) != NULL) {
                *error_r = "malformed or unsupported cooldown record"; goto out;
        }
        data[used-1] = '\0';
        if (str_to_uintmax(data + 3, last_r) < 0 || *last_r > INT64_MAX) {
                *error_r = "invalid cooldown timestamp"; goto out;
        }
        ret = 1;
out:
        if (close(fd) < 0) {
                *error_r = t_strdup_printf("close failed: %m"); ret = -1;
        }
        return ret;
}

static bool publish_start(const char *path, uintmax_t now, const char **error_r)
{
        string_t *temp = t_str_new(256);
        str_append(temp, path); str_append(temp, ".tmp.");
        int fd = safe_mkstemp(temp, 0600, (uid_t)-1, (gid_t)-1);
        if (fd < 0) {
                *error_r = t_strdup_printf("temporary file creation failed: %m");
                return FALSE;
        }
        const char *record = t_strdup_printf("v1 %ju\n", now);
        bool ok = write_full(fd, record, strlen(record)) == 0;
        if (!ok) *error_r = t_strdup_printf("write failed: %m");
        if (close(fd) < 0) {
                if (ok) *error_r = t_strdup_printf("close failed: %m");
                ok = FALSE;
        }
        /* Rename is the admission boundary. No fsync is required for this
           performance state; normal service restart differs from power loss. */
        if (ok && rename(str_c(temp), path) < 0) {
                *error_r = t_strdup_printf("rename failed: %m"); ok = FALSE;
        }
        if (!ok && unlink(str_c(temp)) < 0 && errno != ENOENT)
                *error_r = t_strdup_printf("%s; temporary cleanup failed: %m", *error_r);
        return ok;
}

bool sieve_expire_cooldown_admit(struct mail_user *user,
                               struct file_lock *lock, uintmax_t seconds)
{
        /* Disabled means no extra stat/open/read/write, even for invalid state. */
        if (seconds == 0) return TRUE;
        const char *path = t_strconcat(file_lock_get_path(lock), ".cooldown", NULL);
        const char *error;
        uintmax_t last = 0;
        int ret = read_start(path, &last, &error);
        if (ret < 0) {
                e_warning(user->event, "sieve-expire: cooldown state %s: %s; maintenance skipped", path, error);
                return FALSE;
        }
        time_t sampled = time(NULL);
        if (sampled == (time_t)-1 || sampled < 0 || (uintmax_t)sampled > INT64_MAX) {
                e_warning(user->event, "sieve-expire: cooldown wall clock unavailable; maintenance skipped");
                return FALSE;
        }
        uintmax_t now = (uintmax_t)sampled;
        if (ret > 0 && last > now) {
                e_warning(user->event, "sieve-expire: cooldown state %s has future timestamp %ju (now %ju); maintenance skipped", path, last, now);
                return FALSE;
        }
        /* Subtract only after ordering; never add interval to a timestamp.
           The full native uintmax_t setting range is therefore supported. */
        if (ret > 0 && now - last < seconds) {
                e_debug(user->event, "sieve-expire: cooldown active path=%s start=%ju elapsed=%ju seconds=%ju; skipped", path, last, now-last, seconds);
                return FALSE;
        }
        if (!publish_start(path, now, &error)) {
                e_warning(user->event, "sieve-expire: cooldown publication %s: %s; maintenance skipped", path, error);
                return FALSE;
        }
        e_debug(user->event, "sieve-expire: cooldown admitted path=%s start=%ju seconds=%ju", path, now, seconds);
        return TRUE;
}
