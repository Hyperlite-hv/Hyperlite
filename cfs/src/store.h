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
int cfs_store_put(cfs_store *s, const char *path, const uint8_t *data, size_t len, int64_t expected,
                  int64_t *new_version);
int cfs_store_delete(cfs_store *s, const char *path, int64_t expected);
int cfs_store_rename(cfs_store *s, const char *from, const char *to, int64_t expected, int64_t *new_version);

/* Direct children of a directory, in name order. `cb` returns 0 to go on. CFS_NOT_FOUND for an empty or missing
 * directory other than "/", CFS_INVALID when `dir` is a file. */
typedef int (*cfs_list_cb)(void *ctx, const char *name, size_t name_len, bool is_dir, int64_t version, int64_t size);
int cfs_store_list(cfs_store *s, const char *dir, cfs_list_cb cb, void *ctx);

/* A new guest id, unique and never reused, starting at CFS_FIRST_ID. */
int cfs_store_next_id(cfs_store *s, int64_t *id);

int cfs_store_status(cfs_store *s, int64_t *version, uint8_t checksum[CFS_CHECKSUM_LEN], int64_t *entries,
                     int64_t *bytes);

#endif
