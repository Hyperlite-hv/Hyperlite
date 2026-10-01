/* The local socket protocol, little-endian.
 *
 * Frame:    u32 length of the body, then the body (at most CFS_FRAME_MAX bytes).
 * Request:  u8 op, u32 request id, then the arguments of the op.
 * Response: u32 request id, u8 status, then the payload when the status is CFS_OK, else a string (the reason).
 * Types:    string and blob = u32 length + bytes (strings carry no NUL byte); i64 = 8 bytes; u32; u8.
 *
 * Arguments and payloads per op:
 *   GET     path                         -> i64 version, i64 mtime, blob data
 *   PUT     path, i64 expected, blob     -> i64 version
 *   DELETE  path, i64 expected           -> (nothing)
 *   LIST    path of a directory          -> u32 count, then count x (string name, u8 is_dir, i64 version, i64 size)
 *   RENAME  path from, path to, i64 expected -> i64 version
 *   LOCK    string name, string owner, u32 ttl seconds -> (nothing); CFS_LOCKED carries the holder as reason
 *   UNLOCK  string name, string owner    -> (nothing)
 *   NEXT_ID                              -> i64 id
 *   STATUS                               -> i64 cluster version, blob checksum, u8 quorate, u8 mode (0 local),
 *                                           i64 entries, i64 bytes
 */

#ifndef CFS_PROTO_H
#define CFS_PROTO_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "cfs.h"

typedef struct {
    const uint8_t *p;
    size_t len;
    size_t off;
    bool err;
} cfs_reader;

typedef struct {
    uint8_t *p;
    size_t len;
    size_t cap;
    bool err; /* out of memory or over CFS_FRAME_MAX: the answer becomes CFS_INTERNAL */
} cfs_writer;

void cfs_reader_init(cfs_reader *r, const uint8_t *p, size_t len);
uint8_t cfs_read_u8(cfs_reader *r);
uint32_t cfs_read_u32(cfs_reader *r);
int64_t cfs_read_i64(cfs_reader *r);
/* A blob of at most `max` bytes; *out points into the reader's buffer. */
const uint8_t *cfs_read_blob(cfs_reader *r, uint32_t max, uint32_t *n);
/* A string of at most `max` bytes, copied NUL-terminated into `dst` (max + 1 bytes); refuses an embedded NUL. */
void cfs_read_string(cfs_reader *r, char *dst, size_t max);

void cfs_writer_init(cfs_writer *w);
void cfs_writer_free(cfs_writer *w);
void cfs_write_u8(cfs_writer *w, uint8_t v);
void cfs_write_u32(cfs_writer *w, uint32_t v);
void cfs_write_i64(cfs_writer *w, int64_t v);
void cfs_write_blob(cfs_writer *w, const void *p, size_t n);
void cfs_write_string(cfs_writer *w, const char *s);
/* Append bytes already encoded, without a length prefix. */
void cfs_write_raw(cfs_writer *w, const void *p, size_t n);
/* Overwrite 4 bytes already written at `at` (the frame length, once the body is known). */
void cfs_patch_u32(cfs_writer *w, size_t at, uint32_t v);

typedef struct {
    uint8_t op;
    uint32_t id;
    char path[CFS_PATH_MAX + 1];
    char path2[CFS_PATH_MAX + 1];
    char name[CFS_NAME_MAX + 1];
    char owner[CFS_NAME_MAX + 1];
    int64_t expected;
    uint32_t ttl;
    const uint8_t *data; /* points into the frame */
    uint32_t data_len;
} cfs_request;

/* Decode one request body. Returns CFS_OK, or CFS_INVALID for anything malformed: unknown op, truncated or extra
 * bytes, an over-long or NUL-carrying string. Path syntax is checked by the caller (path.h). `req->id` is set as soon
 * as it could be read, so a refusal can still name the request. */
int cfs_decode_request(const uint8_t *body, size_t len, cfs_request *req);

#endif
