/* The local socket: one thread, poll(), non-blocking clients. The store and the node are only touched from this
 * thread, so every change is serialised without locks.
 *
 * A client's requests are answered in the order it sent them: while a change of that client travels through the
 * cluster, its next requests wait unread, so it always reads its own writes. */

#ifndef CFS_SERVER_H
#define CFS_SERVER_H

#include <signal.h>

#include "node.h"

/* Another file descriptor to watch (Corosync's), and what to do when it is readable. `ready` returns -1 when the
 * daemon must stop (Corosync went away). */
typedef struct {
    int fd;
    int (*ready)(void *arg);
    void *arg;
} cfs_source;

/* Serve until *stop becomes non-zero (set by a signal handler) or a source fails. Returns 0, or -1 when the socket
 * cannot be set up or a source failed. */
int cfs_serve(cfs_node *node, const char *socket_path, unsigned mode, const cfs_source *sources, size_t nsources,
              volatile sig_atomic_t *stop);

#endif
