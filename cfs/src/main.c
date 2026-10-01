/* hyperlite-cfs: Hyperlite's replicated cluster configuration (design: docs/design/hyperlite-cfs.md).
 *
 * This build is phase A, local mode: one node, no Corosync, always writable. The socket protocol, the versions, the
 * compare-and-set, the locks and the id allocation are the ones cluster mode will keep. */

#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "server.h"

#define DEFAULT_DB "/var/lib/hyperlite-cfs/config.db"
#define DEFAULT_SOCKET "/run/hyperlite-cfs/socket"

static volatile sig_atomic_t stop_requested = 0;

static void on_signal(int sig)
{
    (void)sig;
    stop_requested = 1;
}

static void usage(FILE *f)
{
    fprintf(f, "usage: hyperlite-cfs [--db PATH] [--socket PATH] [--socket-mode OCTAL]\n"
               "  --db           database file (default " DEFAULT_DB ")\n"
               "  --socket       Unix socket (default " DEFAULT_SOCKET ")\n"
               "  --socket-mode  permissions of the socket (default 0600: root only)\n");
}

int main(int argc, char **argv)
{
    const char *db = DEFAULT_DB;
    const char *sock = DEFAULT_SOCKET;
    unsigned mode = 0600;
    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "--db") == 0 && i + 1 < argc) {
            db = argv[++i];
        } else if (strcmp(argv[i], "--socket") == 0 && i + 1 < argc) {
            sock = argv[++i];
        } else if (strcmp(argv[i], "--socket-mode") == 0 && i + 1 < argc) {
            char *end;
            unsigned long m = strtoul(argv[++i], &end, 8);
            if (*end || m > 0777) {
                usage(stderr);
                return 2;
            }
            mode = (unsigned)m;
        } else if (strcmp(argv[i], "--help") == 0) {
            usage(stdout);
            return 0;
        } else {
            usage(stderr);
            return 2;
        }
    }

    struct sigaction sa = {.sa_handler = on_signal};
    sigemptyset(&sa.sa_mask);
    sigaction(SIGTERM, &sa, NULL);
    sigaction(SIGINT, &sa, NULL);
    signal(SIGPIPE, SIG_IGN);

    char err[512];
    cfs_ctx ctx;
    ctx.store = cfs_store_open(db, err, sizeof(err));
    if (!ctx.store) {
        fprintf(stderr, "hyperlite-cfs: %s\n", err);
        return 1;
    }
    cfs_locks_init(&ctx.locks);
    fprintf(stderr, "hyperlite-cfs: local mode, database %s, socket %s\n", db, sock);
    int rc = cfs_serve(&ctx, sock, mode, &stop_requested);
    cfs_store_close(ctx.store);
    return rc == 0 ? 0 : 1;
}
