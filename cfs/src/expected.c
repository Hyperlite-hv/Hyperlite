#include "expected.h"

#include <corosync/cmap.h>
#include <corosync/votequorum.h>
#include <errno.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#define NODES_MAX 64

static void usage(FILE *f)
{
    fprintf(f, "usage: hyperlite-cfs expected-votes VOTES [--confirm CLUSTER_NAME]\n"
               "  Lower Corosync's expected votes so that this partition is quorate again, when the nodes it misses\n"
               "  are known to be down for good (the counterpart of `pvecm expected`). Without --confirm, the cluster's\n"
               "  name is asked for on the terminal.\n");
}

typedef struct {
    uint32_t id;
    char name[128];
} configured_node;

/* The nodes of corosync.conf's nodelist, by id, with their name (or first address) for the messages. */
static size_t read_nodelist(cmap_handle_t cmap, configured_node *nodes, size_t max)
{
    cmap_iter_handle_t it;
    size_t n = 0;
    if (cmap_iter_init(cmap, "nodelist.node.", &it) != CS_OK)
        return 0;
    char key[CMAP_KEYNAME_MAXLEN + 1];
    size_t len;
    cmap_value_types_t type;
    while (n < max && cmap_iter_next(cmap, it, key, &len, &type) == CS_OK) {
        unsigned index;
        char field[64];
        if (sscanf(key, "nodelist.node.%u.%63s", &index, field) != 2 || strcmp(field, "nodeid") != 0)
            continue;
        configured_node *node = &nodes[n];
        if (cmap_get_uint32(cmap, key, &node->id) != CS_OK)
            continue;
        char *value = NULL;
        char name_key[CMAP_KEYNAME_MAXLEN + 1];
        snprintf(name_key, sizeof(name_key), "nodelist.node.%u.name", index);
        if (cmap_get_string(cmap, name_key, &value) != CS_OK) {
            snprintf(name_key, sizeof(name_key), "nodelist.node.%u.ring0_addr", index);
            if (cmap_get_string(cmap, name_key, &value) != CS_OK)
                value = NULL;
        }
        snprintf(node->name, sizeof(node->name), "%s", value ? value : "?");
        free(value);
        n++;
    }
    cmap_iter_finalize(cmap, it);
    return n;
}

/* The confirmation: the cluster's name, from --confirm or typed on the terminal. */
static bool confirmed(const char *cluster, const char *given)
{
    if (given)
        return strcmp(given, cluster) == 0;
    if (!isatty(STDIN_FILENO)) {
        fprintf(stderr, "No terminal to ask on: pass --confirm %s once you have checked the above.\n", cluster);
        return false;
    }
    fprintf(stderr, "Type the cluster's name (%s) to go on, anything else to stop: ", cluster);
    char line[256];
    if (!fgets(line, sizeof(line), stdin))
        return false;
    line[strcspn(line, "\r\n")] = '\0';
    return strcmp(line, cluster) == 0;
}

int cfs_expected_main(int argc, char **argv)
{
    const char *confirm = NULL;
    long votes = -1;
    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "--confirm") == 0 && i + 1 < argc) {
            confirm = argv[++i];
        } else if (strcmp(argv[i], "--help") == 0) {
            usage(stdout);
            return 0;
        } else if (votes < 0) {
            char *end;
            errno = 0;
            votes = strtol(argv[i], &end, 10);
            if (errno || *end || votes < 1 || votes > NODES_MAX) {
                fprintf(stderr, "hyperlite-cfs: VOTES must be a number from 1 to %d\n", NODES_MAX);
                return 2;
            }
        } else {
            usage(stderr);
            return 2;
        }
    }
    if (votes < 0) {
        usage(stderr);
        return 2;
    }

    cmap_handle_t cmap;
    votequorum_handle_t vq;
    if (cmap_initialize(&cmap) != CS_OK) {
        fprintf(stderr, "hyperlite-cfs: cannot reach Corosync: is corosync running on this node?\n");
        return 1;
    }
    if (votequorum_initialize(&vq, NULL) != CS_OK) {
        fprintf(stderr, "hyperlite-cfs: cannot reach Corosync's votequorum service\n");
        cmap_finalize(cmap);
        return 1;
    }
    int rc = 1;
    char *cluster = NULL;
    struct votequorum_info info;
    if (cmap_get_string(cmap, "totem.cluster_name", &cluster) != CS_OK || votequorum_getinfo(vq, 0, &info) != CS_OK) {
        fprintf(stderr, "hyperlite-cfs: cannot read the cluster's name or its votes from Corosync\n");
        goto out;
    }

    if (info.flags & VOTEQUORUM_INFO_QUORATE) {
        fprintf(stderr,
                "This partition of %s is quorate (%u votes, %u needed): there is nothing to override.\n", cluster,
                info.total_votes, info.quorum);
        goto out;
    }
    /* Votequorum needs a strict majority of the expected votes: VOTES must bring that down to what is here. */
    if ((unsigned long)votes / 2 + 1 > info.total_votes) {
        fprintf(stderr,
                "This partition has %u vote(s): with %ld expected it would need %ld, and would stay read-only.\n",
                info.total_votes, votes, votes / 2 + 1);
        goto out;
    }

    configured_node nodes[NODES_MAX];
    size_t n = read_nodelist(cmap, nodes, NODES_MAX);
    fprintf(stderr, "Cluster %s: this partition holds %u of %u expected votes, %u are needed.\n", cluster,
            info.total_votes, info.node_expected_votes, info.quorum);
    size_t missing = 0;
    for (size_t i = 0; i < n; i++) {
        struct votequorum_info node;
        bool member = votequorum_getinfo(vq, nodes[i].id, &node) == CS_OK &&
                      node.node_state == VOTEQUORUM_NODESTATE_MEMBER;
        fprintf(stderr, "  node %u (%s): %s\n", nodes[i].id, nodes[i].name,
                member ? "here" : "MISSING: it must be down, not only cut off");
        missing += !member;
    }
    fprintf(stderr,
            "\nExpecting %ld vote(s) makes this partition writable on its own. Do it only if every missing node is\n"
            "really down (powered off, or its Corosync stopped). If a missing node is only cut off and someone does\n"
            "the same on its side, both halves write: when they meet again, the changes of one half are thrown\n"
            "away (hyperlite-cfs logs a warning). Corosync raises the expected votes again as the missing nodes come\n"
            "back.\n\n",
            votes);
    if (!missing)
        fprintf(stderr, "Note: no configured node is missing; the votes missing are a QDevice's.\n");
    if (!confirmed(cluster, confirm)) {
        fprintf(stderr, "Not confirmed: nothing was changed.\n");
        goto out;
    }
    cs_error_t err = votequorum_setexpected(vq, (unsigned)votes);
    if (err != CS_OK) {
        fprintf(stderr, "hyperlite-cfs: Corosync refused the change (error %d): nothing was changed\n", (int)err);
        goto out;
    }
    fprintf(stderr, "Expected votes set to %ld: this partition is quorate.\n", votes);
    rc = 0;
out:
    free(cluster);
    votequorum_finalize(vq);
    cmap_finalize(cmap);
    return rc;
}
