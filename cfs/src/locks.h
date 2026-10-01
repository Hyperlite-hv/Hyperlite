/* Named locks with an owner and a time to live (design, section 4.2).
 *
 * In local mode they live in memory only: a daemon restart releases every lock, which is what a restart of the only
 * node means anyway. In cluster mode the same table will be changed by ordered messages, and a node leaving the
 * membership releases its locks. The TTL guards against a holder that hangs without dying. */

#ifndef CFS_LOCKS_H
#define CFS_LOCKS_H

#include <stddef.h>
#include <stdint.h>
#include <time.h>

#include "cfs.h"

#define CFS_LOCKS_MAX 1024

typedef struct {
    char name[CFS_NAME_MAX + 1];
    char owner[CFS_NAME_MAX + 1];
    time_t expires;
} cfs_lock;

typedef struct {
    cfs_lock items[CFS_LOCKS_MAX];
    size_t count;
} cfs_lock_table;

void cfs_locks_init(cfs_lock_table *t);

/* CFS_OK (taken, or renewed by the same owner), CFS_LOCKED with the holder copied into `holder`, CFS_INVALID for a
 * TTL of 0 or above CFS_LOCK_TTL_MAX, CFS_TOO_LARGE when the table is full. */
int cfs_lock_acquire(cfs_lock_table *t, const char *name, const char *owner, uint32_t ttl, time_t now,
                     char holder[CFS_NAME_MAX + 1]);

/* CFS_OK, CFS_NOT_FOUND (not held, or expired), CFS_FORBIDDEN (held by another owner). */
int cfs_lock_release(cfs_lock_table *t, const char *name, const char *owner, time_t now);

#endif
