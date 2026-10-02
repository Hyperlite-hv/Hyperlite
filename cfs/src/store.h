/* The tree, stored in SQLite: one row per file, directories implied by the paths (like keys in a key-value store).
 *
 * Versions: every successful change increments one cluster-wide counter, and the entry it touched records that
 * value. Two nodes holding the same counter and the same checksum hold the same tree (design, section 3). A write
 * may name the version it expects (compare-and-set), so two administrators changing the same object at once get one
 * success and one CFS_CONFLICT instead of a silent overwrite. */

#ifndef CFS_STORE_H
#define CFS_STORE_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "cfs.h"

typedef struct cfs_store cfs_store;

/* Open (and create) the database. On failure returns NULL and writes the reason into `err`. */
cfs_store *cfs_store_open(const char *db_path, char *err, size_t errlen);
void cfs_store_close(cfs_store *s);

/* The last database error, for the daemon's log when a call returns CFS_INTERNAL. */
const char *cfs_store_error(const cfs_store *s);
void cfs_store_clear_error(cfs_store *s);

/* *data is malloc'ed (NULL for an empty file); the caller frees it. */
int cfs_store_get(cfs_store *s, const char *path, int64_t *version, int64_t *mtime, uint8_t **data, size_t *len);
/* Changes take `now` (seconds since the epoch) from the caller: in cluster mode every node applies the same change
 * with the time its sender stamped, so the trees stay identical whatever each node's clock says. */
int cfs_store_put(cfs_store *s, const char *path, const uint8_t *data, size_t len, int64_t expected, int64_t now,
                  int64_t *new_version);
int cfs_store_delete(cfs_store *s, const char *path, int64_t expected);
int cfs_store_rename(cfs_store *s, const char *from, const char *to, int64_t expected, int64_t now,
                     int64_t *new_version);

/* Named locks with an owner, the node it asked from, and a time to live (design, section 4.2). They are rows of the
 * database, so every node holds the same locks and the state synchronisation carries them. Each change bumps the
 * cluster version.
 *   lock:   CFS_OK (taken, or renewed by the same owner), CFS_LOCKED with the holder copied into `holder`,
 *           CFS_INVALID for a TTL of 0 or above CFS_LOCK_TTL_MAX, CFS_TOO_LARGE when CFS_LOCKS_MAX are held.
 *   unlock: CFS_OK, CFS_NOT_FOUND (not held, or expired), CFS_FORBIDDEN (held by another owner).
 *   drop:   release every lock taken from a node outside `members`, when the membership changes. */
int cfs_store_lock(cfs_store *s, const char *name, const char *owner, uint32_t node, uint32_t ttl, int64_t now,
                   char holder[CFS_NAME_MAX + 1]);
int cfs_store_unlock(cfs_store *s, const char *name, const char *owner, int64_t now);
int cfs_store_drop_locks(cfs_store *s, const uint32_t *members, size_t count);

/* Direct children of a directory, in name order. `cb` returns 0 to go on. CFS_NOT_FOUND for an empty or missing
 * directory other than "/", CFS_INVALID when `dir` is a file. */
typedef int (*cfs_list_cb)(void *ctx, const char *name, size_t name_len, bool is_dir, int64_t version, int64_t size);
int cfs_store_list(cfs_store *s, const char *dir, cfs_list_cb cb, void *ctx);

/* A new guest id, unique and never reused, starting at CFS_FIRST_ID. */
int cfs_store_next_id(cfs_store *s, int64_t *id);

/* The whole state, for a state transfer (design, section 4.4): every entry in path order, every lock in name order,
 * and the two counters. A callback returns 0 to go on. */
typedef int (*cfs_dump_entry_cb)(void *ctx, const char *path, int64_t version, int64_t mtime, const uint8_t *data,
                                 size_t len);
typedef int (*cfs_dump_lock_cb)(void *ctx, const char *name, const char *owner, uint32_t node, int64_t expires);
int cfs_store_dump(cfs_store *s, cfs_dump_entry_cb on_entry, cfs_dump_lock_cb on_lock, void *ctx, int64_t *version,
                   int64_t *next_id);

/* Replace the whole state with another node's, in one transaction: begin, the rows, then commit (or abort). */
int cfs_store_replace_begin(cfs_store *s);
int cfs_store_replace_entry(cfs_store *s, const char *path, int64_t version, int64_t mtime, const uint8_t *data,
                            size_t len);
int cfs_store_replace_lock(cfs_store *s, const char *name, const char *owner, uint32_t node, int64_t expires);
int cfs_store_replace_commit(cfs_store *s, int64_t version, int64_t next_id, int64_t term);
void cfs_store_replace_abort(cfs_store *s);

/* The term: the Corosync ring of the last agreement this state took part in. States are ordered by term, then by
 * version (design, section 8.4); it is not part of the checksum. Setting it is its own small transaction. */
int64_t cfs_store_term(const cfs_store *s);
int cfs_store_set_term(cfs_store *s, int64_t term);

/* The cluster version, and the SHA-256 of the whole state (tree and locks): two nodes hold the same state iff both
 * are equal. */
int cfs_store_status(cfs_store *s, int64_t *version, uint8_t checksum[CFS_CHECKSUM_LEN], int64_t *entries,
                     int64_t *bytes);

#endif
