#include "proto.h"

#include <stdlib.h>
#include <string.h>

void cfs_reader_init(cfs_reader *r, const uint8_t *p, size_t len)
{
    r->p = p;
    r->len = len;
    r->off = 0;
    r->err = false;
}

static bool need(cfs_reader *r, size_t n)
{
    if (r->err || r->len - r->off < n) {
        r->err = true;
        return false;
    }
    return true;
}

uint8_t cfs_read_u8(cfs_reader *r)
{
    if (!need(r, 1))
        return 0;
    return r->p[r->off++];
}

uint32_t cfs_read_u32(cfs_reader *r)
{
    if (!need(r, 4))
        return 0;
    const uint8_t *b = r->p + r->off;
    r->off += 4;
    return (uint32_t)b[0] | (uint32_t)b[1] << 8 | (uint32_t)b[2] << 16 | (uint32_t)b[3] << 24;
}

int64_t cfs_read_i64(cfs_reader *r)
{
    if (!need(r, 8))
        return 0;
    const uint8_t *b = r->p + r->off;
    r->off += 8;
    uint64_t v = 0;
    for (int i = 7; i >= 0; i--)
        v = (v << 8) | b[i];
    return (int64_t)v;
}

const uint8_t *cfs_read_blob(cfs_reader *r, uint32_t max, uint32_t *n)
{
    *n = 0;
    uint32_t len = cfs_read_u32(r);
    if (r->err)
        return NULL;
    if (len > max) {
        r->err = true;
        return NULL;
    }
    if (!need(r, len))
        return NULL;
    const uint8_t *p = r->p + r->off;
    r->off += len;
    *n = len;
    return p;
}

void cfs_read_string(cfs_reader *r, char *dst, size_t max)
{
    uint32_t n;
    const uint8_t *p = cfs_read_blob(r, (uint32_t)max, &n);
    dst[0] = '\0';
    if (!p)
        return;
    if (memchr(p, '\0', n)) {
        r->err = true;
        return;
    }
    memcpy(dst, p, n);
    dst[n] = '\0';
}

void cfs_writer_init(cfs_writer *w)
{
    w->p = NULL;
    w->len = 0;
    w->cap = 0;
    w->err = false;
}

void cfs_writer_free(cfs_writer *w)
{
    free(w->p);
    cfs_writer_init(w);
}

static uint8_t *grow(cfs_writer *w, size_t n)
{
    if (w->err)
        return NULL;
    if (n > CFS_FRAME_MAX + 4 || w->len > CFS_FRAME_MAX + 4 - n) {
        w->err = true;
        return NULL;
    }
    if (w->len + n > w->cap) {
        size_t cap = w->cap ? w->cap : 256;
        while (cap < w->len + n)
            cap *= 2;
        uint8_t *p = realloc(w->p, cap);
        if (!p) {
            w->err = true;
            return NULL;
        }
        w->p = p;
        w->cap = cap;
    }
    uint8_t *at = w->p + w->len;
    w->len += n;
    return at;
}

void cfs_write_u8(cfs_writer *w, uint8_t v)
{
    uint8_t *b = grow(w, 1);
    if (b)
        b[0] = v;
}

static void put_u32(uint8_t *b, uint32_t v)
{
    b[0] = (uint8_t)v;
    b[1] = (uint8_t)(v >> 8);
    b[2] = (uint8_t)(v >> 16);
    b[3] = (uint8_t)(v >> 24);
}

void cfs_write_u32(cfs_writer *w, uint32_t v)
{
    uint8_t *b = grow(w, 4);
    if (b)
        put_u32(b, v);
}

void cfs_write_i64(cfs_writer *w, int64_t v)
{
    uint8_t *b = grow(w, 8);
    if (!b)
        return;
    uint64_t u = (uint64_t)v;
    for (int i = 0; i < 8; i++)
        b[i] = (uint8_t)(u >> (8 * i));
}

void cfs_write_blob(cfs_writer *w, const void *p, size_t n)
{
    if (n > UINT32_MAX) {
        w->err = true;
        return;
    }
    cfs_write_u32(w, (uint32_t)n);
    cfs_write_raw(w, p, n);
}

void cfs_write_raw(cfs_writer *w, const void *p, size_t n)
{
    if (n == 0)
        return;
    uint8_t *b = grow(w, n);
    if (b)
        memcpy(b, p, n);
}

void cfs_write_string(cfs_writer *w, const char *s)
{
    cfs_write_blob(w, s, strlen(s));
}

void cfs_patch_u32(cfs_writer *w, size_t at, uint32_t v)
{
    if (!w->err && at + 4 <= w->len)
        put_u32(w->p + at, v);
}

int cfs_decode_request(const uint8_t *body, size_t len, cfs_request *req)
{
    memset(req, 0, sizeof(*req));
    cfs_reader r;
    cfs_reader_init(&r, body, len);
    req->op = cfs_read_u8(&r);
    req->id = cfs_read_u32(&r);
    if (r.err)
        return CFS_INVALID;

    switch (req->op) {
    case CFS_OP_GET:
    case CFS_OP_LIST:
        cfs_read_string(&r, req->path, CFS_PATH_MAX);
        break;
    case CFS_OP_PUT:
        cfs_read_string(&r, req->path, CFS_PATH_MAX);
        req->expected = cfs_read_i64(&r);
        /* Bounded by the frame only: the store refuses a file over CFS_FILE_MAX with CFS_TOO_LARGE, a clearer
         * answer than a malformed request. */
        req->data = cfs_read_blob(&r, CFS_FRAME_MAX, &req->data_len);
        break;
    case CFS_OP_DELETE:
        cfs_read_string(&r, req->path, CFS_PATH_MAX);
        req->expected = cfs_read_i64(&r);
        break;
    case CFS_OP_RENAME:
        cfs_read_string(&r, req->path, CFS_PATH_MAX);
        cfs_read_string(&r, req->path2, CFS_PATH_MAX);
        req->expected = cfs_read_i64(&r);
        break;
    case CFS_OP_LOCK:
        cfs_read_string(&r, req->name, CFS_NAME_MAX);
        cfs_read_string(&r, req->owner, CFS_NAME_MAX);
        req->ttl = cfs_read_u32(&r);
        break;
    case CFS_OP_UNLOCK:
        cfs_read_string(&r, req->name, CFS_NAME_MAX);
        cfs_read_string(&r, req->owner, CFS_NAME_MAX);
        break;
    case CFS_OP_NEXT_ID:
    case CFS_OP_STATUS:
        break;
    default:
        return CFS_INVALID;
    }
    if (r.err || r.off != r.len)
        return CFS_INVALID;
    if (req->expected < CFS_ANY_VERSION)
        return CFS_INVALID;
    return CFS_OK;
}

const char *cfs_status_name(int status)
{
    static const char *const names[] = {"ok",        "not found", "conflict",  "invalid",        "too large",
                                        "forbidden", "locked",    "read-only", "synchronising", "internal error"};
    if (status < 0 || (size_t)status >= sizeof(names) / sizeof(names[0]))
        return "unknown";
    return names[status];
}
