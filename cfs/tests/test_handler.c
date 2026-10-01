#include "handler.h"

#include <stdlib.h>
#include <string.h>

#include "check.h"

static cfs_ctx ctx;

/* Send one request body through the handler and return the answer's status; the payload stays in `answer`. */
static int call(cfs_writer *req, uid_t uid, cfs_writer *answer, cfs_reader *payload)
{
    cfs_writer_init(answer);
    cfs_handle(&ctx, req->p, req->len, uid, 5000, answer);
    cfs_writer_free(req);
    cfs_reader_init(payload, answer->p, answer->len);
    uint32_t frame = cfs_read_u32(payload);
    CHECK_EQ(frame, answer->len - 4);
    cfs_read_u32(payload); /* request id */
    return cfs_read_u8(payload);
}

static void start(cfs_writer *w, uint8_t op)
{
    cfs_writer_init(w);
    cfs_write_u8(w, op);
    cfs_write_u32(w, 9);
}

static int put(const char *path, const char *data, uid_t uid)
{
    cfs_writer w, a;
    cfs_reader r;
    start(&w, CFS_OP_PUT);
    cfs_write_string(&w, path);
    cfs_write_i64(&w, CFS_ANY_VERSION);
    cfs_write_string(&w, data);
    int rc = call(&w, uid, &a, &r);
    cfs_writer_free(&a);
    return rc;
}

static int get(const char *path, uid_t uid, char *reason, size_t cap)
{
    cfs_writer w, a;
    cfs_reader r;
    start(&w, CFS_OP_GET);
    cfs_write_string(&w, path);
    int rc = call(&w, uid, &a, &r);
    if (rc != CFS_OK && reason)
        cfs_read_string(&r, reason, cap - 1);
    cfs_writer_free(&a);
    return rc;
}

int main(void)
{
    char db[256], err[256], reason[512];
    temp_db(db, sizeof(db));
    ctx.store = cfs_store_open(db, err, sizeof(err));
    if (!ctx.store) {
        fprintf(stderr, "%s\n", err);
        return 1;
    }
    ctx.quorate = true;

    CHECK_EQ(put("/cluster/settings.json", "{}", 1000), CFS_OK);
    CHECK_EQ(get("/cluster/settings.json", 1000, NULL, 0), CFS_OK);

    /* priv/ is root's only, for reads and writes alike. */
    CHECK_EQ(put("/priv/authkey", "secret", 1000), CFS_FORBIDDEN);
    CHECK_EQ(put("/priv/authkey", "secret", 0), CFS_OK);
    CHECK_EQ(get("/priv/authkey", 1000, reason, sizeof(reason)), CFS_FORBIDDEN);
    CHECK(strstr(reason, "root only") != NULL);
    CHECK_EQ(get("/priv/authkey", 0, NULL, 0), CFS_OK);
    CHECK_EQ(put("/nodes/pve1/priv/key", "k", 1000), CFS_FORBIDDEN);

    /* Paths are checked before the store sees them. */
    CHECK_EQ(put("/a/../priv/authkey", "x", 1000), CFS_INVALID);
    CHECK_EQ(get("relative", 0, reason, sizeof(reason)), CFS_INVALID);
    CHECK(strcmp(reason, "invalid path") == 0);

    /* A shape error carries the store's explanation. */
    CHECK_EQ(put("/cluster/settings.json/x", "x", 0), CFS_INVALID);

    /* A malformed body gets an answer, not a crash. */
    cfs_writer w, a;
    cfs_reader r;
    cfs_writer_init(&w);
    cfs_write_u8(&w, CFS_OP_PUT);
    CHECK_EQ(call(&w, 0, &a, &r), CFS_INVALID);
    cfs_writer_free(&a);

    /* LIST of the root, NEXT_ID and STATUS answer with their payloads. */
    start(&w, CFS_OP_LIST);
    cfs_write_string(&w, "/");
    CHECK_EQ(call(&w, 0, &a, &r), CFS_OK);
    CHECK_EQ(cfs_read_u32(&r), 2); /* cluster, priv: the refused write under nodes/ created nothing */
    cfs_writer_free(&a);

    start(&w, CFS_OP_NEXT_ID);
    CHECK_EQ(call(&w, 0, &a, &r), CFS_OK);
    CHECK_EQ(cfs_read_i64(&r), CFS_FIRST_ID);
    cfs_writer_free(&a);

    start(&w, CFS_OP_STATUS);
    CHECK_EQ(call(&w, 0, &a, &r), CFS_OK);
    int64_t version = cfs_read_i64(&r);
    uint32_t sum_len;
    cfs_read_blob(&r, 64, &sum_len);
    CHECK_EQ(sum_len, CFS_CHECKSUM_LEN);
    CHECK_EQ(cfs_read_u8(&r), 1); /* quorate */
    CHECK_EQ(cfs_read_u8(&r), 0); /* local mode */
    CHECK_EQ(cfs_read_i64(&r), 2); /* entries */
    CHECK_EQ(cfs_read_i64(&r), 8); /* bytes: "{}" and "secret" */
    CHECK(version >= 3);
    CHECK(!r.err && r.off == r.len);
    cfs_writer_free(&a);

    /* LOCK reports the holder. */
    start(&w, CFS_OP_LOCK);
    cfs_write_string(&w, "vm:100");
    cfs_write_string(&w, "migrate@pve1");
    cfs_write_u32(&w, 60);
    CHECK_EQ(call(&w, 0, &a, &r), CFS_OK);
    cfs_writer_free(&a);
    start(&w, CFS_OP_LOCK);
    cfs_write_string(&w, "vm:100");
    cfs_write_string(&w, "backup@pve2");
    cfs_write_u32(&w, 60);
    CHECK_EQ(call(&w, 0, &a, &r), CFS_LOCKED);
    cfs_read_string(&r, reason, sizeof(reason) - 1);
    CHECK(strcmp(reason, "migrate@pve1") == 0);
    cfs_writer_free(&a);

    cfs_store_close(ctx.store);
    temp_db_remove(db);
    return check_failures;
}
