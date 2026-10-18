/* The cluster's status (phase E, status.h): the table, the two operations on the wire, and the node's handling of
 * them, in local mode (kept at once) and through a status group (kept when delivered). */

#include "node.h"

#include "check.h"

static int last_status = -1;
static uint8_t last_answer[256];
static size_t last_len;

static void on_reply(void *arg, uint64_t token, const uint8_t *frame, size_t len)
{
    (void)arg;
    (void)token;
    last_status = frame[8];
    last_len = len < sizeof(last_answer) ? len : sizeof(last_answer);
    memcpy(last_answer, frame, last_len);
}

static int sent_count;
static uint8_t sent[64];
static size_t sent_len;

static int fake_send(void *arg, const uint8_t *msg, size_t len)
{
    (void)arg;
    sent_count++;
    sent_len = len < sizeof(sent) ? len : sizeof(sent);
    memcpy(sent, msg, sent_len);
    return 0;
}

static void request(cfs_node *n, uint8_t op, const void *blob, size_t len, uint32_t node, uid_t uid)
{
    cfs_writer w;
    cfs_writer_init(&w);
    cfs_write_u8(&w, op);
    cfs_write_u32(&w, 7);
    if (op == CFS_OP_STAT_PUT)
        cfs_write_blob(&w, blob, len);
    else
        cfs_write_u32(&w, node);
    last_status = -1;
    cfs_node_request(n, w.p, w.len, uid, 1, 1000);
    cfs_writer_free(&w);
}

static void table(void)
{
    cfs_stat_table t;
    cfs_stat_init(&t);
    CHECK_EQ(cfs_stat_set(&t, 1, (const uint8_t *)"aaa", 3, 1000), CFS_OK);
    CHECK_EQ(cfs_stat_set(&t, 2, (const uint8_t *)"bb", 2, 1500), CFS_OK);
    CHECK_EQ(cfs_stat_set(&t, 1, (const uint8_t *)"cccc", 4, 2000), CFS_OK); /* replaces node 1's */
    CHECK_EQ(t.n, 2);

    cfs_writer w;
    cfs_writer_init(&w);
    CHECK_EQ(cfs_stat_write(&t, 0, 2500, &w), CFS_OK);
    cfs_reader r;
    cfs_reader_init(&r, w.p, w.len);
    CHECK_EQ(cfs_read_u32(&r), 2);
    CHECK_EQ(cfs_read_u32(&r), 1);
    CHECK_EQ(cfs_read_i64(&r), 500); /* age */
    CHECK_EQ(cfs_read_u32(&r), 4);   /* size */
    CHECK_EQ(cfs_read_u32(&r), 2);
    CHECK_EQ(cfs_read_i64(&r), 1000);
    CHECK_EQ(cfs_read_u32(&r), 2);
    CHECK(!r.err && r.off == r.len);
    cfs_writer_free(&w);

    cfs_writer_init(&w);
    CHECK_EQ(cfs_stat_write(&t, 1, 2500, &w), CFS_OK);
    cfs_reader_init(&r, w.p, w.len);
    CHECK_EQ(cfs_read_i64(&r), 500);
    uint32_t n = 0;
    const uint8_t *p = cfs_read_blob(&r, CFS_STAT_MAX, &n);
    CHECK(n == 4 && memcmp(p, "cccc", 4) == 0);
    cfs_writer_free(&w);

    cfs_writer_init(&w);
    CHECK_EQ(cfs_stat_write(&t, 9, 2500, &w), CFS_NOT_FOUND);
    cfs_writer_free(&w);

    static uint8_t big[CFS_STAT_MAX + 1];
    CHECK_EQ(cfs_stat_set(&t, 3, big, sizeof(big), 3000), CFS_TOO_LARGE);
    CHECK_EQ(t.n, 2);

    /* Full: the oldest status of another node goes, never the one being replaced. */
    for (uint32_t node = 10; t.n < CFS_MEMBERS_MAX; node++)
        CHECK_EQ(cfs_stat_set(&t, node, (const uint8_t *)"x", 1, 4000 + node), CFS_OK);
    CHECK_EQ(cfs_stat_set(&t, 500, (const uint8_t *)"y", 1, 9000), CFS_OK);
    CHECK_EQ(t.n, CFS_MEMBERS_MAX);
    bool has1 = false, has2 = false, has500 = false;
    for (size_t i = 0; i < t.n; i++) {
        has1 = has1 || t.e[i].node == 1;
        has2 = has2 || t.e[i].node == 2;
        has500 = has500 || t.e[i].node == 500;
    }
    CHECK(has1 && !has2 && has500); /* node 2's status (time 1500) was the oldest */
    cfs_stat_free(&t);
}

static void wire(void)
{
    cfs_writer w;
    cfs_request req;
    cfs_writer_init(&w);
    cfs_write_u8(&w, CFS_OP_STAT_PUT);
    cfs_write_u32(&w, 3);
    cfs_write_blob(&w, "status", 6);
    CHECK_EQ(cfs_decode_request(w.p, w.len, &req), CFS_OK);
    CHECK(req.op == CFS_OP_STAT_PUT && req.data_len == 6 && memcmp(req.data, "status", 6) == 0);
    for (size_t cut = 0; cut < w.len; cut++)
        CHECK_EQ(cfs_decode_request(w.p, cut, &req), CFS_INVALID);
    cfs_writer_free(&w);

    cfs_writer_init(&w);
    cfs_write_u8(&w, CFS_OP_STAT_GET);
    cfs_write_u32(&w, 4);
    cfs_write_u32(&w, 12);
    CHECK_EQ(cfs_decode_request(w.p, w.len, &req), CFS_OK);
    CHECK(req.op == CFS_OP_STAT_GET && req.node == 12);
    cfs_write_u8(&w, 0); /* trailing byte */
    CHECK_EQ(cfs_decode_request(w.p, w.len, &req), CFS_INVALID);
    cfs_writer_free(&w);
}

static void node_local_and_group(void)
{
    cfs_node n;
    cfs_node_init(&n, NULL, CFS_MODE_LOCAL, 5, (cfs_transport){0});
    n.reply = on_reply;

    /* Local mode: kept at once, read back as this node's. */
    request(&n, CFS_OP_STAT_PUT, "mine", 4, 0, 0);
    CHECK_EQ(last_status, CFS_OK);
    request(&n, CFS_OP_STAT_GET, NULL, 0, 5, 0);
    CHECK_EQ(last_status, CFS_OK);
    request(&n, CFS_OP_STAT_GET, NULL, 0, 6, 0);
    CHECK_EQ(last_status, CFS_NOT_FOUND);

    /* Only root publishes; anyone allowed on the socket reads. */
    request(&n, CFS_OP_STAT_PUT, "not mine", 8, 0, 1000);
    CHECK_EQ(last_status, CFS_FORBIDDEN);
    request(&n, CFS_OP_STAT_GET, NULL, 0, 0, 1000);
    CHECK_EQ(last_status, CFS_OK);

    /* Through a status group: sent, kept when the group delivers it (to this node too). */
    n.stat_tr = (cfs_transport){.send = fake_send};
    request(&n, CFS_OP_STAT_PUT, "group", 5, 0, 0);
    CHECK(last_status == CFS_OK && sent_count == 1 && sent_len == 5 && memcmp(sent, "group", 5) == 0);
    cfs_node_stat_deliver(&n, 7, (const uint8_t *)"theirs", 6);
    CHECK_EQ(n.stat.n, 2);
    request(&n, CFS_OP_STAT_GET, NULL, 0, 7, 0);
    CHECK_EQ(last_status, CFS_OK);
    cfs_node_free(&n);
}

int main(void)
{
    table();
    wire();
    node_local_and_group();
    return check_failures ? 1 : 0;
}
