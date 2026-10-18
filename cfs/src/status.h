/* The cluster's status (phase E, docs/design/phase-e-statd.md): every node's latest status, as that node sent it.
 *
 * A status is an opaque blob (the Python service's summary of the node's VMs, compressed). It is not part of the
 * replicated tree: not ordered with the changes, not written to the database, never refused for want of quorum. Kept
 * in memory only, with the time it arrived, so a reader can tell how old it is. */

#ifndef CFS_STATUS_H
#define CFS_STATUS_H

#include <stddef.h>
#include <stdint.h>

#include "cfs.h"
#include "proto.h"

typedef struct {
    uint32_t node;
    uint8_t *data;
    uint32_t len;
    int64_t at_ms; /* monotonic milliseconds when it arrived */
} cfs_stat_entry;

typedef struct {
    cfs_stat_entry e[CFS_MEMBERS_MAX];
    size_t n;
} cfs_stat_table;

void cfs_stat_init(cfs_stat_table *t);
void cfs_stat_free(cfs_stat_table *t);
/* The monotonic clock, in milliseconds. */
int64_t cfs_stat_now_ms(void);
/* Keep `node`'s status, replacing its previous one. With the table full, the oldest status of another node goes.
 * CFS_OK, CFS_TOO_LARGE (over CFS_STAT_MAX) or CFS_INTERNAL (out of memory). */
int cfs_stat_set(cfs_stat_table *t, uint32_t node, const uint8_t *data, size_t len, int64_t now_ms);
/* The STAT_GET payload. Node 0: the list, u32 count then count x (u32 node, i64 age in ms, u32 size); every status
 * at once could exceed one answer (CFS_FRAME_MAX). Another node: i64 age in ms, blob status, or CFS_NOT_FOUND. */
int cfs_stat_write(const cfs_stat_table *t, uint32_t node, int64_t now_ms, cfs_writer *w);

#endif
