/* Shared definitions of hyperlite-cfs: status codes, limits and operations.
 *
 * Design: docs/design/hyperlite-cfs.md. The limits follow Proxmox's pmxcfs where it documents one (the whole tree is
 * capped at 128 MiB); the others keep every request small enough to travel in one Corosync message later on. */

#ifndef CFS_H
#define CFS_H

#include <stddef.h>
#include <stdint.h>

/* Status codes on the wire. Never renumber: the Python client depends on them. */
enum cfs_status {
    CFS_OK = 0,
    CFS_NOT_FOUND = 1,
    CFS_CONFLICT = 2,      /* the expected version did not match */
    CFS_INVALID = 3,       /* malformed request, bad path, file where a directory is needed */
    CFS_TOO_LARGE = 4,     /* the file or the whole tree would exceed its limit */
    CFS_FORBIDDEN = 5,     /* a priv/ path for a client that is not root, a lock held by another owner */
    CFS_LOCKED = 6,        /* the lock is held by someone else */
    CFS_READ_ONLY = 7,     /* no quorum (cluster mode) */
    CFS_SYNCHRONISING = 8, /* membership change in progress (cluster mode): retry */
    CFS_INTERNAL = 9,      /* the database failed; the daemon logs why */
    CFS_UNCERTAIN = 10,    /* the membership changed before every member confirmed the change: it may or may not
                              have been applied; read the entry back before retrying (cluster mode) */
};

enum cfs_op {
    CFS_OP_GET = 1,
    CFS_OP_PUT = 2,
    CFS_OP_DELETE = 3,
    CFS_OP_LIST = 4,
    CFS_OP_RENAME = 5,
    CFS_OP_LOCK = 6,
    CFS_OP_UNLOCK = 7,
    CFS_OP_NEXT_ID = 8,
    CFS_OP_STATUS = 9,
};

/* Expected-version values with a special meaning; any positive value must equal the entry's version. */
#define CFS_ANY_VERSION ((int64_t)-1) /* write whatever is there */
#define CFS_MUST_NOT_EXIST ((int64_t)0) /* create only */

#define CFS_PATH_MAX 255                 /* bytes, the leading '/' included */
#define CFS_PATH_DEPTH_MAX 16            /* components */
#define CFS_NAME_MAX 128                 /* lock names and owners */
#define CFS_FILE_MAX (1024u * 1024u)     /* one entry */
#define CFS_TREE_MAX (128ull * 1024 * 1024) /* the whole tree, as pmxcfs */
#define CFS_FRAME_MAX (CFS_FILE_MAX + 4096u) /* one request or answer */
#define CFS_FIRST_ID 100                 /* guest ids start where Proxmox's do */
#define CFS_LOCK_TTL_MAX 3600u           /* seconds */
#define CFS_LOCKS_MAX 1024               /* locks held at once */
#define CFS_MEMBERS_MAX 64               /* nodes in one cluster (Proxmox reports clusters of over 50) */
#define CFS_CHECKSUM_LEN 32              /* SHA-256 */

const char *cfs_status_name(int status);

#endif
