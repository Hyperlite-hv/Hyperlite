#include "handler.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "path.h"

static void begin_answer(cfs_writer *out, size_t *frame_at, uint32_t id, uint8_t status)
{
    *frame_at = out->len;
    cfs_write_u32(out, 0); /* frame length, patched by end_answer */
    cfs_write_u32(out, id);
    cfs_write_u8(out, status);
}

static void end_answer(cfs_writer *out, size_t frame_at)
{
    cfs_patch_u32(out, frame_at, (uint32_t)(out->len - frame_at - 4));
}

static void answer_error(cfs_writer *out, uint32_t id, int status, const char *reason)
{
    size_t at;
    begin_answer(out, &at, id, (uint8_t)status);
    cfs_write_string(out, reason);
    end_answer(out, at);
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

/* Fills `reason` when the status is not CFS_OK; writes the payload into `body` when it is. */
static int apply(cfs_ctx *ctx, const cfs_request *req, uid_t uid, time_t now, cfs_writer *body, char *reason,
                 size_t cap)
{
    int rc = CFS_OK;
    int64_t version = 0, mtime = 0;
    cfs_store_clear_error(ctx->store);
    switch (req->op) {
    case CFS_OP_GET: {
        if ((rc = check_path(req->path, false, uid, reason, cap)) != CFS_OK)
            return rc;
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
    case CFS_OP_PUT:
        if ((rc = check_path(req->path, false, uid, reason, cap)) != CFS_OK)
            return rc;
        rc = cfs_store_put(ctx->store, req->path, req->data, req->data_len, req->expected, &version);
        if (rc == CFS_OK)
            cfs_write_i64(body, version);
        break;
    case CFS_OP_DELETE:
        if ((rc = check_path(req->path, false, uid, reason, cap)) != CFS_OK)
            return rc;
        rc = cfs_store_delete(ctx->store, req->path, req->expected);
        break;
    case CFS_OP_LIST: {
        if ((rc = check_path(req->path, true, uid, reason, cap)) != CFS_OK)
            return rc;
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
    case CFS_OP_RENAME:
        if ((rc = check_path(req->path, false, uid, reason, cap)) != CFS_OK ||
            (rc = check_path(req->path2, false, uid, reason, cap)) != CFS_OK)
            return rc;
        rc = cfs_store_rename(ctx->store, req->path, req->path2, req->expected, &version);
        if (rc == CFS_OK)
            cfs_write_i64(body, version);
        break;
    case CFS_OP_LOCK: {
        if (!cfs_name_valid(req->name) || !cfs_name_valid(req->owner)) {
            snprintf(reason, cap, "invalid lock name or owner");
            return CFS_INVALID;
        }
        char holder[CFS_NAME_MAX + 1];
        rc = cfs_lock_acquire(&ctx->locks, req->name, req->owner, req->ttl, now, holder);
        if (rc == CFS_LOCKED)
            snprintf(reason, cap, "%s", holder);
        else if (rc == CFS_INVALID)
            snprintf(reason, cap, "the time to live must be 1 to %u seconds", CFS_LOCK_TTL_MAX);
        else if (rc == CFS_TOO_LARGE)
            snprintf(reason, cap, "too many locks held");
        return rc;
    }
    case CFS_OP_UNLOCK:
        if (!cfs_name_valid(req->name) || !cfs_name_valid(req->owner)) {
            snprintf(reason, cap, "invalid lock name or owner");
            return CFS_INVALID;
        }
        rc = cfs_lock_release(&ctx->locks, req->name, req->owner, now);
        if (rc == CFS_FORBIDDEN)
            snprintf(reason, cap, "the lock is held by another owner");
        return rc;
    case CFS_OP_NEXT_ID:
        rc = cfs_store_next_id(ctx->store, &version);
        if (rc == CFS_OK)
            cfs_write_i64(body, version);
        break;
    case CFS_OP_STATUS: {
        uint8_t sum[CFS_CHECKSUM_LEN];
        int64_t entries = 0, bytes = 0;
        rc = cfs_store_status(ctx->store, &version, sum, &entries, &bytes);
        if (rc == CFS_OK) {
            cfs_write_i64(body, version);
            cfs_write_blob(body, sum, sizeof(sum));
            cfs_write_u8(body, 1); /* local mode is always quorate */
            cfs_write_u8(body, 0); /* mode: local */
            cfs_write_i64(body, entries);
            cfs_write_i64(body, bytes);
        }
        break;
    }
    default:
        snprintf(reason, cap, "unknown operation");
        return CFS_INVALID;
    }

    if (rc == CFS_INTERNAL) {
        /* The details go to the daemon's log, not to the client. */
        fprintf(stderr, "hyperlite-cfs: %s\n", cfs_store_error(ctx->store));
        snprintf(reason, cap, "internal error, see the hyperlite-cfs log");
    } else if (rc != CFS_OK && reason[0] == '\0') {
        const char *detail = cfs_store_error(ctx->store);
        if (rc == CFS_INVALID && detail[0])
            snprintf(reason, cap, "%s", detail);
        else
            snprintf(reason, cap, "%s", cfs_status_name(rc));
    }
    return rc;
}

void cfs_handle(cfs_ctx *ctx, const uint8_t *body, size_t len, uid_t peer_uid, time_t now, cfs_writer *out)
{
    cfs_request req;
    if (cfs_decode_request(body, len, &req) != CFS_OK) {
        answer_error(out, req.id, CFS_INVALID, "malformed request");
        return;
    }
    char reason[512] = "";
    cfs_writer payload;
    cfs_writer_init(&payload);
    int rc = apply(ctx, &req, peer_uid, now, &payload, reason, sizeof(reason));
    if (rc == CFS_OK && payload.err) {
        rc = CFS_INTERNAL;
        snprintf(reason, sizeof(reason), "the answer could not be built");
    }
    if (rc != CFS_OK) {
        answer_error(out, req.id, rc, reason);
    } else {
        size_t at;
        begin_answer(out, &at, req.id, CFS_OK);
        cfs_write_raw(out, payload.p, payload.len);
        end_answer(out, at);
    }
    cfs_writer_free(&payload);
}
