#include "path.h"

#include <string.h>

#include "cfs.h"

static bool component_char(char c)
{
    return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') || c == '.' || c == '_' ||
           c == '-';
}

bool cfs_path_valid(const char *path, bool allow_root)
{
    size_t len = strnlen(path, CFS_PATH_MAX + 1);
    if (len == 0 || len > CFS_PATH_MAX || path[0] != '/')
        return false;
    if (len == 1)
        return allow_root;
    if (path[len - 1] == '/')
        return false;

    size_t depth = 0;
    const char *p = path + 1;
    while (*p) {
        const char *start = p;
        while (*p && *p != '/') {
            if (!component_char(*p))
                return false;
            p++;
        }
        size_t clen = (size_t)(p - start);
        if (clen == 0)
            return false; /* "//" */
        if ((clen == 1 && start[0] == '.') || (clen == 2 && start[0] == '.' && start[1] == '.'))
            return false;
        if (++depth > CFS_PATH_DEPTH_MAX)
            return false;
        if (*p == '/')
            p++;
    }
    return true;
}

bool cfs_path_is_private(const char *path)
{
    const char *p = path;
    while ((p = strstr(p, "/priv")) != NULL) {
        if (p[5] == '\0' || p[5] == '/')
            return true;
        p += 5;
    }
    return false;
}

bool cfs_name_valid(const char *name)
{
    size_t len = strnlen(name, CFS_NAME_MAX + 1);
    if (len == 0 || len > CFS_NAME_MAX)
        return false;
    for (size_t i = 0; i < len; i++) {
        char c = name[i];
        if (!(component_char(c) || c == ':' || c == '@'))
            return false;
    }
    return true;
}
