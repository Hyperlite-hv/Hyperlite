#include "node.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* Messages between nodes, little-endian like the socket protocol:
 *   u32 magic, u8 format version, u8 kind, then
 *   CHANGE: i64 sender's message number, i64 sender's time (seconds), then a request body as a client sent it;
 *   STATE:  u8 quorate, i64 cluster version, blob SHA-256 of tree and locks.
 * The sender's node id comes from the transport (Corosync names it), never from the message. */
#define MSG_MAGIC 0x53464348u /* "HCFS" */
#define MSG_FORMAT 1
#define MSG_CHANGE 1
#define MSG_STATE 2
#define MSG_HEADER 6
#define CHANGE_HEADER (MSG_HEADER + 16)

static void put_le(uint8_t *b, uint64_t v, int bytes)
{
    for (int i = 0; i < bytes; i++)
        b[i] = (uint8_t)(v >> (8 * i));
}

void cfs_node_init(cfs_node *n, cfs_store *store, uint8_t mode, uint32_t self, cfs_transport tr)
{
    memset(n, 0, sizeof(*n));
    n->ctx.store = store;
    n->ctx.mode = mode;
    n->self = self;
    n->tr = tr;
    n->next_seq = 1;
}

void cfs_node_free(cfs_node *n)
{
    for (size_t i = 0; i < n->nqueue; i++)
        free(n->queue[i].p);
    free(n->queue);
    free(n->pending);
    n->queue = NULL;
    n->pending = NULL;
    n->nqueue = n->npending = 0;
}

/* ---- Answers to this node's clients -------------------------------------------------------------------------------- */

static void reply(cfs_node *n, uint64_t token, uint32_t id, int status, const cfs_writer *payload, const char *reason)
{
    cfs_writer out;
    cfs_writer_init(&out);
    cfs_writer empty;
    cfs_writer_init(&empty);
    cfs_answer(&out, id, status, payload ? payload : &empty, reason);
    if (!out.err && n->reply)
        n->reply(n->reply_arg, token, out.p, out.len);
    cfs_writer_free(&out);
}

/* Answer every change still in flight: none of them will be applied any more. */
static void fail_pending(cfs_node *n, int status, const char *reason)
{
    for (size_t i = 0; i < n->npending; i++)
        reply(n, n->pending[i].token, n->pending[i].id, status, NULL, reason);
    n->npending = 0;
}

static bool take_pending(cfs_node *n, uint64_t seq, cfs_pending *out)
{
    for (size_t i = 0; i < n->npending; i++) {
        if (n->pending[i].seq == seq) {
            *out = n->pending[i];
            memmove(&n->pending[i], &n->pending[i + 1], (n->npending - i - 1) * sizeof(*n->pending));
            n->npending--;
            return true;
        }
    }
    return false;
}

static bool grow_pending(cfs_node *n)
{
    if (n->npending < n->cap_pending)
        return true;
    size_t cap = n->cap_pending ? n->cap_pending * 2 : 16;
    cfs_pending *p = realloc(n->pending, cap * sizeof(*p));
    if (!p)
        return false;
    n->pending = p;
    n->cap_pending = cap;
    return true;
}

void cfs_node_forget(cfs_node *n, uint64_t token)
{
    /* The change still goes to every node; only its answer is dropped, so the pending entry gets a token no client
     * holds. */
    for (size_t i = 0; i < n->npending; i++)
        if (n->pending[i].token == token)
            n->pending[i].token = UINT64_MAX;
}

/* ---- Sending ------------------------------------------------------------------------------------------------------ */

/* Send, or queue when the transport asks to retry. Takes ownership of `msg`. false when the transport failed. */
static bool send_or_queue(cfs_node *n, uint8_t *msg, size_t len)
{
    int rc = n->nqueue ? 1 : n->tr.send(n->tr.arg, msg, len); /* keep the order behind what is queued */
    if (rc == 0) {
        free(msg);
        return true;
    }
    if (rc < 0) {
        free(msg);
        return false;
    }
    if (n->nqueue == n->cap_queue) {
        size_t cap = n->cap_queue ? n->cap_queue * 2 : 16;
        cfs_outgoing *q = realloc(n->queue, cap * sizeof(*q));
        if (!q) {
            free(msg);
            return false;
        }
        n->queue = q;
        n->cap_queue = cap;
    }
    n->queue[n->nqueue++] = (cfs_outgoing){msg, len};
    return true;
}

bool cfs_node_flush(cfs_node *n)
{
    size_t sent = 0;
    while (sent < n->nqueue) {
        int rc = n->tr.send(n->tr.arg, n->queue[sent].p, n->queue[sent].len);
        if (rc == 1)
            break;
        if (rc < 0)
            fprintf(stderr, "hyperlite-cfs: a queued message could not be sent and is dropped\n");
        free(n->queue[sent].p);
        sent++;
    }
    memmove(n->queue, n->queue + sent, (n->nqueue - sent) * sizeof(*n->queue));
    n->nqueue -= sent;
    return n->nqueue > 0;
}

static void send_state(cfs_node *n)
{
    int64_t version = 0, entries, bytes;
    uint8_t sum[CFS_CHECKSUM_LEN] = {0};
    if (cfs_store_status(n->ctx.store, &version, sum, &entries, &bytes) != CFS_OK) {
        fprintf(stderr, "hyperlite-cfs: cannot compute the state: %s\n", cfs_store_error(n->ctx.store));
        return; /* without our state the members never agree: changes stay refused */
    }
    cfs_writer w;
    cfs_writer_init(&w);
    cfs_write_u32(&w, MSG_MAGIC);
    cfs_write_u8(&w, MSG_FORMAT);
    cfs_write_u8(&w, MSG_STATE);
    cfs_write_u8(&w, n->quorate ? 1 : 0);
    cfs_write_i64(&w, version);
    cfs_write_blob(&w, sum, sizeof(sum));
    if (w.err) {
        cfs_writer_free(&w);
        fprintf(stderr, "hyperlite-cfs: cannot build this node's state message\n");
        return;
    }
    if (!send_or_queue(n, w.p, w.len))
        fprintf(stderr, "hyperlite-cfs: cannot send this node's state\n");
    w.p = NULL; /* send_or_queue owns it now */
    cfs_writer_free(&w);
}

/* ---- Client requests ---------------------------------------------------------------------------------------------- */

void cfs_node_request(cfs_node *n, const uint8_t *body, size_t len, uid_t uid, uint64_t token, int64_t now)
{
    cfs_request req;
    char reason[512] = "";
    if (cfs_decode_request(body, len, &req) != CFS_OK) {
        reply(n, token, req.id, CFS_INVALID, NULL, "malformed request");
        return;
    }
    int rc = cfs_check(&req, uid, reason, sizeof(reason));
    if (rc != CFS_OK) {
        reply(n, token, req.id, rc, NULL, reason);
        return;
    }
    if (!cfs_op_changes(req.op)) {
        cfs_writer payload;
        cfs_writer_init(&payload);
        n->ctx.quorate = n->quorate;
        rc = cfs_read(&n->ctx, &req, &payload, reason, sizeof(reason));
        if (rc == CFS_OK && payload.err) {
            rc = CFS_INTERNAL;
            snprintf(reason, sizeof(reason), "the answer could not be built");
        }
        reply(n, token, req.id, rc, &payload, reason);
        cfs_writer_free(&payload);
        return;
    }

    if (n->failed) {
        reply(n, token, req.id, CFS_READ_ONLY, NULL,
              "this node failed to apply a change and left the cluster; see the hyperlite-cfs log, then restart it");
        return;
    }
    if (!n->quorate) {
        reply(n, token, req.id, CFS_READ_ONLY, NULL,
              "this node is not part of a quorate cluster: changes are refused until the quorum is back");
        return;
    }
    if (!n->synced) {
        reply(n, token, req.id, CFS_SYNCHRONISING, NULL,
              n->diverged ? "the cluster members hold different states and copying a state between them is not "
                            "implemented yet: changes are refused"
                          : "the cluster is agreeing on its state after a membership change: retry");
        return;
    }

    size_t total = CHANGE_HEADER + len;
    uint8_t *msg = malloc(total);
    if (!msg || !grow_pending(n)) {
        free(msg);
        reply(n, token, req.id, CFS_INTERNAL, NULL, "out of memory");
        return;
    }
    uint64_t seq = n->next_seq++;
    put_le(msg, MSG_MAGIC, 4);
    msg[4] = MSG_FORMAT;
    msg[5] = MSG_CHANGE;
    put_le(msg + MSG_HEADER, seq, 8);
    put_le(msg + MSG_HEADER + 8, (uint64_t)now, 8);
    memcpy(msg + CHANGE_HEADER, body, len);
    /* Registered before sending: a loopback transport delivers inside send(). */
    n->pending[n->npending++] = (cfs_pending){seq, token, req.id};
    if (!send_or_queue(n, msg, total)) {
        cfs_pending gone;
        take_pending(n, seq, &gone);
        reply(n, token, req.id, CFS_INTERNAL, NULL, "the change could not be sent to the cluster");
    }
}

/* ---- Delivery ----------------------------------------------------------------------------------------------------- */

static void fail_node(cfs_node *n, const char *why)
{
    fprintf(stderr, "hyperlite-cfs: %s; this node leaves the cluster so as not to diverge from it\n", why);
    n->failed = true;
    n->synced = false;
    fail_pending(n, CFS_INTERNAL, "this node failed to apply the change and left the cluster");
    if (n->tr.leave)
        n->tr.leave(n->tr.arg);
}

static void deliver_change(cfs_node *n, uint32_t sender, cfs_reader *r, const uint8_t *msg, size_t len)
{
    uint64_t seq = (uint64_t)cfs_read_i64(r);
    int64_t when = cfs_read_i64(r);
    if (r->err)
        return;
    cfs_pending mine;
    bool own = sender == n->self && take_pending(n, seq, &mine);
    if (!n->synced || n->failed) {
        /* Every member drops it alike: it was sent around a membership change. */
        if (own)
            reply(n, mine.token, mine.id, CFS_SYNCHRONISING, NULL,
                  "the cluster membership changed while this change was in flight, and it was not applied: read the "
                  "entry back before retrying");
        return;
    }
    cfs_request req;
    char reason[512] = "";
    cfs_writer payload;
    cfs_writer_init(&payload);
    int rc = cfs_decode_request(msg + CHANGE_HEADER, len - CHANGE_HEADER, &req);
    if (rc != CFS_OK || !cfs_op_changes(req.op)) {
        rc = CFS_INVALID;
        snprintf(reason, sizeof(reason), "malformed change");
    } else {
        rc = cfs_apply(n->ctx.store, &req, sender, when, &payload, reason, sizeof(reason));
    }
    if (rc == CFS_INTERNAL) {
        cfs_writer_free(&payload);
        if (own)
            reply(n, mine.token, mine.id, CFS_INTERNAL, NULL, reason);
        fail_node(n, "a change could not be applied");
        return;
    }
    if (own) {
        if (rc == CFS_OK && payload.err) {
            rc = CFS_INTERNAL;
            snprintf(reason, sizeof(reason), "the answer could not be built");
        }
        reply(n, mine.token, mine.id, rc, &payload, reason);
    }
    cfs_writer_free(&payload);
}

static long member_index(const cfs_node *n, uint32_t node)
{
    for (size_t i = 0; i < n->nmembers; i++)
        if (n->members[i] == node)
            return (long)i;
    return -1;
}

/* Every member decides from the same states, delivered in the same order, so they decide alike. */
static void evaluate_states(cfs_node *n)
{
    bool all = n->nmembers > 0, quorate = true, same = true;
    for (size_t i = 0; i < n->nmembers; i++) {
        const cfs_member_state *s = &n->states[i];
        all = all && s->seen;
        quorate = quorate && s->quorate;
        same = same && s->version == n->states[0].version &&
               memcmp(s->sum, n->states[0].sum, CFS_CHECKSUM_LEN) == 0;
    }
    bool was = n->synced;
    n->synced = all && quorate && same && !n->failed;
    n->diverged = all && !same;
    if (n->synced && !was)
        fprintf(stderr, "hyperlite-cfs: %zu member(s) agree on version %lld, changes are applied\n", n->nmembers,
                (long long)n->states[0].version);
    else if (n->diverged)
        fprintf(stderr, "hyperlite-cfs: the members hold different states, changes stay refused\n");
}

static void deliver_state(cfs_node *n, uint32_t sender, cfs_reader *r)
{
    cfs_member_state s = {.seen = true};
    s.quorate = cfs_read_u8(r) != 0;
    s.version = cfs_read_i64(r);
    uint32_t sum_len;
    const uint8_t *sum = cfs_read_blob(r, CFS_CHECKSUM_LEN, &sum_len);
    long i = member_index(n, sender);
    if (r->err || r->off != r->len || sum_len != CFS_CHECKSUM_LEN || i < 0)
        return;
    memcpy(s.sum, sum, CFS_CHECKSUM_LEN);
    n->states[i] = s;
    evaluate_states(n);
}

void cfs_node_deliver(cfs_node *n, uint32_t sender, const uint8_t *msg, size_t len)
{
    cfs_reader r;
    cfs_reader_init(&r, msg, len);
    uint32_t magic = cfs_read_u32(&r);
    uint8_t format = cfs_read_u8(&r), kind = cfs_read_u8(&r);
    if (r.err || magic != MSG_MAGIC || format != MSG_FORMAT) {
        fprintf(stderr, "hyperlite-cfs: ignored a message of an unknown format from node %u\n", sender);
        return;
    }
    if (kind == MSG_CHANGE)
        deliver_change(n, sender, &r, msg, len);
    else if (kind == MSG_STATE)
        deliver_state(n, sender, &r);
}

static int cmp_u32(const void *a, const void *b)
{
    uint32_t x = *(const uint32_t *)a, y = *(const uint32_t *)b;
    return x < y ? -1 : x > y;
}

void cfs_node_membership(cfs_node *n, const uint32_t *members, size_t count)
{
    if (count > CFS_MEMBERS_MAX)
        count = CFS_MEMBERS_MAX;
    memcpy(n->members, members, count * sizeof(*members));
    qsort(n->members, count, sizeof(*n->members), cmp_u32);
    n->nmembers = count;
    memset(n->states, 0, sizeof(n->states));
    n->synced = false;
    n->diverged = false;
    fprintf(stderr, "hyperlite-cfs: membership changed, %zu member(s)\n", count);
    /* Changes sent before this point and not delivered yet are dropped by every member (deliver_change). */
    fail_pending(n, CFS_SYNCHRONISING,
                 "the cluster membership changed while this change was in flight: read the entry back before "
                 "retrying");
    if (n->failed)
        return;
    /* Locks taken from a node that left are released, by every member at the same point of the stream. */
    if (cfs_store_drop_locks(n->ctx.store, n->members, n->nmembers) != CFS_OK) {
        fail_node(n, cfs_store_error(n->ctx.store));
        return;
    }
    send_state(n);
}

void cfs_node_quorum(cfs_node *n, bool quorate)
{
    if (n->quorate == quorate)
        return;
    n->quorate = quorate;
    fprintf(stderr, "hyperlite-cfs: %s\n", quorate ? "quorate" : "quorum lost, changes are refused");
    /* The other members learn it the same way as a state: a member without quorum holds every member back. */
    if (n->nmembers && !n->failed)
        send_state(n);
}
