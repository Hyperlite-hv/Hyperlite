#include "proto.h"

#include <string.h>

#include "check.h"

static void put_request(cfs_writer *w, const char *path, int64_t expected, const char *data)
{
    cfs_writer_init(w);
    cfs_write_u8(w, CFS_OP_PUT);
    cfs_write_u32(w, 42);
    cfs_write_string(w, path);
    cfs_write_i64(w, expected);
    cfs_write_string(w, data);
}

int main(void)
{
    cfs_writer w;
    cfs_request req;

    put_request(&w, "/a/b", 7, "hello");
    CHECK_EQ(cfs_decode_request(w.p, w.len, &req), CFS_OK);
    CHECK_EQ(req.op, CFS_OP_PUT);
    CHECK_EQ(req.id, 42);
    CHECK(strcmp(req.path, "/a/b") == 0);
    CHECK_EQ(req.expected, 7);
    CHECK(req.data_len == 5 && memcmp(req.data, "hello", 5) == 0);

    /* Every truncation of a valid request is refused, never read past its end. */
    for (size_t cut = 0; cut < w.len; cut++)
        CHECK_EQ(cfs_decode_request(w.p, cut, &req), CFS_INVALID);
    /* Trailing bytes too. */
    cfs_write_u8(&w, 0);
    CHECK_EQ(cfs_decode_request(w.p, w.len, &req), CFS_INVALID);
    cfs_writer_free(&w);

    /* An expected version below -1 means nothing. */
    put_request(&w, "/a", -2, "x");
    CHECK_EQ(cfs_decode_request(w.p, w.len, &req), CFS_INVALID);
    cfs_writer_free(&w);

    /* A NUL inside a string is refused: it would cut the path the store sees. */
    cfs_writer_init(&w);
    cfs_write_u8(&w, CFS_OP_GET);
    cfs_write_u32(&w, 1);
    cfs_write_blob(&w, "/a\0/b", 5);
    CHECK_EQ(cfs_decode_request(w.p, w.len, &req), CFS_INVALID);
    cfs_writer_free(&w);

    /* A string longer than its maximum, and a length that runs past the buffer. */
    char longp[CFS_PATH_MAX + 2];
    memset(longp, 'a', sizeof(longp) - 1);
    longp[sizeof(longp) - 1] = '\0';
    cfs_writer_init(&w);
    cfs_write_u8(&w, CFS_OP_GET);
    cfs_write_u32(&w, 1);
    cfs_write_string(&w, longp);
    CHECK_EQ(cfs_decode_request(w.p, w.len, &req), CFS_INVALID);
    cfs_writer_free(&w);
    const uint8_t lying[] = {CFS_OP_GET, 1, 0, 0, 0, 0xff, 0xff, 0xff, 0x7f, '/'};
    CHECK_EQ(cfs_decode_request(lying, sizeof(lying), &req), CFS_INVALID);

    /* Unknown op, empty body. */
    const uint8_t unknown[] = {200, 1, 0, 0, 0};
    CHECK_EQ(cfs_decode_request(unknown, sizeof(unknown), &req), CFS_INVALID);
    CHECK_EQ(req.id, 1);
    CHECK_EQ(cfs_decode_request(unknown, 0, &req), CFS_INVALID);

    /* Little-endian round trip of the integer types. */
    cfs_writer_init(&w);
    cfs_write_u32(&w, 0x01020304u);
    cfs_write_i64(&w, -5);
    CHECK(w.p[0] == 4 && w.p[3] == 1);
    cfs_reader r;
    cfs_reader_init(&r, w.p, w.len);
    CHECK_EQ(cfs_read_u32(&r), 0x01020304u);
    CHECK_EQ(cfs_read_i64(&r), -5);
    CHECK(!r.err);
    cfs_read_u8(&r);
    CHECK(r.err);
    cfs_writer_free(&w);

    /* The writer refuses to grow past one frame. */
    cfs_writer_init(&w);
    static uint8_t chunk[CFS_FRAME_MAX];
    cfs_write_raw(&w, chunk, sizeof(chunk));
    CHECK(!w.err);
    cfs_write_raw(&w, chunk, 16);
    CHECK(w.err);
    cfs_writer_free(&w);
    return check_failures;
}
