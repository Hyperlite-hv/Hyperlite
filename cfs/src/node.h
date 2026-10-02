/* One node of the replicated configuration (design: docs/design/hyperlite-cfs.md, sections 4 and 5).
 *
 * The node sits between the clients and a transport that delivers messages to every member of the group in one
 * agreed order (Corosync CPG with CPG_TYPE_AGREED, or a loopback for local mode). It does not know which transport it
 * runs on, so the tests drive several nodes through a simulated one.
 *
 *   - A read is answered at once from this node's store.
 *   - A change is checked here, refused at once without quorum or while the members do not agree on one state, and
 *     otherwise multicast. Every member applies it when it is delivered, in delivery order, with the sender's node id
 *     and time stamp; the sender answers its client only when its own copy has been applied (read-your-writes, and
 *     the same result on every node).
 *   - On a membership change every member stops applying changes and multicasts its state (cluster version, SHA-256
 *     of tree and locks, and its view of the quorum). Changes are applied again once every member has sent a state,
 *     all of them quorate and all identical. A change delivered before that is dropped by every member alike.
 *     When they differ, every member picks the same source (the highest version, then the lowest node id); the
 *     source multicasts its whole state, the members that differ replace theirs with it in one transaction and check
 *     its checksum, then every member sends its state again. One transfer per membership: members that still differ
 *     after it stay read-only and say so.
 *   - A change that fails here with an internal error (disk full, database error) while the other members applied
 *     it would make this node diverge silently: the node leaves the group instead and refuses every change. */

#ifndef CFS_NODE_H
#define CFS_NODE_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <sys/types.h>

#include "handler.h"

typedef struct {
    /* Multicast `msg` to the group, in agreed order: 0 sent, 1 try again later (flow control), -1 failed. */
    int (*send)(void *arg, const uint8_t *msg, size_t len);
    /* Leave the group for good. */
    void (*leave)(void *arg);
    void *arg;
} cfs_transport;

/* Hand a framed answer to the client identified by `token`. */
typedef void (*cfs_reply_fn)(void *arg, uint64_t token, const uint8_t *frame, size_t len);

typedef struct {
    uint64_t seq;   /* the sender's message number */
    uint64_t token; /* the client waiting for it */
    uint32_t id;    /* the client's request id */
} cfs_pending;

typedef struct {
    uint8_t *p;
    size_t len;
} cfs_outgoing;

typedef struct {
    bool seen;
    bool quorate;
    int64_t version;
    uint8_t sum[CFS_CHECKSUM_LEN];
} cfs_member_state;

typedef struct {
    cfs_ctx ctx;
    uint32_t self;
    cfs_transport tr;
    cfs_reply_fn reply;
    void *reply_arg;

    bool quorate;  /* the quorum service's view of this node */
    bool synced;   /* every member sent the same state: changes are applied */
    bool diverged; /* every member sent a state, and they differ */
    bool failed;   /* a change failed here: the node left the group */

    uint32_t members[CFS_MEMBERS_MAX];
    cfs_member_state states[CFS_MEMBERS_MAX];
    size_t nmembers;

    /* State transfer, at most one per membership. */
    bool transferring;     /* waiting for the source's state */
    bool transferred;      /* a transfer already ran in this membership */
    bool xfer_needed;      /* this node's state differs from the source's */
    uint32_t xfer_source;
    int64_t xfer_version, xfer_next_id;
    uint8_t xfer_sum[CFS_CHECKSUM_LEN];
    uint8_t *xfer_buf; /* the records received so far */
    size_t xfer_len, xfer_cap;
    uint32_t xfer_records;

    uint64_t next_seq;
    cfs_pending *pending;
    size_t npending, cap_pending;
    cfs_outgoing *queue; /* messages the transport asked to retry, in order */
    size_t nqueue, cap_queue;
} cfs_node;

void cfs_node_init(cfs_node *n, cfs_store *store, uint8_t mode, uint32_t self, cfs_transport tr);
void cfs_node_free(cfs_node *n);

/* The client side. `now` is this node's clock (seconds); it travels with a change. */
void cfs_node_request(cfs_node *n, const uint8_t *body, size_t len, uid_t uid, uint64_t token, int64_t now);
/* The client behind `token` went away: its answers are no longer wanted. */
void cfs_node_forget(cfs_node *n, uint64_t token);

/* The transport side, in delivery order. `members` need not be sorted. */
void cfs_node_deliver(cfs_node *n, uint32_t sender, const uint8_t *msg, size_t len);
void cfs_node_membership(cfs_node *n, const uint32_t *members, size_t count);
void cfs_node_quorum(cfs_node *n, bool quorate);

/* Send again what the transport refused for flow control; true while something is still waiting. */
bool cfs_node_flush(cfs_node *n);

#endif
