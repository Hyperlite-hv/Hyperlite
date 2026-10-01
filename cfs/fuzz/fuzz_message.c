/* libFuzzer target: any byte string as a message from another node, through the node's decoder, the state agreement
 * and the apply path, against a real store. Peer messages come from the network: they must be as safe to parse as the
 * local socket. Built with meson -Dfuzz=true (clang), run with ./fuzz_message -max_total_time=60. */

#include <stdio.h>
#include <stdlib.h>

#include "node.h"

static cfs_node node;
static cfs_store *store;

static int drop(void *arg, const uint8_t *msg, size_t len)
{
    (void)arg;
    (void)msg;
    (void)len;
    return 0;
}

static void setup(void)
{
    char dir[] = "/tmp/cfs-fuzz-msg-XXXXXX";
    char db[64], err[256];
    if (!mkdtemp(dir))
        abort();
    snprintf(db, sizeof(db), "%s/config.db", dir);
    store = cfs_store_open(db, err, sizeof(err));
    if (!store) {
        fprintf(stderr, "%s\n", err);
        abort();
    }
    cfs_node_init(&node, store, CFS_MODE_CLUSTER, 1, (cfs_transport){.send = drop});
    cfs_node_quorum(&node, true);
    uint32_t members[] = {1, 2};
    cfs_node_membership(&node, members, 2);
}

int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size);

int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size)
{
    if (!store)
        setup();
    /* The first byte picks the sender, so both a member and a stranger are exercised; then let the node agree with
     * itself now and then, so changes are applied too, not only dropped. */
    if (size == 0)
        return 0;
    if (data[0] & 0x80) {
        node.synced = true;
        node.failed = false;
    }
    cfs_node_deliver(&node, (uint32_t)(data[0] & 0x03), data + 1, size - 1);
    return 0;
}
