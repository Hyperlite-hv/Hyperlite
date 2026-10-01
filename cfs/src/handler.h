/* One request in, one answer out: the daemon's whole behaviour, separate from the sockets so tests can drive it. */

#ifndef CFS_HANDLER_H
#define CFS_HANDLER_H

#include <stddef.h>
#include <stdint.h>
#include <sys/types.h>
#include <time.h>

#include "locks.h"
#include "proto.h"
#include "store.h"

typedef struct {
    cfs_store *store;
    cfs_lock_table locks;
} cfs_ctx;

/* Decode `body`, apply it, and append the framed answer (u32 length + response body) to `out`. `peer_uid` is the
 * client's user, from the socket credentials: entries under priv/ are refused to anyone but root. */
void cfs_handle(cfs_ctx *ctx, const uint8_t *body, size_t len, uid_t peer_uid, time_t now, cfs_writer *out);

#endif
