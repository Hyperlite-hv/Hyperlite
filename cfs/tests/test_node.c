/* Several nodes in one process over a simulated Corosync: one agreed order per partition, membership changes at the
 * same point of that order for every member, quorum = a strict majority of the configured nodes. Each scenario checks
 * what the design promises: identical states, one answer per change and the same on every node, no change applied
 * without quorum or around a membership change, and a node that cannot apply a change leaving instead of diverging. */

#include "node.h"

#include <sqlite3.h>

#include "check.h"

#define NODES 3
#define QUEUE_MAX 4096

typedef struct {
    uint32_t sender;
    uint8_t *msg;
    size_t len;
} sim_msg;

typedef struct {
    cfs_node node;
    cfs_store *store;
    char db[256];
    int group;   /* partition: nodes with the same group see each other; -1 once it left */
    int status;  /* last answer's status, -1 when none arrived */
    uint8_t answer[64];
    size_t answer_len;
} sim_node;

static sim_node sim[NODES];
static sim_msg queue[QUEUE_MAX];
static size_t queued;
static int64_t clock_now = 1000;

static int sim_send(void *arg, const uint8_t *msg, size_t len)
{
    sim_node *s = arg;
    if (queued == QUEUE_MAX)
        return 1;
    uint8_t *copy = malloc(len);
    memcpy(copy, msg, len);
    queue[queued++] = (sim_msg){s->node.self, copy, len};
    return 0;
}

static void sim_leave(void *arg)
{
    ((sim_node *)arg)->group = -1;
}

static void on_reply(void *arg, uint64_t token, const uint8_t *frame, size_t len)
{
    (void)token;
    sim_node *s = arg;
    s->status = frame[8];
    s->answer_len = len < sizeof(s->answer) ? len : sizeof(s->answer);
    memcpy(s->answer, frame, s->answer_len);
}

static sim_node *by_id(uint32_t id)
{
    return &sim[id - 1];
}

/* Deliver everything queued, in order, to the members of the sender's partition. */
static void pump(void)
{
    for (size_t i = 0; i < queued; i++) {
        sim_node *from = by_id(queue[i].sender);
        for (int k = 0; k < NODES; k++)
            if (from->group >= 0 && sim[k].group == from->group)
                cfs_node_deliver(&sim[k].node, queue[i].sender, queue[i].msg, queue[i].len);
        free(queue[i].msg);
        queue[i].msg = NULL;
    }
    queued = 0;
}

/* Messages not delivered when the membership changes are lost by everyone: allowed by virtual synchrony, and the
 * harder case for the node, which must not count on them. */
static void drop_queue(void)
{
    for (size_t i = 0; i < queued; i++)
        free(queue[i].msg);
    queued = 0;
}

/* New partitions (group per node, -1 for a node that stays out), then the quorum and membership callbacks in the
 * order corosync.c calls them. */
static void partition(const int groups[NODES])
{
    drop_queue();
    for (int k = 0; k < NODES; k++)
        if (sim[k].group != -1 || groups[k] != -1)
            sim[k].group = groups[k];
    for (int k = 0; k < NODES; k++) {
        if (sim[k].group < 0)
            continue;
        uint32_t members[NODES];
        size_t n = 0;
        for (int j = 0; j < NODES; j++)
            if (sim[j].group == sim[k].group)
                members[n++] = sim[j].node.self;
        cfs_node_quorum(&sim[k].node, n * 2 > NODES);
        cfs_node_membership(&sim[k].node, members, n);
    }
    pump();
}

static void setup(void)
{
    for (int k = 0; k < NODES; k++) {
        char err[256];
        temp_db(sim[k].db, sizeof(sim[k].db));
        sim[k].store = cfs_store_open(sim[k].db, err, sizeof(err));
        if (!sim[k].store) {
            fprintf(stderr, "%s\n", err);
            exit(1);
        }
        cfs_node_init(&sim[k].node, sim[k].store, CFS_MODE_CLUSTER, (uint32_t)k + 1,
                      (cfs_transport){.send = sim_send, .leave = sim_leave, .arg = &sim[k]});
        sim[k].node.reply = on_reply;
        sim[k].node.reply_arg = &sim[k];
        sim[k].group = 0;
    }
    int all[NODES] = {0, 0, 0};
    partition(all);
}

static void teardown(void)
{
    drop_queue();
    for (int k = 0; k < NODES; k++) {
        cfs_node_free(&sim[k].node);
        cfs_store_close(sim[k].store);
        temp_db_remove(sim[k].db);
    }
}

/* Send a PUT from node `k`; the answer lands in sim[k].status (maybe only after pump()). */
static void put(int k, const char *path, const char *data, int64_t expected)
{
    cfs_writer w;
    cfs_writer_init(&w);
    cfs_write_u8(&w, CFS_OP_PUT);
    cfs_write_u32(&w, 7);
    cfs_write_string(&w, path);
    cfs_write_i64(&w, expected);
    cfs_write_string(&w, data);
    sim[k].status = -1;
    cfs_node_request(&sim[k].node, w.p, w.len, 0, 1, clock_now);
    cfs_writer_free(&w);
}

static void lock(int k, const char *name, const char *owner)
{
    cfs_writer w;
    cfs_writer_init(&w);
    cfs_write_u8(&w, CFS_OP_LOCK);
    cfs_write_u32(&w, 8);
    cfs_write_string(&w, name);
    cfs_write_string(&w, owner);
    cfs_write_u32(&w, 600);
    sim[k].status = -1;
    cfs_node_request(&sim[k].node, w.p, w.len, 0, 1, clock_now);
    cfs_writer_free(&w);
}

static void state(int k, int64_t *version, uint8_t sum[CFS_CHECKSUM_LEN])
{
    int64_t entries, bytes;
    CHECK_EQ(cfs_store_status(sim[k].store, version, sum, &entries, &bytes), CFS_OK);
}

static bool same_state(int a, int b)
{
    int64_t va, vb;
    uint8_t sa[CFS_CHECKSUM_LEN], sb[CFS_CHECKSUM_LEN];
    state(a, &va, sa);
    state(b, &vb, sb);
    return va == vb && memcmp(sa, sb, CFS_CHECKSUM_LEN) == 0;
}

static void test_changes_are_applied_alike_everywhere(void)
{
    setup();
    for (int k = 0; k < NODES; k++)
        CHECK(sim[k].node.synced);

    /* Two nodes create the same entry at once: one wins, the other gets a conflict, and every node agrees. */
    put(0, "/vms/100.json", "{\"from\":1}", CFS_MUST_NOT_EXIST);
    put(1, "/vms/100.json", "{\"from\":2}", CFS_MUST_NOT_EXIST);
    CHECK_EQ(sim[0].status, -1); /* answered only once its own copy is applied */
    pump();
    CHECK_EQ(sim[0].status, CFS_OK);
    CHECK_EQ(sim[1].status, CFS_CONFLICT);

    /* Many changes from every node, interleaved. */
    char path[64], data[64];
    for (int i = 0; i < 300; i++) {
        snprintf(path, sizeof(path), "/n/%d", i % 17);
        snprintf(data, sizeof(data), "%d", i);
        clock_now = 1000 + i;
        put(i % NODES, path, data, CFS_ANY_VERSION);
        if (i % 7 == 0)
            pump();
    }
    pump();
    CHECK(same_state(0, 1));
    CHECK(same_state(1, 2));

    /* The time stored is the sender's, so even mtime is identical. */
    int64_t v0, m0, v2, m2;
    uint8_t *d0, *d2;
    size_t l0, l2;
    CHECK_EQ(cfs_store_get(sim[0].store, "/n/3", &v0, &m0, &d0, &l0), CFS_OK);
    CHECK_EQ(cfs_store_get(sim[2].store, "/n/3", &v2, &m2, &d2, &l2), CFS_OK);
    CHECK(v0 == v2 && m0 == m2 && l0 == l2 && memcmp(d0, d2, l0) == 0);
    free(d0);
    free(d2);
    teardown();
}

static void test_no_change_without_quorum(void)
{
    setup();
    int split[NODES] = {0, 0, 1}; /* node 3 alone */
    partition(split);
    CHECK(sim[0].node.synced && sim[1].node.synced);
    CHECK(!sim[2].node.quorate && !sim[2].node.synced);
    put(2, "/x", "minority", CFS_ANY_VERSION);
    CHECK_EQ(sim[2].status, CFS_READ_ONLY); /* refused at once, nothing sent */
    put(0, "/x", "majority", CFS_ANY_VERSION);
    pump();
    CHECK_EQ(sim[0].status, CFS_OK);
    CHECK(same_state(0, 1));
    CHECK(!same_state(0, 2));

    /* Back together, the members hold different states: until the state transfer exists (step B2) changes stay
     * refused rather than applied on top of different trees. */
    int all[NODES] = {0, 0, 0};
    partition(all);
    for (int k = 0; k < NODES; k++)
        CHECK(sim[k].node.diverged && !sim[k].node.synced);
    put(0, "/y", "1", CFS_ANY_VERSION);
    CHECK_EQ(sim[0].status, CFS_SYNCHRONISING);
    teardown();
}

static void test_a_change_in_flight_at_a_membership_change_is_applied_nowhere(void)
{
    setup();
    int64_t before;
    uint8_t sum[CFS_CHECKSUM_LEN];
    state(0, &before, sum);
    put(0, "/z", "lost", CFS_ANY_VERSION);
    int split[NODES] = {0, 0, 1};
    partition(split); /* the change was not delivered before the new membership */
    CHECK_EQ(sim[0].status, CFS_SYNCHRONISING);
    for (int k = 0; k < NODES; k++) {
        int64_t v;
        state(k, &v, sum);
        CHECK_EQ(v, before);
    }

    /* A change sent after the membership changed at the protocol level, but before its sender heard of it, reaches
     * the new members after the states of those who heard first: everyone is still agreeing, and drops it. */
    for (int k = 0; k < NODES; k++)
        sim[k].group = 0;
    uint32_t members[NODES] = {1, 2, 3};
    for (int k = 1; k < NODES; k++) {
        cfs_node_quorum(&sim[k].node, true);
        cfs_node_membership(&sim[k].node, members, NODES);
    }
    CHECK(sim[0].node.synced); /* node 1 still believes the old membership agreed */
    put(0, "/z", "early", CFS_ANY_VERSION);
    cfs_node_membership(&sim[0].node, members, NODES);
    CHECK_EQ(sim[0].status, CFS_SYNCHRONISING);
    pump();
    for (int k = 0; k < NODES; k++) {
        int64_t v;
        state(k, &v, sum);
        CHECK_EQ(v, before);
    }
    teardown();
}

static void test_the_locks_of_a_node_that_left_are_released(void)
{
    setup();
    lock(2, "vm:100", "migrate@node3");
    pump();
    CHECK_EQ(sim[2].status, CFS_OK);
    lock(0, "vm:100", "backup@node1");
    pump();
    CHECK_EQ(sim[0].status, CFS_LOCKED);
    int split[NODES] = {0, 0, 1};
    partition(split);
    lock(0, "vm:100", "backup@node1");
    pump();
    CHECK_EQ(sim[0].status, CFS_OK);
    CHECK(same_state(0, 1));
    teardown();
}

static void test_a_node_that_cannot_apply_a_change_leaves(void)
{
    setup();
    /* Another connection holds node 2's database: its next write fails there, and only there. */
    sqlite3 *other;
    CHECK_EQ(sqlite3_open(sim[1].db, &other), SQLITE_OK);
    CHECK_EQ(sqlite3_exec(other, "BEGIN EXCLUSIVE", NULL, NULL, NULL), SQLITE_OK);
    put(0, "/a", "1", CFS_ANY_VERSION);
    pump();
    CHECK_EQ(sim[0].status, CFS_OK);
    CHECK(sim[1].node.failed);
    CHECK_EQ(sim[1].group, -1); /* it left the group */
    sqlite3_exec(other, "ROLLBACK", NULL, NULL, NULL);
    sqlite3_close(other);
    put(1, "/b", "1", CFS_ANY_VERSION);
    CHECK_EQ(sim[1].status, CFS_READ_ONLY);

    /* The others carry on, as a majority. */
    int rest[NODES] = {0, -1, 0};
    partition(rest);
    put(2, "/c", "1", CFS_ANY_VERSION);
    pump();
    CHECK_EQ(sim[2].status, CFS_OK);
    CHECK(same_state(0, 2));
    teardown();
}

static void test_losing_the_quorum_without_a_membership_change(void)
{
    setup();
    /* votequorum can drop the quorum without CPG changing (expected votes raised): the node says so, and every member
     * stops applying changes, since one of them is held back. */
    cfs_node_quorum(&sim[2].node, false);
    pump();
    for (int k = 0; k < NODES; k++)
        CHECK(!sim[k].node.synced);
    put(0, "/q", "1", CFS_ANY_VERSION);
    CHECK_EQ(sim[0].status, CFS_SYNCHRONISING);
    cfs_node_quorum(&sim[2].node, true);
    pump();
    for (int k = 0; k < NODES; k++)
        CHECK(sim[k].node.synced);
    put(0, "/q", "1", CFS_ANY_VERSION);
    pump();
    CHECK_EQ(sim[0].status, CFS_OK);
    teardown();
}

static void test_reads_are_answered_at_once(void)
{
    setup();
    put(0, "/r", "1", CFS_ANY_VERSION);
    pump();
    cfs_writer w;
    cfs_writer_init(&w);
    cfs_write_u8(&w, CFS_OP_GET);
    cfs_write_u32(&w, 9);
    cfs_write_string(&w, "/r");
    sim[2].status = -1;
    cfs_node_request(&sim[2].node, w.p, w.len, 0, 1, clock_now);
    CHECK_EQ(sim[2].status, CFS_OK);
    CHECK_EQ(queued, 0); /* nothing sent to the others */
    cfs_writer_free(&w);
    teardown();
}

int main(void)
{
    test_changes_are_applied_alike_everywhere();
    test_no_change_without_quorum();
    test_a_change_in_flight_at_a_membership_change_is_applied_nowhere();
    test_the_locks_of_a_node_that_left_are_released();
    test_a_node_that_cannot_apply_a_change_leaves();
    test_losing_the_quorum_without_a_membership_change();
    test_reads_are_answered_at_once();
    return check_failures;
}
