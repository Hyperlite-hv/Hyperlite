/* The tree as files (docs/design/cfs-files.md): a read-only FUSE mount served from the store, in the daemon's poll()
 * loop as one more source, so a read sees the tree as the last applied change left it.
 *
 * Paths are the tree's, unchanged. priv/ and nodes/<name>/priv/ are root's (0600, directories 0700), everything
 * else root's and the API group's (0640, directories 0750). Three virtual files at the root are made when read:
 * .version (the tree's version and checksum), .quorum (1 when this node may change the configuration) and .members
 * (this node's id, the members online and the cluster's configuration). `local` points at nodes/<this host>. Every
 * write is refused with EROFS (a read-only mount). */

#ifndef CFS_MOUNT_H
#define CFS_MOUNT_H

#include <sys/stat.h>
#include <sys/types.h>

#include "node.h"
#include "server.h"

typedef struct cfs_mount cfs_mount;

/* Mount at `mountpoint` (an empty directory). `group` owns what is not private. Returns NULL with `err` set. */
cfs_mount *cfs_mount_open(cfs_node *node, const char *mountpoint, gid_t group, char *err, size_t errlen);

/* The source to hand to cfs_serve: the session's descriptor and its handler. */
cfs_source cfs_mount_source(cfs_mount *m);

/* Unmount and free. */
void cfs_mount_close(cfs_mount *m);

/* The parts the C tests reach without a kernel. */
int cfs_mount_attr_of(cfs_mount *m, const char *path, struct stat *st);
int cfs_mount_virtual(cfs_mount *m, const char *name, char **text, size_t *len);
cfs_mount *cfs_mount_for_tests(cfs_node *node, gid_t group, const char *host);

#endif
