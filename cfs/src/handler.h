/* What a request does, separate from the sockets and from Corosync so tests can drive it.
 *
 * A request is first checked where it arrives (cfs_check: path syntax, priv/ for a client that is not root, lock
 * names). A read is then answered by this node alone (cfs_read). A change travels to every node and each one applies
 * it in delivery order (cfs_apply): that function depends only on the request, the sender's node id and time stamp,
 * and the store, so every node that applies the same sequence holds the same state. */

#ifndef CFS_HANDLER_H
#define CFS_HANDLER_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <sys/types.h>

#include "proto.h"
#include "store.h"

enum cfs_mode {
    CFS_MODE_LOCAL = 0,
    CFS_MODE_CLUSTER = 1,
};

typedef struct {
    cfs_store *store;
    uint8_t mode;  /* enum cfs_mode, reported by STATUS */
    bool quorate;  /* reported by STATUS; always true in local mode */
} cfs_ctx;

/* Whether an operation changes the state (and so travels to every node). */
bool cfs_op_changes(uint8_t op);

/* Checks made where the request arrives. CFS_OK, or a refusal with its reason. */
int cfs_check(const cfs_request *req, uid_t uid, char *reason, size_t cap);

/* A read (GET, LIST, STATUS), answered from this node's store. */
int cfs_read(cfs_ctx *ctx, const cfs_request *req, cfs_writer *payload, char *reason, size_t cap);

/* A change, applied on every node in delivery order. `node` sent it, `now` is its time stamp (seconds). */
int cfs_apply(cfs_store *store, const cfs_request *req, uint32_t node, int64_t now, cfs_writer *payload,
              char *reason, size_t cap);

/* Append a framed answer (u32 length + response body) to `out`: the payload when `status` is CFS_OK, else `reason`. */
void cfs_answer(cfs_writer *out, uint32_t id, int status, const cfs_writer *payload, const char *reason);

/* Check, then read or apply as node 1 at `now`, and append the framed answer: one node on its own, synchronously.
 * Used by the tests and the fuzzer; the daemon goes through node.h, which sends changes to every node first. */
void cfs_handle(cfs_ctx *ctx, const uint8_t *body, size_t len, uid_t peer_uid, int64_t now, cfs_writer *out);

#endif
