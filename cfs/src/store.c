#include "store.h"

#include <openssl/evp.h>
#include <stdbool.h>
#include <sqlite3.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

struct cfs_store {
    sqlite3 *db;
    int64_t version; /* cluster version: the number of the last applied change */
    int64_t bytes;   /* total size of the entries' data, kept against CFS_TREE_MAX */
    char error[CFS_PATH_MAX + 512];
};

static const char SCHEMA[] =
    "CREATE TABLE IF NOT EXISTS tree ("
    " path TEXT PRIMARY KEY NOT NULL,"
    " version INTEGER NOT NULL,"
    " mtime INTEGER NOT NULL,"
    " data BLOB NOT NULL) WITHOUT ROWID;"
    "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY NOT NULL, value INTEGER NOT NULL) WITHOUT ROWID;"
    "CREATE TABLE IF NOT EXISTS locks ("
    " name TEXT PRIMARY KEY NOT NULL,"
    " owner TEXT NOT NULL,"
    " node INTEGER NOT NULL,"
    " expires INTEGER NOT NULL) WITHOUT ROWID;"
    "INSERT OR IGNORE INTO meta (key, value) VALUES ('version', 0), ('next_id', 100);";

static void set_error(cfs_store *s, const char *what)
{
    snprintf(s->error, sizeof(s->error), "%s: %s", what, sqlite3_errmsg(s->db));
}

const char *cfs_store_error(const cfs_store *s)
{
    return s->error;
}

void cfs_store_clear_error(cfs_store *s)
{
    s->error[0] = '\0';
}

static int exec(cfs_store *s, const char *sql)
{
    char *msg = NULL;
    if (sqlite3_exec(s->db, sql, NULL, NULL, &msg) != SQLITE_OK) {
        snprintf(s->error, sizeof(s->error), "%s: %s", sql, msg ? msg : "unknown error");
        sqlite3_free(msg);
        return CFS_INTERNAL;
    }
    return CFS_OK;
}

static int64_t query_i64(cfs_store *s, const char *sql, int64_t fallback)
{
    sqlite3_stmt *st;
    int64_t v = fallback;
    if (sqlite3_prepare_v2(s->db, sql, -1, &st, NULL) != SQLITE_OK)
        return fallback;
    if (sqlite3_step(st) == SQLITE_ROW && sqlite3_column_type(st, 0) != SQLITE_NULL)
        v = sqlite3_column_int64(st, 0);
    sqlite3_finalize(st);
    return v;
}

cfs_store *cfs_store_open(const char *db_path, char *err, size_t errlen)
{
    cfs_store *s = calloc(1, sizeof(*s));
    if (!s) {
        snprintf(err, errlen, "out of memory");
        return NULL;
    }
    if (sqlite3_open_v2(db_path, &s->db, SQLITE_OPEN_READWRITE | SQLITE_OPEN_CREATE | SQLITE_OPEN_NOMUTEX, NULL) !=
        SQLITE_OK) {
        snprintf(err, errlen, "cannot open %s: %s", db_path, s->db ? sqlite3_errmsg(s->db) : "out of memory");
        sqlite3_close(s->db);
        free(s);
        return NULL;
    }
    /* WAL with a full sync: a change the daemon acknowledged survives a power cut. */
    if (exec(s, "PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL; PRAGMA foreign_keys=ON;") != CFS_OK ||
        exec(s, SCHEMA) != CFS_OK) {
        snprintf(err, errlen, "%s", s->error);
        cfs_store_close(s);
        return NULL;
    }
    s->version = query_i64(s, "SELECT value FROM meta WHERE key = 'version'", 0);
    s->bytes = query_i64(s, "SELECT COALESCE(SUM(length(data)), 0) FROM tree", 0);
    return s;
}

void cfs_store_close(cfs_store *s)
{
    if (!s)
        return;
    sqlite3_close(s->db);
    free(s);
}

/* ---- Lookups ---------------------------------------------------------------------------------------------------- */

/* CFS_OK with the entry's version and size, CFS_NOT_FOUND, or CFS_INTERNAL. */
static int entry_info(cfs_store *s, const char *path, int64_t *version, int64_t *size)
{
    sqlite3_stmt *st;
    if (sqlite3_prepare_v2(s->db, "SELECT version, length(data) FROM tree WHERE path = ?", -1, &st, NULL) !=
        SQLITE_OK) {
        set_error(s, "lookup");
        return CFS_INTERNAL;
    }
    sqlite3_bind_text(st, 1, path, -1, SQLITE_STATIC);
    int rc = sqlite3_step(st);
    int result;
    if (rc == SQLITE_ROW) {
        *version = sqlite3_column_int64(st, 0);
        *size = sqlite3_column_int64(st, 1);
        result = CFS_OK;
    } else if (rc == SQLITE_DONE) {
        result = CFS_NOT_FOUND;
    } else {
        set_error(s, "lookup");
        result = CFS_INTERNAL;
    }
    sqlite3_finalize(st);
    return result;
}

/* The key range of everything below directory `dir`: [dir + "/", dir + "0"), since '0' follows '/' in ASCII. */
static void subtree_range(const char *dir, char *lo, char *hi, size_t cap)
{
    if (strcmp(dir, "/") == 0) {
        snprintf(lo, cap, "/");
        snprintf(hi, cap, "0");
    } else {
        snprintf(lo, cap, "%s/", dir);
        snprintf(hi, cap, "%s0", dir);
    }
}

/* 1 when at least one entry lies below `dir`, 0 when none, -1 on error. */
static int has_children(cfs_store *s, const char *dir)
{
    char lo[CFS_PATH_MAX + 2], hi[CFS_PATH_MAX + 2];
    subtree_range(dir, lo, hi, sizeof(lo));
    sqlite3_stmt *st;
    if (sqlite3_prepare_v2(s->db, "SELECT 1 FROM tree WHERE path >= ? AND path < ? LIMIT 1", -1, &st, NULL) !=
        SQLITE_OK) {
        set_error(s, "children");
        return -1;
    }
    sqlite3_bind_text(st, 1, lo, -1, SQLITE_TRANSIENT);
    sqlite3_bind_text(st, 2, hi, -1, SQLITE_TRANSIENT);
    int rc = sqlite3_step(st);
    sqlite3_finalize(st);
    if (rc == SQLITE_ROW)
        return 1;
    if (rc == SQLITE_DONE)
        return 0;
    set_error(s, "children");
    return -1;
}

/* A file may not sit where a directory is (an entry below it) nor below another file. */
static int check_shape(cfs_store *s, const char *path)
{
    int children = has_children(s, path);
    if (children < 0)
        return CFS_INTERNAL;
    if (children) {
        snprintf(s->error, sizeof(s->error), "%s is a directory", path);
        return CFS_INVALID;
    }
    char parent[CFS_PATH_MAX + 1];
    snprintf(parent, sizeof(parent), "%s", path);
    for (char *slash = strrchr(parent, '/'); slash && slash != parent; slash = strrchr(parent, '/')) {
        *slash = '\0';
        int64_t v, size;
        int rc = entry_info(s, parent, &v, &size);
        if (rc == CFS_OK) {
            snprintf(s->error, sizeof(s->error), "%s is a file", parent);
            return CFS_INVALID;
        }
        if (rc != CFS_NOT_FOUND)
            return rc;
    }
    return CFS_OK;
}

static int check_expected(int found, int64_t current, int64_t expected)
{
    if (expected == CFS_ANY_VERSION)
        return CFS_OK;
    if (expected == CFS_MUST_NOT_EXIST)
        return found == CFS_OK ? CFS_CONFLICT : CFS_OK;
    if (found != CFS_OK)
        return CFS_NOT_FOUND;
    return current == expected ? CFS_OK : CFS_CONFLICT;
}

/* ---- Changes: each one is a single transaction that also bumps the cluster version --------------------------------- */

static int begin(cfs_store *s)
{
    return exec(s, "BEGIN IMMEDIATE");
}

static void rollback(cfs_store *s)
{
    char *msg = NULL;
    sqlite3_exec(s->db, "ROLLBACK", NULL, NULL, &msg);
    sqlite3_free(msg);
}

/* Write the new cluster version and commit. On success, the in-memory counter follows. */
static int commit_version(cfs_store *s, int64_t version)
{
    sqlite3_stmt *st;
    if (sqlite3_prepare_v2(s->db, "UPDATE meta SET value = ? WHERE key = 'version'", -1, &st, NULL) != SQLITE_OK) {
        set_error(s, "version");
        return CFS_INTERNAL;
    }
    sqlite3_bind_int64(st, 1, version);
    int rc = sqlite3_step(st);
    sqlite3_finalize(st);
    if (rc != SQLITE_DONE) {
        set_error(s, "version");
        return CFS_INTERNAL;
    }
    if (exec(s, "COMMIT") != CFS_OK)
        return CFS_INTERNAL;
    s->version = version;
    return CFS_OK;
}

static int write_row(cfs_store *s, const char *path, int64_t version, int64_t mtime, const uint8_t *data, size_t len)
{
    sqlite3_stmt *st;
    if (sqlite3_prepare_v2(s->db,
                           "INSERT INTO tree (path, version, mtime, data) VALUES (?, ?, ?, ?) "
                           "ON CONFLICT(path) DO UPDATE SET version = excluded.version, mtime = excluded.mtime, "
                           "data = excluded.data",
                           -1, &st, NULL) != SQLITE_OK) {
        set_error(s, "write");
        return CFS_INTERNAL;
    }
    sqlite3_bind_text(st, 1, path, -1, SQLITE_STATIC);
    sqlite3_bind_int64(st, 2, version);
    sqlite3_bind_int64(st, 3, mtime);
    sqlite3_bind_blob(st, 4, len ? (const void *)data : "", (int)len, SQLITE_STATIC);
    int rc = sqlite3_step(st);
    sqlite3_finalize(st);
    if (rc != SQLITE_DONE) {
        set_error(s, "write");
        return CFS_INTERNAL;
    }
    return CFS_OK;
}

static int delete_row(cfs_store *s, const char *path)
{
    sqlite3_stmt *st;
    if (sqlite3_prepare_v2(s->db, "DELETE FROM tree WHERE path = ?", -1, &st, NULL) != SQLITE_OK) {
        set_error(s, "delete");
        return CFS_INTERNAL;
    }
    sqlite3_bind_text(st, 1, path, -1, SQLITE_STATIC);
    int rc = sqlite3_step(st);
    sqlite3_finalize(st);
    if (rc != SQLITE_DONE) {
        set_error(s, "delete");
        return CFS_INTERNAL;
    }
    return CFS_OK;
}

int cfs_store_put(cfs_store *s, const char *path, const uint8_t *data, size_t len, int64_t expected, int64_t now,
                  int64_t *new_version)
{
    if (len > CFS_FILE_MAX)
        return CFS_TOO_LARGE;
    if (begin(s) != CFS_OK)
        return CFS_INTERNAL;
    int64_t current = 0, old_size = 0;
    int found = entry_info(s, path, &current, &old_size);
    int rc = found == CFS_INTERNAL ? CFS_INTERNAL : check_expected(found, current, expected);
    if (rc == CFS_OK && found == CFS_NOT_FOUND)
        rc = check_shape(s, path);
    int64_t bytes = s->bytes - (found == CFS_OK ? old_size : 0) + (int64_t)len;
    if (rc == CFS_OK && (uint64_t)bytes > CFS_TREE_MAX)
        rc = CFS_TOO_LARGE;
    int64_t version = s->version + 1;
    if (rc == CFS_OK)
        rc = write_row(s, path, version, now, data, len);
    if (rc == CFS_OK)
        rc = commit_version(s, version);
    if (rc != CFS_OK) {
        rollback(s);
        return rc;
    }
    s->bytes = bytes;
    *new_version = version;
    return CFS_OK;
}

int cfs_store_delete(cfs_store *s, const char *path, int64_t expected)
{
    if (begin(s) != CFS_OK)
        return CFS_INTERNAL;
    int64_t current = 0, size = 0;
    int rc = entry_info(s, path, &current, &size);
    if (rc == CFS_OK && expected != CFS_ANY_VERSION && expected != current)
        rc = CFS_CONFLICT;
    if (rc == CFS_OK)
        rc = delete_row(s, path);
    if (rc == CFS_OK)
        rc = commit_version(s, s->version + 1);
    if (rc != CFS_OK) {
        rollback(s);
        return rc;
    }
    s->bytes -= size;
    return CFS_OK;
}

int cfs_store_rename(cfs_store *s, const char *from, const char *to, int64_t expected, int64_t now,
                     int64_t *new_version)
{
    if (strcmp(from, to) == 0) {
        snprintf(s->error, sizeof(s->error), "the source and the target are the same");
        return CFS_INVALID;
    }
    if (begin(s) != CFS_OK)
        return CFS_INTERNAL;
    int64_t current = 0, size = 0, other = 0, other_size = 0;
    uint8_t *data = NULL;
    size_t len = 0;
    int64_t mtime;
    int rc = entry_info(s, from, &current, &size);
    if (rc == CFS_OK && expected != CFS_ANY_VERSION && expected != current)
        rc = CFS_CONFLICT;
    if (rc == CFS_OK) {
        int target = entry_info(s, to, &other, &other_size);
        rc = target == CFS_OK ? CFS_CONFLICT : target == CFS_NOT_FOUND ? CFS_OK : target;
    }
    /* The source is removed first, so moving a file into what was its own parent path is judged without it. */
    if (rc == CFS_OK)
        rc = cfs_store_get(s, from, &current, &mtime, &data, &len);
    if (rc == CFS_OK)
        rc = delete_row(s, from);
    if (rc == CFS_OK)
        rc = check_shape(s, to);
    int64_t version = s->version + 1;
    if (rc == CFS_OK)
        rc = write_row(s, to, version, now, data, len);
    free(data);
    if (rc == CFS_OK)
        rc = commit_version(s, version);
    if (rc != CFS_OK) {
        rollback(s);
        return rc;
    }
    *new_version = version;
    return CFS_OK;
}

int cfs_store_next_id(cfs_store *s, int64_t *id)
{
    if (begin(s) != CFS_OK)
        return CFS_INTERNAL;
    int64_t next = query_i64(s, "SELECT value FROM meta WHERE key = 'next_id'", -1);
    int rc = next < CFS_FIRST_ID ? CFS_INTERNAL : CFS_OK;
    if (rc != CFS_OK)
        snprintf(s->error, sizeof(s->error), "the id counter is missing or below %d", CFS_FIRST_ID);
    sqlite3_stmt *st = NULL;
    if (rc == CFS_OK &&
        sqlite3_prepare_v2(s->db, "UPDATE meta SET value = ? WHERE key = 'next_id'", -1, &st, NULL) != SQLITE_OK) {
        set_error(s, "next id");
        rc = CFS_INTERNAL;
    }
    if (rc == CFS_OK) {
        sqlite3_bind_int64(st, 1, next + 1);
        if (sqlite3_step(st) != SQLITE_DONE) {
            set_error(s, "next id");
            rc = CFS_INTERNAL;
        }
    }
    sqlite3_finalize(st);
    if (rc == CFS_OK)
        rc = commit_version(s, s->version + 1);
    if (rc != CFS_OK) {
        rollback(s);
        return rc;
    }
    *id = next;
    return CFS_OK;
}

/* ---- Locks: rows of the same database, so they are replicated, checksummed and copied with the tree ----------- */

/* Run one statement with up to two text and two integer parameters bound in order (texts first). Returns the
 * sqlite3_step result, or -1 when it cannot be prepared. */
static int run(cfs_store *s, const char *sql, const char *t1, const char *t2, int64_t i1, int64_t i2, int nint)
{
    sqlite3_stmt *st;
    if (sqlite3_prepare_v2(s->db, sql, -1, &st, NULL) != SQLITE_OK) {
        set_error(s, "lock");
        return -1;
    }
    int k = 1;
    if (t1)
        sqlite3_bind_text(st, k++, t1, -1, SQLITE_STATIC);
    if (t2)
        sqlite3_bind_text(st, k++, t2, -1, SQLITE_STATIC);
    if (nint > 0)
        sqlite3_bind_int64(st, k++, i1);
    if (nint > 1)
        sqlite3_bind_int64(st, k++, i2);
    int rc = sqlite3_step(st);
    if (rc != SQLITE_ROW && rc != SQLITE_DONE)
        set_error(s, "lock");
    sqlite3_finalize(st);
    return rc;
}

/* The current holder of `name` among unexpired locks: CFS_OK with the owner copied, CFS_NOT_FOUND, CFS_INTERNAL. */
static int lock_holder(cfs_store *s, const char *name, char owner[CFS_NAME_MAX + 1])
{
    sqlite3_stmt *st;
    if (sqlite3_prepare_v2(s->db, "SELECT owner FROM locks WHERE name = ?", -1, &st, NULL) != SQLITE_OK) {
        set_error(s, "lock");
        return CFS_INTERNAL;
    }
    sqlite3_bind_text(st, 1, name, -1, SQLITE_STATIC);
    int rc = sqlite3_step(st), result;
    if (rc == SQLITE_ROW) {
        snprintf(owner, CFS_NAME_MAX + 1, "%s", (const char *)sqlite3_column_text(st, 0));
        result = CFS_OK;
    } else if (rc == SQLITE_DONE) {
        result = CFS_NOT_FOUND;
    } else {
        set_error(s, "lock");
        result = CFS_INTERNAL;
    }
    sqlite3_finalize(st);
    return result;
}

int cfs_store_lock(cfs_store *s, const char *name, const char *owner, uint32_t node, uint32_t ttl, int64_t now,
                   char holder[CFS_NAME_MAX + 1])
{
    holder[0] = '\0';
    if (ttl == 0 || ttl > CFS_LOCK_TTL_MAX)
        return CFS_INVALID;
    if (begin(s) != CFS_OK)
        return CFS_INTERNAL;
    int rc = run(s, "DELETE FROM locks WHERE expires <= ?", NULL, NULL, now, 0, 1) == SQLITE_DONE ? CFS_OK
                                                                                                  : CFS_INTERNAL;
    char current[CFS_NAME_MAX + 1];
    int held = rc == CFS_OK ? lock_holder(s, name, current) : rc;
    if (held == CFS_INTERNAL)
        rc = CFS_INTERNAL;
    else if (held == CFS_OK && strcmp(current, owner) != 0) {
        snprintf(holder, CFS_NAME_MAX + 1, "%s", current);
        rc = CFS_LOCKED;
    } else if (held == CFS_NOT_FOUND && query_i64(s, "SELECT COUNT(*) FROM locks", 0) >= CFS_LOCKS_MAX)
        rc = CFS_TOO_LARGE;
    /* A new lock, or a renewal by its owner (from any node: the owner names the holder, the node only says where to
     * release it when that node leaves). */
    if (rc == CFS_OK &&
        run(s, "INSERT INTO locks (name, owner, node, expires) VALUES (?, ?, ?, ?) "
               "ON CONFLICT(name) DO UPDATE SET node = excluded.node, expires = excluded.expires",
            name, owner, node, now + (int64_t)ttl, 2) != SQLITE_DONE)
        rc = CFS_INTERNAL;
    if (rc == CFS_OK)
        rc = commit_version(s, s->version + 1);
    if (rc != CFS_OK)
        rollback(s);
    return rc;
}

int cfs_store_unlock(cfs_store *s, const char *name, const char *owner, int64_t now)
{
    if (begin(s) != CFS_OK)
        return CFS_INTERNAL;
    int rc = run(s, "DELETE FROM locks WHERE expires <= ?", NULL, NULL, now, 0, 1) == SQLITE_DONE ? CFS_OK
                                                                                                  : CFS_INTERNAL;
    char current[CFS_NAME_MAX + 1];
    if (rc == CFS_OK)
        rc = lock_holder(s, name, current);
    if (rc == CFS_OK && strcmp(current, owner) != 0)
        rc = CFS_FORBIDDEN;
    if (rc == CFS_OK && run(s, "DELETE FROM locks WHERE name = ?", name, NULL, 0, 0, 0) != SQLITE_DONE)
        rc = CFS_INTERNAL;
    if (rc == CFS_OK)
        rc = commit_version(s, s->version + 1);
    if (rc != CFS_OK)
        rollback(s);
    return rc;
}

int cfs_store_drop_locks(cfs_store *s, const uint32_t *members, size_t count)
{
    sqlite3_stmt *st;
    if (sqlite3_prepare_v2(s->db, "SELECT DISTINCT node FROM locks ORDER BY node", -1, &st, NULL) != SQLITE_OK) {
        set_error(s, "drop locks");
        return CFS_INTERNAL;
    }
    uint32_t gone[CFS_MEMBERS_MAX];
    size_t ngone = 0;
    int rc;
    while ((rc = sqlite3_step(st)) == SQLITE_ROW && ngone < CFS_MEMBERS_MAX) {
        uint32_t node = (uint32_t)sqlite3_column_int64(st, 0);
        bool member = false;
        for (size_t i = 0; i < count; i++)
            member = member || members[i] == node;
        if (!member)
            gone[ngone++] = node;
    }
    sqlite3_finalize(st);
    if (rc != SQLITE_DONE && rc != SQLITE_ROW) {
        set_error(s, "drop locks");
        return CFS_INTERNAL;
    }
    if (ngone == 0)
        return CFS_OK;
    if (begin(s) != CFS_OK)
        return CFS_INTERNAL;
    rc = CFS_OK;
    for (size_t i = 0; i < ngone && rc == CFS_OK; i++)
        if (run(s, "DELETE FROM locks WHERE node = ?", NULL, NULL, gone[i], 0, 1) != SQLITE_DONE)
            rc = CFS_INTERNAL;
    if (rc == CFS_OK)
        rc = commit_version(s, s->version + 1);
    if (rc != CFS_OK)
        rollback(s);
    return rc;
}

/* ---- State transfer ------------------------------------------------------------------------------------------- */

int cfs_store_dump(cfs_store *s, cfs_dump_entry_cb on_entry, cfs_dump_lock_cb on_lock, void *ctx, int64_t *version,
                   int64_t *next_id)
{
    sqlite3_stmt *st;
    if (sqlite3_prepare_v2(s->db, "SELECT path, version, mtime, data FROM tree ORDER BY path", -1, &st, NULL) !=
        SQLITE_OK) {
        set_error(s, "dump");
        return CFS_INTERNAL;
    }
    int rc, result = CFS_OK;
    while (result == CFS_OK && (rc = sqlite3_step(st)) == SQLITE_ROW) {
        int n = sqlite3_column_bytes(st, 3);
        if (on_entry(ctx, (const char *)sqlite3_column_text(st, 0), sqlite3_column_int64(st, 1),
                     sqlite3_column_int64(st, 2), sqlite3_column_blob(st, 3), (size_t)n) != 0)
            result = CFS_INTERNAL;
    }
    if (result == CFS_OK && rc != SQLITE_DONE) {
        set_error(s, "dump");
        result = CFS_INTERNAL;
    }
    sqlite3_finalize(st);
    if (result != CFS_OK)
        return result;
    if (sqlite3_prepare_v2(s->db, "SELECT name, owner, node, expires FROM locks ORDER BY name", -1, &st, NULL) !=
        SQLITE_OK) {
        set_error(s, "dump");
        return CFS_INTERNAL;
    }
    while (result == CFS_OK && (rc = sqlite3_step(st)) == SQLITE_ROW)
        if (on_lock(ctx, (const char *)sqlite3_column_text(st, 0), (const char *)sqlite3_column_text(st, 1),
                    (uint32_t)sqlite3_column_int64(st, 2), sqlite3_column_int64(st, 3)) != 0)
            result = CFS_INTERNAL;
    if (result == CFS_OK && rc != SQLITE_DONE) {
        set_error(s, "dump");
        result = CFS_INTERNAL;
    }
    sqlite3_finalize(st);
    *version = s->version;
    *next_id = query_i64(s, "SELECT value FROM meta WHERE key = 'next_id'", -1);
    if (result == CFS_OK && *next_id < CFS_FIRST_ID) {
        snprintf(s->error, sizeof(s->error), "the id counter is missing or below %d", CFS_FIRST_ID);
        result = CFS_INTERNAL;
    }
    return result;
}

int cfs_store_replace_begin(cfs_store *s)
{
    if (begin(s) != CFS_OK)
        return CFS_INTERNAL;
    if (exec(s, "DELETE FROM tree; DELETE FROM locks;") != CFS_OK) {
        rollback(s);
        return CFS_INTERNAL;
    }
    return CFS_OK;
}

int cfs_store_replace_entry(cfs_store *s, const char *path, int64_t version, int64_t mtime, const uint8_t *data,
                            size_t len)
{
    return len > CFS_FILE_MAX ? CFS_TOO_LARGE : write_row(s, path, version, mtime, data, len);
}

int cfs_store_replace_lock(cfs_store *s, const char *name, const char *owner, uint32_t node, int64_t expires)
{
    return run(s, "INSERT INTO locks (name, owner, node, expires) VALUES (?, ?, ?, ?)", name, owner, node, expires,
               2) == SQLITE_DONE
               ? CFS_OK
               : CFS_INTERNAL;
}

int cfs_store_replace_commit(cfs_store *s, int64_t version, int64_t next_id)
{
    sqlite3_stmt *st;
    int rc = CFS_OK;
    if (sqlite3_prepare_v2(s->db, "UPDATE meta SET value = ? WHERE key = 'next_id'", -1, &st, NULL) != SQLITE_OK) {
        set_error(s, "replace");
        rc = CFS_INTERNAL;
    } else {
        sqlite3_bind_int64(st, 1, next_id);
        if (sqlite3_step(st) != SQLITE_DONE) {
            set_error(s, "replace");
            rc = CFS_INTERNAL;
        }
        sqlite3_finalize(st);
    }
    int64_t bytes = query_i64(s, "SELECT COALESCE(SUM(length(data)), 0) FROM tree", -1);
    if (rc == CFS_OK && (bytes < 0 || (uint64_t)bytes > CFS_TREE_MAX)) {
        snprintf(s->error, sizeof(s->error), "the received tree is larger than the limit");
        rc = CFS_TOO_LARGE;
    }
    if (rc == CFS_OK)
        rc = commit_version(s, version);
    if (rc != CFS_OK) {
        rollback(s);
        return rc;
    }
    s->bytes = bytes;
    return CFS_OK;
}

void cfs_store_replace_abort(cfs_store *s)
{
    rollback(s);
}

/* ---- Reads ------------------------------------------------------------------------------------------------------- */

int cfs_store_get(cfs_store *s, const char *path, int64_t *version, int64_t *mtime, uint8_t **data, size_t *len)
{
    *data = NULL;
    *len = 0;
    sqlite3_stmt *st;
    if (sqlite3_prepare_v2(s->db, "SELECT version, mtime, data FROM tree WHERE path = ?", -1, &st, NULL) !=
        SQLITE_OK) {
        set_error(s, "get");
        return CFS_INTERNAL;
    }
    sqlite3_bind_text(st, 1, path, -1, SQLITE_STATIC);
    int rc = sqlite3_step(st);
    int result = CFS_OK;
    if (rc == SQLITE_ROW) {
        *version = sqlite3_column_int64(st, 0);
        *mtime = sqlite3_column_int64(st, 1);
        int n = sqlite3_column_bytes(st, 2);
        const void *blob = sqlite3_column_blob(st, 2);
        if (n > 0) {
            *data = malloc((size_t)n);
            if (!*data) {
                snprintf(s->error, sizeof(s->error), "out of memory");
                result = CFS_INTERNAL;
            } else {
                memcpy(*data, blob, (size_t)n);
                *len = (size_t)n;
            }
        }
    } else if (rc == SQLITE_DONE) {
        result = CFS_NOT_FOUND;
    } else {
        set_error(s, "get");
        result = CFS_INTERNAL;
    }
    sqlite3_finalize(st);
    return result;
}

int cfs_store_list(cfs_store *s, const char *dir, cfs_list_cb cb, void *ctx)
{
    int64_t v, size;
    if (strcmp(dir, "/") != 0) {
        int rc = entry_info(s, dir, &v, &size);
        if (rc == CFS_OK)
            return CFS_INVALID;
        if (rc != CFS_NOT_FOUND)
            return rc;
    }
    char lo[CFS_PATH_MAX + 2], hi[CFS_PATH_MAX + 2];
    subtree_range(dir, lo, hi, sizeof(lo));
    size_t prefix = strlen(lo);
    sqlite3_stmt *st;
    if (sqlite3_prepare_v2(s->db,
                           "SELECT path, version, length(data) FROM tree WHERE path >= ? AND path < ? ORDER BY path",
                           -1, &st, NULL) != SQLITE_OK) {
        set_error(s, "list");
        return CFS_INTERNAL;
    }
    sqlite3_bind_text(st, 1, lo, -1, SQLITE_TRANSIENT);
    sqlite3_bind_text(st, 2, hi, -1, SQLITE_TRANSIENT);
    char last[CFS_PATH_MAX + 1] = "";
    size_t last_len = 0;
    bool any = false;
    int rc, result = CFS_OK;
    while ((rc = sqlite3_step(st)) == SQLITE_ROW) {
        const char *path = (const char *)sqlite3_column_text(st, 0);
        const char *name = path + prefix;
        const char *slash = strchr(name, '/');
        size_t name_len = slash ? (size_t)(slash - name) : strlen(name);
        /* Everything under one subdirectory is contiguous in key order (it shares the prefix "name/"). */
        if (any && name_len == last_len && memcmp(name, last, name_len) == 0)
            continue;
        any = true;
        memcpy(last, name, name_len);
        last[name_len] = '\0';
        last_len = name_len;
        bool is_dir = slash != NULL;
        if (cb(ctx, name, name_len, is_dir, is_dir ? 0 : sqlite3_column_int64(st, 1),
               is_dir ? 0 : sqlite3_column_int64(st, 2)) != 0) {
            result = CFS_INTERNAL;
            break;
        }
    }
    if (result == CFS_OK && rc != SQLITE_DONE) {
        set_error(s, "list");
        result = CFS_INTERNAL;
    }
    sqlite3_finalize(st);
    if (result == CFS_OK && !any && strcmp(dir, "/") != 0)
        return CFS_NOT_FOUND;
    return result;
}

int cfs_store_status(cfs_store *s, int64_t *version, uint8_t checksum[CFS_CHECKSUM_LEN], int64_t *entries,
                     int64_t *bytes)
{
    /* SHA-256 over every entry in path order: path, NUL, version, length, data. Equal on two nodes iff their trees
     * are equal, which is what the state synchronisation compares (design, section 4.4). */
    EVP_MD_CTX *md = EVP_MD_CTX_new();
    if (!md || EVP_DigestInit_ex(md, EVP_sha256(), NULL) != 1) {
        EVP_MD_CTX_free(md);
        snprintf(s->error, sizeof(s->error), "SHA-256 unavailable");
        return CFS_INTERNAL;
    }
    sqlite3_stmt *st;
    if (sqlite3_prepare_v2(s->db, "SELECT path, version, data FROM tree ORDER BY path", -1, &st, NULL) != SQLITE_OK) {
        EVP_MD_CTX_free(md);
        set_error(s, "status");
        return CFS_INTERNAL;
    }
    int64_t count = 0;
    int rc;
    while ((rc = sqlite3_step(st)) == SQLITE_ROW) {
        const unsigned char *path = sqlite3_column_text(st, 0);
        uint8_t num[16];
        uint64_t v = (uint64_t)sqlite3_column_int64(st, 1);
        int n = sqlite3_column_bytes(st, 2);
        uint64_t len = (uint64_t)n;
        for (int i = 0; i < 8; i++) {
            num[i] = (uint8_t)(v >> (8 * i));
            num[8 + i] = (uint8_t)(len >> (8 * i));
        }
        EVP_DigestUpdate(md, path, strlen((const char *)path) + 1);
        EVP_DigestUpdate(md, num, sizeof(num));
        if (n > 0)
            EVP_DigestUpdate(md, sqlite3_column_blob(st, 2), (size_t)n);
        count++;
    }
    sqlite3_finalize(st);
    /* The locks after the tree, so two nodes agree only if they hold the same locks too. */
    if (rc == SQLITE_DONE &&
        sqlite3_prepare_v2(s->db, "SELECT name, owner, node, expires FROM locks ORDER BY name", -1, &st, NULL) ==
            SQLITE_OK) {
        EVP_DigestUpdate(md, "\0locks", 7);
        while ((rc = sqlite3_step(st)) == SQLITE_ROW) {
            const unsigned char *name = sqlite3_column_text(st, 0), *owner = sqlite3_column_text(st, 1);
            uint8_t num[12];
            uint32_t node = (uint32_t)sqlite3_column_int64(st, 2);
            uint64_t expires = (uint64_t)sqlite3_column_int64(st, 3);
            for (int i = 0; i < 4; i++)
                num[i] = (uint8_t)(node >> (8 * i));
            for (int i = 0; i < 8; i++)
                num[4 + i] = (uint8_t)(expires >> (8 * i));
            EVP_DigestUpdate(md, name, strlen((const char *)name) + 1);
            EVP_DigestUpdate(md, owner, strlen((const char *)owner) + 1);
            EVP_DigestUpdate(md, num, sizeof(num));
        }
        sqlite3_finalize(st);
    } else if (rc == SQLITE_DONE) {
        rc = SQLITE_ERROR;
    }
    unsigned int out_len = 0;
    int ok = rc == SQLITE_DONE && EVP_DigestFinal_ex(md, checksum, &out_len) == 1 && out_len == CFS_CHECKSUM_LEN;
    EVP_MD_CTX_free(md);
    if (!ok) {
        set_error(s, "status");
        return CFS_INTERNAL;
    }
    *version = s->version;
    *entries = count;
    *bytes = s->bytes;
    return CFS_OK;
}
