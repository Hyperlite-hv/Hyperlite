/* The Corosync transport: the CPG group "hyperlite-cfs" with agreed ordering, and the quorum service.
 *
 * CPG delivers every message to the members of the group in one order agreed by all of them, and the membership
 * changes at the same point of that order on every member (extended virtual synchrony): this is what the node needs.
 * The quorum service (votequorum underneath) says whether this node belongs to the majority. */

#ifndef CFS_COROSYNC_H
#define CFS_COROSYNC_H

#include <stdbool.h>
#include <stdint.h>

#include "node.h"
#include "server.h"

typedef struct cfs_corosync cfs_corosync;

/* Connect to the local Corosync. On failure returns NULL and writes the reason into `err`. */
cfs_corosync *cfs_corosync_open(char *err, size_t errlen);
/* This node's id in the Corosync configuration. */
uint32_t cfs_corosync_nodeid(const cfs_corosync *c);
/* The transport for cfs_node_init. */
cfs_transport cfs_corosync_transport(cfs_corosync *c);
/* Join the group: from then on, deliveries, membership and quorum changes reach `node` through the two sources. */
int cfs_corosync_start(cfs_corosync *c, cfs_node *node, cfs_source sources[2], char *err, size_t errlen);
void cfs_corosync_close(cfs_corosync *c);

#endif
