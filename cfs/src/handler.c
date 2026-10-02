#include "handler.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "path.h"

void cfs_answer(cfs_writer *out, uint32_t id, int status, const cfs_writer *payload, const char *reason)
{
    size_t at = out->len;
    cfs_write_u32(out, 0); /* frame length, patched below */
    cfs_write_u32(out, id);
    cfs_write_u8(out, (uint8_t)status);
    if (status == CFS_OK)
        cfs_write_raw(out, payload->p, payload->len);
    else
        cfs_write_string(out, reason);
    cfs_patch_u32(out, at, (uint32_t)(out->len - at - 4));
}

bool cfs_op_changes(uint8_t op)
{
    switch (op) {
    case CFS_OP_PUT:
    case CFS_OP_DELETE:
    case CFS_OP_RENAME:
    case CFS_OP_LOCK:
    case CFS_OP_UNLOCK:
    case CFS_OP_NEXT_ID:
        return true;
    default:
        return false;
    }
}

static int check_path(const char *path, bool allow_root, uid_t uid, char *reason, size_t cap)
{
    if (!cfs_path_valid(path, allow_root)) {
        snprintf(reason, cap, "invalid path");
        return CFS_INVALID;
    }
    if (uid != 0 && cfs_path_is_private(path)) {
        snprintf(reason, cap, "%s is readable by root only", path);
        return CFS_FORBIDDEN;
    }
    return CFS_OK;
}

int cfs_check(const cfs_request *req, uid_t uid, char *reason, size_t cap)
{
    switch (req->op) {
    case CFS_OP_GET:
    case CFS_OP_PUT:
    case CFS_OP_DELETE:
        return check_path(req->path, false, uid, reason, cap);
    case CFS_OP_LIST:
        return check_path(req->path, true, uid, reason, cap);
    case CFS_OP_RENAME: {
        int rc = check_path(req->path, false, uid, reason, cap);
        return rc != CFS_OK ? rc : check_path(req->path2, false, uid, reason, cap);
    }
    case CFS_OP_LOCK:
    case CFS_OP_UNLOCK:
        if (!cfs_name_valid(req->name) || !cfs_name_valid(req->owner)) {
            snprintf(reason, cap, "invalid lock name or owner");
            return CFS_INVALID;
        }
        return CFS_OK;
    case CFS_OP_NEXT_ID:
    case CFS_OP_STATUS:
        return CFS_OK;
    default:
        snprintf(reason, cap, "unknown operation");
        return CFS_INVALID;
    }
}

/* Turn a store status into the reason sent to the client, when none was set yet. */
static int explain(cfs_store *store, int rc, char *reason, size_t cap)
{
    if (rc == CFS_INTERNAL) {
        /* The details go to the daemon's log, not to the client. */
        fprintf(stderr, "hyperlite-cfs: %s\n", cfs_store_error(store));
        snprintf(reason, cap, "internal error, see the hyperlite-cfs log");
    } else if (rc != CFS_OK && reason[0] == '\0') {
        const char *detail = cfs_store_error(store);
        if (rc == CFS_INVALID && detail[0])
            snprintf(reason, cap, "%s", detail);
        else
            snprintf(reason, cap, "%s", cfs_status_name(rc));
    }
    return rc;
}

typedef struct {
    cfs_writer *out;
    uint32_t count;
} list_ctx;

static int list_entry(void *p, const char *name, size_t name_len, bool is_dir, int64_t version, int64_t size)
{
    list_ctx *l = p;
    cfs_write_blob(l->out, name, name_len);
    cfs_write_u8(l->out, is_dir ? 1 : 0);
    cfs_write_i64(l->out, version);
    cfs_write_i64(l->out, size);
    l->count++;
    return l->out->err ? -1 : 0;
}

int cfs_read(cfs_ctx *ctx, const cfs_request *req, cfs_writer *body, char *reason, size_t cap)
{
    int rc = CFS_OK;
    int64_t version = 0, mtime = 0;
    cfs_store_clear_error(ctx->store);
    switch (req->op) {
    case CFS_OP_GET: {
        uint8_t *data;
        size_t len;
        rc = cfs_store_get(ctx->store, req->path, &version, &mtime, &data, &len);
        if (rc == CFS_OK) {
            cfs_write_i64(body, version);
            cfs_write_i64(body, mtime);
            cfs_write_blob(body, data, len);
            free(data);
        }
        break;
    }
    case CFS_OP_LIST: {
        size_t count_at = body->len;
        cfs_write_u32(body, 0);
        list_ctx l = {body, 0};
        rc = cfs_store_list(ctx->store, req->path, list_entry, &l);
        if (rc == CFS_OK) {
            cfs_patch_u32(body, count_at, l.count);
        } else if (body->err) {
            snprintf(reason, cap, "the directory listing is too large for one answer");
            rc = CFS_TOO_LARGE;
        }
        break;
    }
    case CFS_OP_STATUS: {
        uint8_t sum[CFS_CHECKSUM_LEN];
        int64_t entries = 0, bytes = 0;
        rc = cfs_store_status(ctx->store, &version, sum, &entries, &bytes);
        if (rc == CFS_OK) {
            cfs_write_i64(body, version);
            cfs_write_blob(body, sum, sizeof(sum));
            cfs_write_u8(body, ctx->quorate ? 1 : 0);
            cfs_write_u8(body, ctx->mode);
            cfs_write_i64(body, entries);
            cfs_write_i64(body, bytes);
        }
        break;
    }
    default:
        snprintf(reason, cap, "not a read");
        return CFS_INVALID;
    }
    return explain(ctx->store, rc, reason, cap);
}

int cfs_apply(cfs_store *store, const cfs_request *req, uint32_t node, int64_t now, cfs_writer *body, char *reason,
              size_t cap)
{
    int rc;
    int64_t version = 0;
    cfs_store_clear_error(store);
    switch (req->op) {
    case CFS_OP_PUT:
        rc = cfs_store_put(store, req->path, req->data, req->data_len, req->expected, now, &version);
        if (rc == CFS_OK)
            cfs_write_i64(body, version);
        break;
    case CFS_OP_DELETE:
        rc = cfs_store_delete(store, req->path, req->expected);
        break;
    case CFS_OP_RENAME:
        rc = cfs_store_rename(store, req->path, req->path2, req->expected, now, &version);
        if (rc == CFS_OK)
            cfs_write_i64(body, version);
        break;
    case CFS_OP_LOCK: {
        char holder[CFS_NAME_MAX + 1];
        rc = cfs_store_lock(store, req->name, req->owner, node, req->ttl, now, holder);
        if (rc == CFS_LOCKED)
            snprintf(reason, cap, "%s", holder);
        else if (rc == CFS_INVALID)
            snprintf(reason, cap, "the time to live must be 1 to %u seconds", CFS_LOCK_TTL_MAX);
        else if (rc == CFS_TOO_LARGE)
            snprintf(reason, cap, "too many locks held");
        break;
    }
    case CFS_OP_UNLOCK:
        rc = cfs_store_unlock(store, req->name, req->owner, now);
        if (rc == CFS_FORBIDDEN)
            snprintf(reason, cap, "the lock is held by another owner");
        break;
    case CFS_OP_NEXT_ID:
        rc = cfs_store_next_id(store, &version);
        if (rc == CFS_OK)
            cfs_write_i64(body, version);
        break;
    default:
        snprintf(reason, cap, "not a change");
        return CFS_INVALID;
    }
    return explain(store, rc, reason, cap);
}

void cfs_handle(cfs_ctx *ctx, const uint8_t *body, size_t len, uid_t peer_uid, int64_t now, cfs_writer *out)
{
    cfs_request req;
    char reason[512] = "";
    cfs_writer payload;
    cfs_writer_init(&payload);
    int rc = cfs_decode_request(body, len, &req);
    if (rc != CFS_OK)
        snprintf(reason, sizeof(reason), "malformed request");
    if (rc == CFS_OK)
        rc = cfs_check(&req, peer_uid, reason, sizeof(reason));
    if (rc == CFS_OK)
        rc = cfs_op_changes(req.op) ? cfs_apply(ctx->store, &req, 1, now, &payload, reason, sizeof(reason))
                                    : cfs_read(ctx, &req, &payload, reason, sizeof(reason));
    if (rc == CFS_OK && payload.err) {
        rc = CFS_INTERNAL;
        snprintf(reason, sizeof(reason), "the answer could not be built");
    }
    cfs_answer(out, req.id, rc, &payload, reason);
    cfs_writer_free(&payload);
}
