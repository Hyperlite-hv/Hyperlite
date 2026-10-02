#include "corosync.h"

#include <corosync/cpg.h>
#include <corosync/quorum.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/uio.h>

#define GROUP "hyperlite-cfs"

struct cfs_corosync {
    cpg_handle_t cpg;
    quorum_handle_t quorum;
    struct cpg_name group;
    uint32_t nodeid;
    cfs_node *node;
    bool joined;
    /* The quorum service and CPG are two connections, in no set order between them, and a quorum view read when CPG
     * reports a membership can still be the previous ring's. The node counts as quorate only when the last quorum
     * notification was for the ring CPG reports now; votequorum sends one at every ring (votequorum_sync_activate). */
    bool quorate;         /* the quorum service's last answer */
    uint64_t quorum_ring; /* the ring it was for */
    uint64_t ring;        /* the ring CPG reported last */
    bool ring_known;
};

static void update_quorum(cfs_corosync *c)
{
    cfs_node_quorum(c->node, c->quorate && c->ring_known && c->quorum_ring == c->ring);
}

static cfs_corosync *from_cpg(cpg_handle_t h)
{
    void *ctx = NULL;
    return cpg_context_get(h, &ctx) == CS_OK ? ctx : NULL;
}

static cfs_corosync *from_quorum(quorum_handle_t h)
{
    const void *ctx = NULL;
    return quorum_context_get(h, &ctx) == CS_OK ? (cfs_corosync *)ctx : NULL;
}

static void on_deliver(cpg_handle_t h, const struct cpg_name *group, uint32_t nodeid, uint32_t pid, void *msg,
                       size_t len)
{
    (void)group;
    (void)pid;
    cfs_corosync *c = from_cpg(h);
    if (c && c->node)
        cfs_node_deliver(c->node, nodeid, msg, len);
}

static void on_confchg(cpg_handle_t h, const struct cpg_name *group, const struct cpg_address *members,
                       size_t nmembers, const struct cpg_address *left, size_t nleft, const struct cpg_address *joined,
                       size_t njoined)
{
    (void)group;
    (void)left;
    (void)nleft;
    (void)joined;
    (void)njoined;
    cfs_corosync *c = from_cpg(h);
    if (!c || !c->node)
        return;
    uint32_t ids[CFS_MEMBERS_MAX];
    size_t n = 0;
    for (size_t i = 0; i < nmembers && n < CFS_MEMBERS_MAX; i++) {
        bool dup = false;
        for (size_t k = 0; k < n; k++)
            dup = dup || ids[k] == members[i].nodeid;
        if (!dup)
            ids[n++] = members[i].nodeid; /* one daemon per node; a second process of a node counts once */
    }
    cfs_node_membership(c->node, ids, n);
}

/* A new Corosync ring (totem membership). Its sequence number only grows, and Corosync keeps it across restarts in
 * /var/lib/corosync: the node uses it as the term of its agreements (design, section 8.4). */
static void on_ring(cpg_handle_t h, struct cpg_ring_id ring, uint32_t nmembers, const uint32_t *members)
{
    (void)nmembers;
    (void)members;
    cfs_corosync *c = from_cpg(h);
    if (!c || !c->node)
        return;
    c->ring = ring.seq;
    c->ring_known = true;
    /* The quorum first: the state the ring makes the node send must already say whether this ring is quorate. */
    update_quorum(c);
    cfs_node_ring(c->node, ring.seq);
}

static void on_quorum(quorum_handle_t h, uint32_t quorate, uint64_t ring_seq, uint32_t nview, uint32_t *view)
{
    (void)nview;
    (void)view;
    cfs_corosync *c = from_quorum(h);
    if (!c || !c->node)
        return;
    c->quorate = quorate != 0;
    c->quorum_ring = ring_seq;
    update_quorum(c);
}

static int cpg_send(void *arg, const uint8_t *msg, size_t len)
{
    cfs_corosync *c = arg;
    struct iovec iov = {.iov_base = (void *)msg, .iov_len = len};
    cs_error_t rc = cpg_mcast_joined(c->cpg, CPG_TYPE_AGREED, &iov, 1);
    if (rc == CS_OK)
        return 0;
    if (rc == CS_ERR_TRY_AGAIN)
        return 1;
    fprintf(stderr, "hyperlite-cfs: Corosync refused a message (error %d)\n", (int)rc);
    return -1;
}

static void cpg_quit(void *arg)
{
    cfs_corosync *c = arg;
    if (c->joined && cpg_leave(c->cpg, &c->group) == CS_OK)
        c->joined = false;
}

static int cpg_ready(void *arg)
{
    cfs_corosync *c = arg;
    cs_error_t rc = cpg_dispatch(c->cpg, CS_DISPATCH_ALL);
    if (rc != CS_OK && rc != CS_ERR_TRY_AGAIN) {
        fprintf(stderr, "hyperlite-cfs: lost Corosync's process group (error %d)\n", (int)rc);
        return -1;
    }
    return 0;
}

static int quorum_ready(void *arg)
{
    cfs_corosync *c = arg;
    cs_error_t rc = quorum_dispatch(c->quorum, CS_DISPATCH_ALL);
    if (rc != CS_OK && rc != CS_ERR_TRY_AGAIN) {
        fprintf(stderr, "hyperlite-cfs: lost Corosync's quorum service (error %d)\n", (int)rc);
        return -1;
    }
    return 0;
}

cfs_corosync *cfs_corosync_open(char *err, size_t errlen)
{
    cfs_corosync *c = calloc(1, sizeof(*c));
    if (!c) {
        snprintf(err, errlen, "out of memory");
        return NULL;
    }
    cpg_model_v1_data_t model = {
        .model = CPG_MODEL_V1,
        .cpg_deliver_fn = on_deliver,
        .cpg_confchg_fn = on_confchg,
        .cpg_totem_confchg_fn = on_ring,
        .flags = CPG_MODEL_V1_DELIVER_INITIAL_TOTEM_CONF, /* the current ring at once, not only the next one */
    };
    cs_error_t rc = cpg_model_initialize(&c->cpg, CPG_MODEL_V1, (cpg_model_data_t *)&model, c);
    if (rc != CS_OK) {
        snprintf(err, errlen, "cannot reach Corosync's process groups (error %d): is corosync running?", (int)rc);
        free(c);
        return NULL;
    }
    quorum_callbacks_t qcb = {.quorum_notify_fn = on_quorum};
    uint32_t qtype = 0;
    rc = quorum_initialize(&c->quorum, &qcb, &qtype);
    if (rc == CS_OK && qtype != QUORUM_SET)
        snprintf(err, errlen, "Corosync has no quorum provider: set quorum { provider: corosync_votequorum }");
    else if (rc != CS_OK)
        snprintf(err, errlen, "cannot reach Corosync's quorum service (error %d)", (int)rc);
    if (rc == CS_OK && qtype == QUORUM_SET)
        rc = quorum_context_set(c->quorum, c);
    else if (rc == CS_OK)
        rc = CS_ERR_NOT_EXIST;
    unsigned int nodeid = 0;
    if (rc == CS_OK && (rc = cpg_local_get(c->cpg, &nodeid)) != CS_OK)
        snprintf(err, errlen, "cannot read this node's Corosync id (error %d)", (int)rc);
    if (rc != CS_OK) {
        quorum_finalize(c->quorum);
        cpg_finalize(c->cpg);
        free(c);
        return NULL;
    }
    c->nodeid = nodeid;
    snprintf(c->group.value, sizeof(c->group.value), "%s", GROUP);
    c->group.length = (uint32_t)strlen(GROUP);
    return c;
}

uint32_t cfs_corosync_nodeid(const cfs_corosync *c)
{
    return c->nodeid;
}

cfs_transport cfs_corosync_transport(cfs_corosync *c)
{
    return (cfs_transport){.send = cpg_send, .leave = cpg_quit, .arg = c};
}

int cfs_corosync_start(cfs_corosync *c, cfs_node *node, cfs_source sources[2], char *err, size_t errlen)
{
    c->node = node;
    int cfd = -1, qfd = -1;
    /* CS_TRACK_CURRENT: a first notification at once, with the ring it is for; until then the node is not quorate. */
    cs_error_t rc = quorum_trackstart(c->quorum, CS_TRACK_CURRENT | CS_TRACK_CHANGES);
    if (rc == CS_OK)
        rc = cpg_fd_get(c->cpg, &cfd);
    if (rc == CS_OK)
        rc = quorum_fd_get(c->quorum, &qfd);
    if (rc == CS_OK)
        rc = cpg_join(c->cpg, &c->group);
    if (rc != CS_OK) {
        snprintf(err, errlen, "cannot join the hyperlite-cfs process group (error %d)", (int)rc);
        return -1;
    }
    c->joined = true;
    sources[0] = (cfs_source){.fd = cfd, .ready = cpg_ready, .arg = c};
    sources[1] = (cfs_source){.fd = qfd, .ready = quorum_ready, .arg = c};
    return 0;
}

void cfs_corosync_close(cfs_corosync *c)
{
    if (!c)
        return;
    cpg_quit(c);
    quorum_finalize(c->quorum);
    cpg_finalize(c->cpg);
    free(c);
}
