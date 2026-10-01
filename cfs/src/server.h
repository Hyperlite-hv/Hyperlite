/* The local socket: one thread, poll(), non-blocking clients. Requests from one client are answered in order; the
 * store is only touched from this thread, so every change is serialised without locks. */

#ifndef CFS_SERVER_H
#define CFS_SERVER_H

#include <signal.h>

#include "handler.h"

/* Serve until *stop becomes non-zero (set by a signal handler). Returns 0, or -1 when the socket cannot be set up. */
int cfs_serve(cfs_ctx *ctx, const char *socket_path, unsigned mode, volatile sig_atomic_t *stop);

#endif
