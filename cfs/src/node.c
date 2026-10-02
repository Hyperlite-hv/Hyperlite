#include "node.h"

#include "path.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* Messages between nodes, little-endian like the socket protocol:
 *   u32 magic, u8 format version, u8 kind, then
 *   CHANGE:  i64 sender's message number, i64 sender's time (seconds), then a request body as a client sent it;
 *   STATE:   u8 quorate, u64 ring, i64 term, i64 cluster version, blob SHA-256 of tree and locks;
 *   APPLIED: u64 changes delivered by the sender since the agreement (its confirmation).
 * The sender's node id comes from the transport (Corosync names it), never from the message. */
#define MSG_MAGIC 0x53464348u /* "HCFS" */
#define MSG_FORMAT 2
#define MSG_CHANGE 1
#define MSG_STATE 2
#define MSG_XFER_BEGIN 3 /* i64 version, i64 term, blob checksum */
#define MSG_XFER_DATA 4  /* u32 count, then records: u8 1 + string path, i64 version, i64 mtime, blob data, or
                            u8 2 + string name, string owner, u32 node, i64 expires */
#define MSG_XFER_END 5   /* u32 records sent, i64 next id */
#define MSG_APPLIED 6
#define XFER_CHUNK (512u * 1024u) /* a data message is sent once it holds this much */
#define XFER_BUF_MAX (2 * CFS_TREE_MAX) /* the records of a full tree, with room for their framing */
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

static void xfer_reset(cfs_node *n)
{
    free(n->xfer_buf);
    n->xfer_buf = NULL;
    n->xfer_len = n->xfer_cap = 0;
    n->xfer_records = 0;
    n->transferring = false;
    n->xfer_needed = false;
}

void cfs_node_free(cfs_node *n)
{
    xfer_reset(n);
    for (size_t i = 0; i < n->nawaiting; i++)
        free(n->awaiting[i].frame);
    free(n->awaiting);
    n->awaiting = NULL;
    n->nawaiting = 0;
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

static const char UNCERTAIN_REASON[] =
    "the cluster membership changed before every member confirmed this change: it may or may not have been "
    "applied; read the entry back before retrying";

/* Answer the changes applied here that every member has confirmed since (all of them, on a single member). */
static void confirm(cfs_node *n)
{
    uint64_t lowest = n->pos;
    for (size_t i = 0; i < n->nmembers; i++)
        if (n->members[i] != n->self && n->confirmed[i] < lowest)
            lowest = n->confirmed[i];
    size_t done = 0;
    while (done < n->nawaiting && n->awaiting[done].pos <= lowest) {
        cfs_awaiting *a = &n->awaiting[done++];
        if (n->reply)
            n->reply(n->reply_arg, a->token, a->frame, a->len);
        free(a->frame);
    }
    if (done) {
        memmove(n->awaiting, n->awaiting + done, (n->nawaiting - done) * sizeof(*n->awaiting));
        n->nawaiting -= done;
    }
}

/* The membership changed, or the members stopped agreeing, before the changes still waiting were confirmed. */
static void fail_awaiting(cfs_node *n)
{
    for (size_t i = 0; i < n->nawaiting; i++) {
        reply(n, n->awaiting[i].token, n->awaiting[i].id, CFS_UNCERTAIN, NULL, UNCERTAIN_REASON);
        free(n->awaiting[i].frame);
    }
    n->nawaiting = 0;
}

static bool await_confirmation(cfs_node *n, const cfs_pending *p, int status, const cfs_writer *payload,
                               const char *reason)
{
    if (n->nawaiting == n->cap_awaiting) {
        size_t cap = n->cap_awaiting ? n->cap_awaiting * 2 : 16;
        cfs_awaiting *a = realloc(n->awaiting, cap * sizeof(*a));
        if (!a)
            return false;
        n->awaiting = a;
        n->cap_awaiting = cap;
    }
    cfs_writer out;
    cfs_writer_init(&out);
    cfs_answer(&out, p->id, status, payload, reason);
    if (out.err) {
        cfs_writer_free(&out);
        return false;
    }
    n->awaiting[n->nawaiting++] = (cfs_awaiting){n->pos, p->token, p->id, out.p, out.len};
    return true;
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
    for (size_t i = 0; i < n->nawaiting; i++)
        if (n->awaiting[i].token == token)
            n->awaiting[i].token = UINT64_MAX;
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
    cfs_write_i64(&w, (int64_t)n->ring);
    cfs_write_i64(&w, cfs_store_term(n->ctx.store));
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

/* Tell the members how many changes this node applied; at most one confirmation in flight, so a busy cluster sends
 * about one per member per round trip, not one per change. */
static void send_confirmation(cfs_node *n)
{
    if (n->confirming || n->pos == n->reported || n->nmembers < 2 || n->failed)
        return;
    cfs_writer w;
    cfs_writer_init(&w);
    cfs_write_u32(&w, MSG_MAGIC);
    cfs_write_u8(&w, MSG_FORMAT);
    cfs_write_u8(&w, MSG_APPLIED);
    cfs_write_i64(&w, (int64_t)n->pos);
    if (w.err) {
        cfs_writer_free(&w);
        return;
    }
    if (send_or_queue(n, w.p, w.len)) {
        n->confirming = true;
        n->reported = n->pos;
    } else {
        fprintf(stderr, "hyperlite-cfs: cannot send this node's confirmation\n");
    }
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
              n->diverged ? "the cluster members still hold different states after a state transfer: changes are "
                            "refused; see the hyperlite-cfs log of each node"
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

/* ---- State transfer ------------------------------------------------------------------------------------------ */

typedef struct {
    cfs_node *n;
    cfs_writer w;
    size_t count_at;
    uint32_t count, total;
    bool err;
} dump_ctx;

static void chunk_start(dump_ctx *d)
{
    cfs_writer_init(&d->w);
    cfs_write_u32(&d->w, MSG_MAGIC);
    cfs_write_u8(&d->w, MSG_FORMAT);
    cfs_write_u8(&d->w, MSG_XFER_DATA);
    d->count_at = d->w.len;
    cfs_write_u32(&d->w, 0);
    d->count = 0;
}

static void chunk_send(dump_ctx *d)
{
    cfs_patch_u32(&d->w, d->count_at, d->count);
    if (d->w.err || !send_or_queue(d->n, d->w.p, d->w.len))
        d->err = true;
    if (d->w.err)
        cfs_writer_free(&d->w);
    d->w.p = NULL; /* send_or_queue owns it */
    chunk_start(d);
}

/* Room for one more record of `size` bytes, sending what is held first when it would not fit in one message. */
static void chunk_room(dump_ctx *d, size_t size)
{
    if (d->count && d->w.len + size > CFS_FRAME_MAX - 64)
        chunk_send(d);
}

static int dump_entry(void *p, const char *path, int64_t version, int64_t mtime, const uint8_t *data, size_t len)
{
    dump_ctx *d = p;
    chunk_room(d, 1 + 4 + strlen(path) + 16 + 4 + len);
    cfs_write_u8(&d->w, 1);
    cfs_write_string(&d->w, path);
    cfs_write_i64(&d->w, version);
    cfs_write_i64(&d->w, mtime);
    cfs_write_blob(&d->w, data, len);
    d->count++;
    d->total++;
    if (d->w.len >= XFER_CHUNK)
        chunk_send(d);
    return d->err || d->w.err ? -1 : 0;
}

static int dump_lock(void *p, const char *name, const char *owner, uint32_t node, int64_t expires)
{
    dump_ctx *d = p;
    chunk_room(d, 1 + 8 + strlen(name) + strlen(owner) + 12);
    cfs_write_u8(&d->w, 2);
    cfs_write_string(&d->w, name);
    cfs_write_string(&d->w, owner);
    cfs_write_u32(&d->w, node);
    cfs_write_i64(&d->w, expires);
    d->count++;
    d->total++;
    return d->err || d->w.err ? -1 : 0;
}

static void fail_node(cfs_node *n, const char *why);

/* The source multicasts its whole state: a begin, data messages, an end. */
static void send_dump(cfs_node *n)
{
    int64_t version = 0, next_id = 0, entries, bytes;
    uint8_t sum[CFS_CHECKSUM_LEN];
    if (cfs_store_status(n->ctx.store, &version, sum, &entries, &bytes) != CFS_OK) {
        fail_node(n, cfs_store_error(n->ctx.store));
        return;
    }
    cfs_writer w;
    cfs_writer_init(&w);
    cfs_write_u32(&w, MSG_MAGIC);
    cfs_write_u8(&w, MSG_FORMAT);
    cfs_write_u8(&w, MSG_XFER_BEGIN);
    cfs_write_i64(&w, version);
    cfs_write_i64(&w, cfs_store_term(n->ctx.store));
    cfs_write_blob(&w, sum, sizeof(sum));
    bool ok = !w.err && send_or_queue(n, w.p, w.len);
    if (w.err)
        cfs_writer_free(&w);
    dump_ctx d = {.n = n};
    chunk_start(&d);
    if (ok && cfs_store_dump(n->ctx.store, dump_entry, dump_lock, &d, &version, &next_id) != CFS_OK)
        ok = false;
    if (ok && d.count)
        chunk_send(&d);
    cfs_writer_free(&d.w);
    if (!ok || d.err) {
        fail_node(n, "this node could not send its state to the members that differ");
        return;
    }
    cfs_writer_init(&w);
    cfs_write_u32(&w, MSG_MAGIC);
    cfs_write_u8(&w, MSG_FORMAT);
    cfs_write_u8(&w, MSG_XFER_END);
    cfs_write_u32(&w, d.total);
    cfs_write_i64(&w, next_id);
    if (w.err || !send_or_queue(n, w.p, w.len)) {
        if (w.err)
            cfs_writer_free(&w);
        fail_node(n, "this node could not finish sending its state");
    }
}

static long member_index(const cfs_node *n, uint32_t node);

/* Every member runs this at the same point of the delivery order, with the same states: they pick the same source. */
static void start_transfer(cfs_node *n)
{
    size_t src = 0;
    for (size_t i = 1; i < n->nmembers; i++) { /* members are sorted: on a tie the lowest node id stays */
        const cfs_member_state *a = &n->states[i], *b = &n->states[src];
        if (a->term > b->term || (a->term == b->term && a->version > b->version))
            src = i;
    }
    long me = member_index(n, n->self);
    n->transferring = true;
    n->transferred = true;
    n->xfer_source = n->members[src];
    n->xfer_needed = me >= 0 && (n->states[me].version != n->states[src].version ||
                                 memcmp(n->states[me].sum, n->states[src].sum, CFS_CHECKSUM_LEN) != 0);
    fprintf(stderr, "hyperlite-cfs: the members differ; node %u sends its state (term %lld, version %lld)%s\n",
            n->xfer_source, (long long)n->states[src].term, (long long)n->states[src].version,
            n->xfer_needed ? " and this node takes it" : "");
    if (n->xfer_needed && me >= 0 && n->states[me].term == n->states[src].term &&
        n->states[me].version == n->states[src].version)
        fprintf(stderr, "hyperlite-cfs: WARNING: this node holds a different state at the same term and version, "
                        "which only two writable partitions can produce (an expected-votes override on both "
                        "sides?): its changes since then are replaced by node %u's\n",
                n->xfer_source);
    if (n->xfer_source == n->self)
        send_dump(n);
}

static void deliver_xfer_begin(cfs_node *n, cfs_reader *r)
{
    n->xfer_version = cfs_read_i64(r);
    n->xfer_term = cfs_read_i64(r);
    uint32_t len;
    const uint8_t *sum = cfs_read_blob(r, CFS_CHECKSUM_LEN, &len);
    if (r->err || len != CFS_CHECKSUM_LEN) {
        fail_node(n, "the source's state transfer began with a malformed message");
        return;
    }
    memcpy(n->xfer_sum, sum, CFS_CHECKSUM_LEN);
    n->xfer_len = 0;
    n->xfer_records = 0;
}

static void deliver_xfer_data(cfs_node *n, cfs_reader *r)
{
    uint32_t count = cfs_read_u32(r);
    if (r->err)
        return;
    if (!n->xfer_needed)
        return;
    size_t len = r->len - r->off;
    if (n->xfer_len + len > XFER_BUF_MAX) {
        fail_node(n, "the source's state is larger than any valid state");
        return;
    }
    if (n->xfer_len + len > n->xfer_cap) {
        size_t cap = n->xfer_cap ? n->xfer_cap : 1u << 20;
        while (cap < n->xfer_len + len)
            cap *= 2;
        uint8_t *p = realloc(n->xfer_buf, cap);
        if (!p) {
            fail_node(n, "out of memory while receiving the source's state");
            return;
        }
        n->xfer_buf = p;
        n->xfer_cap = cap;
    }
    if (len)
        memcpy(n->xfer_buf + n->xfer_len, r->p + r->off, len); /* an empty message leaves the buffer unallocated */
    n->xfer_len += len;
    n->xfer_records += count;
}

/* Replace this node's state with the records received, in one transaction, and check it is the source's. */
static bool apply_transfer(cfs_node *n, uint32_t total, int64_t next_id)
{
    cfs_store *st = n->ctx.store;
    if (total != n->xfer_records)
        return false;
    if (cfs_store_replace_begin(st) != CFS_OK)
        return false;
    cfs_reader r;
    cfs_reader_init(&r, n->xfer_buf, n->xfer_len);
    int rc = CFS_OK;
    for (uint32_t i = 0; i < total && rc == CFS_OK; i++) {
        uint8_t type = cfs_read_u8(&r);
        if (type == 1) {
            char path[CFS_PATH_MAX + 1];
            cfs_read_string(&r, path, CFS_PATH_MAX);
            int64_t version = cfs_read_i64(&r), mtime = cfs_read_i64(&r);
            uint32_t len;
            const uint8_t *data = cfs_read_blob(&r, CFS_FILE_MAX, &len);
            rc = r.err || !cfs_path_valid(path, false) ? CFS_INVALID
                                                       : cfs_store_replace_entry(st, path, version, mtime, data, len);
        } else if (type == 2) {
            char name[CFS_NAME_MAX + 1], owner[CFS_NAME_MAX + 1];
            cfs_read_string(&r, name, CFS_NAME_MAX);
            cfs_read_string(&r, owner, CFS_NAME_MAX);
            uint32_t node = cfs_read_u32(&r);
            int64_t expires = cfs_read_i64(&r);
            rc = r.err ? CFS_INVALID : cfs_store_replace_lock(st, name, owner, node, expires);
        } else {
            rc = CFS_INVALID;
        }
    }
    if (rc != CFS_OK || r.off != r.len) {
        cfs_store_replace_abort(st);
        return false;
    }
    if (cfs_store_replace_commit(st, n->xfer_version, next_id, n->xfer_term) != CFS_OK)
        return false;
    int64_t version, entries, bytes;
    uint8_t sum[CFS_CHECKSUM_LEN];
    return cfs_store_status(st, &version, sum, &entries, &bytes) == CFS_OK && version == n->xfer_version &&
           cfs_store_term(st) == n->xfer_term && memcmp(sum, n->xfer_sum, CFS_CHECKSUM_LEN) == 0;
}

static void deliver_xfer_end(cfs_node *n, cfs_reader *r)
{
    uint32_t total = cfs_read_u32(r);
    int64_t next_id = cfs_read_i64(r);
    if (r->err) {
        fail_node(n, "the source's state transfer ended with a malformed message");
        return;
    }
    if (n->xfer_needed) {
        if (!apply_transfer(n, total, next_id)) {
            xfer_reset(n);
            fail_node(n, "the state received from the source could not be applied, or does not match its checksum");
            return;
        }
        fprintf(stderr, "hyperlite-cfs: took node %u's state, version %lld\n", n->xfer_source,
                (long long)n->xfer_version);
    }
    xfer_reset(n);
    /* Every member, at the same point of the order, starts the agreement again from its new state. */
    memset(n->states, 0, sizeof(n->states));
    send_state(n);
}

/* ---- Delivery ----------------------------------------------------------------------------------------------------- */

static void fail_node(cfs_node *n, const char *why)
{
    fprintf(stderr, "hyperlite-cfs: %s; this node leaves the cluster so as not to diverge from it\n", why);
    n->failed = true;
    n->synced = false;
    fail_pending(n, CFS_INTERNAL, "this node failed to apply the change and left the cluster");
    fail_awaiting(n);
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
        /* Every member here drops it alike: it was sent around a membership change. A member that left before the
         * change may still have applied it, hence "uncertain". */
        if (own)
            reply(n, mine.token, mine.id, CFS_UNCERTAIN, NULL, UNCERTAIN_REASON);
        return;
    }
    n->pos++;
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
        /* Answered once every member confirmed it: before that, a partition could leave it only on a minority. */
        if (!await_confirmation(n, &mine, rc, &payload, reason))
            reply(n, mine.token, mine.id, CFS_UNCERTAIN, NULL,
                  "out of memory while waiting for the members to confirm this change: read the entry back");
        confirm(n);
    }
    cfs_writer_free(&payload);
    send_confirmation(n);
}

static void deliver_applied(cfs_node *n, uint32_t sender, cfs_reader *r)
{
    uint64_t pos = (uint64_t)cfs_read_i64(r);
    long i = member_index(n, sender);
    if (r->err || r->off != r->len || i < 0 || !n->synced || n->failed)
        return; /* one sent around a membership change counts for nothing: the agreement starts the count over */
    if (pos > n->pos)
        pos = n->pos; /* a member cannot have applied more than was delivered; never trust a peer beyond that */
    if (pos > n->confirmed[i])
        n->confirmed[i] = pos;
    if (sender == n->self) {
        n->confirming = false;
        send_confirmation(n);
    }
    confirm(n);
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
    bool all = n->nmembers > 0, quorate = true, same = true, one_ring = true;
    for (size_t i = 0; i < n->nmembers; i++) {
        const cfs_member_state *s = &n->states[i];
        all = all && s->seen;
        quorate = quorate && s->quorate;
        /* A state counts only if sent in this node's current ring. Corosync reports a new membership before the
         * ring that brings it (cpg_sync_activate), so the first state of each member names the old ring; the ring
         * arrives before any message of the new one is delivered, so every member judges each state alike, and each
         * member sends its state again once it knows the ring (cfs_node_ring). */
        one_ring = one_ring && s->ring == n->ring;
        /* The data alone: a state sent just before an agreement can name the term before it, and the agreement
         * brings every member to the same term anyway. */
        same = same && s->version == n->states[0].version &&
               memcmp(s->sum, n->states[0].sum, CFS_CHECKSUM_LEN) == 0;
    }
    all = all && one_ring;
    bool was = n->synced;
    n->synced = all && quorate && same && !n->failed && !n->transferring;
    n->diverged = all && !same && n->transferred && !n->transferring;
    if (was && !n->synced)
        fail_awaiting(n);
    if (all && quorate && !same && !n->transferred && !n->failed) {
        start_transfer(n);
        return;
    }
    if (n->synced && !was) {
        /* The agreement: the state is now the one of this ring, which only grows, so a later quorate membership
         * always outranks a minority that never agreed since (design, section 8.4). */
        int64_t ring = (int64_t)n->states[0].ring, term = 0;
        for (size_t i = 0; i < n->nmembers; i++)
            if (n->states[i].term > term)
                term = n->states[i].term;
        if (cfs_store_set_term(n->ctx.store, ring > term ? ring : term) != CFS_OK) {
            fail_node(n, cfs_store_error(n->ctx.store));
            return;
        }
        if (ring < term)
            fprintf(stderr, "hyperlite-cfs: WARNING: Corosync's ring %lld is behind this state's term %lld; was "
                            "/var/lib/corosync wiped? The term stays\n",
                    (long long)ring, (long long)term);
        n->pos = n->reported = 0;
        n->confirming = false;
        memset(n->confirmed, 0, sizeof(n->confirmed));
        fprintf(stderr, "hyperlite-cfs: %zu member(s) agree on version %lld (term %lld), changes are applied\n",
                n->nmembers, (long long)n->states[0].version, (long long)cfs_store_term(n->ctx.store));
    } else if (n->diverged)
        fprintf(stderr, "hyperlite-cfs: the members still hold different states after a state transfer, changes "
                        "stay refused\n");
}

static void deliver_state(cfs_node *n, uint32_t sender, cfs_reader *r)
{
    cfs_member_state s = {.seen = true};
    s.quorate = cfs_read_u8(r) != 0;
    s.ring = (uint64_t)cfs_read_i64(r);
    s.term = cfs_read_i64(r);
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
    else if (kind == MSG_APPLIED)
        deliver_applied(n, sender, &r);
    else if (n->transferring && sender == n->xfer_source && !n->failed) {
        if (kind == MSG_XFER_BEGIN)
            deliver_xfer_begin(n, &r);
        else if (kind == MSG_XFER_DATA)
            deliver_xfer_data(n, &r);
        else if (kind == MSG_XFER_END)
            deliver_xfer_end(n, &r);
    }
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
    n->transferred = false;
    xfer_reset(n); /* a transfer cut by the membership change is dropped; the next agreement starts over */
    fprintf(stderr, "hyperlite-cfs: membership changed, %zu member(s)\n", count);
    /* Changes sent before this point and not delivered yet are dropped by every member still here (deliver_change),
     * but one that left may have applied them; those applied here were not confirmed by every member. */
    fail_pending(n, CFS_UNCERTAIN, UNCERTAIN_REASON);
    fail_awaiting(n);
    if (n->failed)
        return;
    /* Locks taken from a node that left are released, by every member at the same point of the stream. */
    if (cfs_store_drop_locks(n->ctx.store, n->members, n->nmembers) != CFS_OK) {
        fail_node(n, cfs_store_error(n->ctx.store));
        return;
    }
    send_state(n);
}

void cfs_node_ring(cfs_node *n, uint64_t seq)
{
    if (n->ring == seq)
        return;
    n->ring = seq;
    /* Agreement needs every member in the same ring: a member that heard of this one after sending its state says
     * so again. */
    if (n->nmembers && !n->failed)
        send_state(n);
}

void cfs_node_quorum(cfs_node *n, bool quorate)
{
    if (n->quorate == quorate)
        return;
    n->quorate = quorate;
    fprintf(stderr, "hyperlite-cfs: %s\n",
            quorate ? "quorate"
                    : "not quorate (no quorum, or the quorum service has not spoken for this ring yet): changes are "
                      "refused");
    /* The other members learn it the same way as a state: a member without quorum holds every member back. */
    if (n->nmembers && !n->failed)
        send_state(n);
}
