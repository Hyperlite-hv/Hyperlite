/* hyperlite-cfs: Hyperlite's replicated cluster configuration (design: docs/design/hyperlite-cfs.md).
 *
 * Local mode (the default) is a cluster of one node: the same node code, with a loopback in place of Corosync, always
 * quorate. Cluster mode (--cluster) joins the local Corosync's process group: changes are applied by every member in
 * the order Corosync agreed, and refused without quorum. */

#include <signal.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "corosync.h"
#include "expected.h"
#include "server.h"

#define DEFAULT_DB "/var/lib/hyperlite-cfs/config.db"
#define DEFAULT_SOCKET "/run/hyperlite-cfs/socket"

static volatile sig_atomic_t stop_requested = 0;

static void on_signal(int sig)
{
    (void)sig;
    stop_requested = 1;
}

/* Local mode: a change is "delivered" to this node as soon as it is sent. */
static int loop_send(void *arg, const uint8_t *msg, size_t len)
{
    cfs_node *node = arg;
    cfs_node_deliver(node, node->self, msg, len);
    return 0;
}

static void usage(FILE *f)
{
    fprintf(f, "usage: hyperlite-cfs [--cluster] [--db PATH] [--socket PATH] [--socket-mode OCTAL]\n"
               "       hyperlite-cfs expected-votes VOTES [--confirm CLUSTER_NAME]\n"
               "  --cluster      replicate through the local Corosync (default: local mode, one node)\n"
               "  --db           database file (default " DEFAULT_DB ")\n"
               "  --socket       Unix socket (default " DEFAULT_SOCKET ")\n"
               "  --socket-mode  permissions of the socket (default 0600: root only)\n"
               "  expected-votes make a partition that lost the quorum writable again (see --help after it)\n");
}

int main(int argc, char **argv)
{
    const char *db = DEFAULT_DB;
    const char *sock = DEFAULT_SOCKET;
    unsigned mode = 0600;
    bool cluster = false;
    if (argc > 1 && strcmp(argv[1], "expected-votes") == 0)
        return cfs_expected_main(argc - 1, argv + 1);
    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "--cluster") == 0) {
            cluster = true;
        } else if (strcmp(argv[i], "--db") == 0 && i + 1 < argc) {
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
    cfs_store *store = cfs_store_open(db, err, sizeof(err));
    if (!store) {
        fprintf(stderr, "hyperlite-cfs: %s\n", err);
        return 1;
    }
    static cfs_node node;
    cfs_corosync *cs = NULL;
    cfs_source sources[2];
    size_t nsources = 0;
    int rc = 0;
    if (cluster) {
        cs = cfs_corosync_open(err, sizeof(err));
        if (!cs) {
            fprintf(stderr, "hyperlite-cfs: %s\n", err);
            cfs_store_close(store);
            return 1;
        }
        cfs_node_init(&node, store, CFS_MODE_CLUSTER, cfs_corosync_nodeid(cs), cfs_corosync_transport(cs));
        if (cfs_corosync_start(cs, &node, sources, err, sizeof(err)) != 0) {
            fprintf(stderr, "hyperlite-cfs: %s\n", err);
            rc = -1;
        }
        nsources = 2;
        fprintf(stderr, "hyperlite-cfs: cluster mode, node %u, database %s, socket %s\n", node.self, db, sock);
    } else {
        cfs_node_init(&node, store, CFS_MODE_LOCAL, 1, (cfs_transport){.send = loop_send, .arg = &node});
        cfs_node_quorum(&node, true);
        uint32_t self = 1;
        cfs_node_membership(&node, &self, 1);
        fprintf(stderr, "hyperlite-cfs: local mode, database %s, socket %s\n", db, sock);
    }
    if (rc == 0)
        rc = cfs_serve(&node, sock, mode, sources, nsources, &stop_requested);
    cfs_corosync_close(cs);
    cfs_node_free(&node);
    cfs_store_close(store);
    return rc == 0 ? 0 : 1;
}
