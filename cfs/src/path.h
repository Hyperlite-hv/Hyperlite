/* Path rules of the tree. Every path comes from a client and ends up as a database key, so it is checked strictly:
 * absolute, components of [A-Za-z0-9._-] that are not "." or "..", no empty component, bounded length and depth. */

#ifndef CFS_PATH_H
#define CFS_PATH_H

#include <stdbool.h>
#include <stddef.h>

/* True when `path` (NUL-terminated) is a valid entry path. "/" alone is a valid directory but not an entry. */
bool cfs_path_valid(const char *path, bool allow_root);

/* True when one component of a valid path is "priv": such entries are readable and writable by root only, like
 * /etc/pve/priv/ and /etc/pve/nodes/<name>/priv/ on Proxmox. */
bool cfs_path_is_private(const char *path);

/* True for lock names and owners: 1 to CFS_NAME_MAX bytes of [A-Za-z0-9._:@-]. */
bool cfs_name_valid(const char *name);

#endif
