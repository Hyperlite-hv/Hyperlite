#define FUSE_USE_VERSION 34

#include "mount.h"

#include <errno.h>
#include <fcntl.h>
#include <stdarg.h>
/* libfuse3's headers have stray semicolons that -Wpedantic refuses. */
#pragma GCC diagnostic push
#pragma GCC diagnostic ignored "-Wpedantic"
#include <fuse_lowlevel.h>
#pragma GCC diagnostic pop
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/statvfs.h>
#include <time.h>
#include <unistd.h>

#include "path.h"
#include "store.h"

#define VIRTUAL_VERSION ".version"
#define VIRTUAL_QUORUM ".quorum"
#define VIRTUAL_MEMBERS ".members"
#define LOCAL_LINK "local"
#define INODES_MAX 1048576

struct cfs_mount {
    cfs_node *node;
    gid_t group;
    char host[256];
    time_t started; /* the directories' and the link's date: the tree has none for them */
    struct fuse_session *se;
    struct fuse_buf buf;
    char *mountpoint;
    /* Inode numbers: 1 is the root, i + 2 is paths[i]. Paths are kept for the life of the mount (the kernel may ask
     * for an inode it looked up long ago); the tree is bounded, so is this table. */
    char **paths;
    size_t npaths, cap;
};

/* ---- Paths and inodes ---- */

static const char *path_of(const cfs_mount *m, fuse_ino_t ino)
{
    if (ino == FUSE_ROOT_ID)
        return "/";
    if (ino < 2 || ino - 2 >= m->npaths)
        return NULL;
    return m->paths[ino - 2];
}

static fuse_ino_t ino_of(cfs_mount *m, const char *path)
{
    if (strcmp(path, "/") == 0)
        return FUSE_ROOT_ID;
    for (size_t i = 0; i < m->npaths; i++)
        if (strcmp(m->paths[i], path) == 0)
            return i + 2;
    if (m->npaths >= INODES_MAX)
        return 0;
    if (m->npaths == m->cap) {
        size_t cap = m->cap ? m->cap * 2 : 256;
        char **p = realloc(m->paths, cap * sizeof(*p));
        if (!p)
            return 0;
        m->paths = p;
        m->cap = cap;
    }
    char *copy = strdup(path);
    if (!copy)
        return 0;
    m->paths[m->npaths++] = copy;
    return m->npaths + 1;
}

static int child_path(const char *dir, const char *name, char *out, size_t cap)
{
    if (!name[0] || strchr(name, '/') || strcmp(name, ".") == 0 || strcmp(name, "..") == 0)
        return -1;
    int n = snprintf(out, cap, "%s%s%s", dir, strcmp(dir, "/") == 0 ? "" : "/", name);
    return n < 0 || (size_t)n >= cap ? -1 : 0;
}

static bool is_virtual(const char *path)
{
    return strcmp(path, "/" VIRTUAL_VERSION) == 0 || strcmp(path, "/" VIRTUAL_QUORUM) == 0 ||
           strcmp(path, "/" VIRTUAL_MEMBERS) == 0;
}

/* ---- Virtual files ---- */

static int printf_alloc(char **text, size_t *len, const char *fmt, ...) __attribute__((format(printf, 3, 4)));

static int printf_alloc(char **text, size_t *len, const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    int n = vasprintf(text, fmt, ap);
    va_end(ap);
    if (n < 0)
        return -ENOMEM;
    *len = (size_t)n;
    return 0;
}

int cfs_mount_virtual(cfs_mount *m, const char *name, char **text, size_t *len)
{
    cfs_store *store = m->node->ctx.store;
    if (strcmp(name, VIRTUAL_QUORUM) == 0)
        return printf_alloc(text, len, "%d\n", m->node->quorate && m->node->synced ? 1 : 0);
    if (strcmp(name, VIRTUAL_VERSION) == 0) {
        int64_t version = 0, entries = 0, bytes = 0;
        uint8_t sum[CFS_CHECKSUM_LEN];
        if (cfs_store_status(store, &version, sum, &entries, &bytes) != CFS_OK)
            return -EIO;
        char hex[2 * CFS_CHECKSUM_LEN + 1];
        for (size_t i = 0; i < CFS_CHECKSUM_LEN; i++)
            snprintf(hex + 2 * i, 3, "%02x", sum[i]);
        return printf_alloc(text, len, "{\"version\": %lld, \"checksum\": \"%s\", \"entries\": %lld, \"bytes\": %lld}\n",
                            (long long)version, hex, (long long)entries, (long long)bytes);
    }
    if (strcmp(name, VIRTUAL_MEMBERS) == 0) {
        /* The configuration as the tree holds it (names, addresses), and the members this node sees online. */
        int64_t version = 0, mtime = 0;
        uint8_t *conf = NULL;
        size_t conf_len = 0;
        int rc = cfs_store_get(store, "/cluster/corosync.json", &version, &mtime, &conf, &conf_len);
        char online[CFS_MEMBERS_MAX * 12 + 2] = "";
        size_t used = 0;
        for (size_t i = 0; i < m->node->nmembers && used < sizeof(online) - 12; i++)
            used += (size_t)snprintf(online + used, sizeof(online) - used, "%s%u", i ? ", " : "", m->node->members[i]);
        rc = printf_alloc(text, len, "{\"self\": %u, \"online\": [%s], \"quorate\": %s, \"configuration\": %.*s}\n",
                          m->node->self, online, m->node->quorate ? "true" : "false",
                          rc == CFS_OK && conf_len ? (int)conf_len : 4, rc == CFS_OK && conf_len ? (char *)conf : "null");
        free(conf);
        return rc;
    }
    return -ENOENT;
}

/* ---- Attributes ---- */

typedef struct {
    const char *want;
    bool found, is_dir;
    int64_t size;
} find_child;

static int find_cb(void *ctx, const char *name, size_t name_len, bool is_dir, int64_t version, int64_t size)
{
    (void)version;
    find_child *f = ctx;
    if (strlen(f->want) == name_len && memcmp(f->want, name, name_len) == 0) {
        f->found = true;
        f->is_dir = is_dir;
        f->size = size;
        return 1;
    }
    return 0;
}

static void fill(const cfs_mount *m, struct stat *st, const char *path, mode_t type, off_t size, time_t mtime)
{
    bool priv = cfs_path_is_private(path);
    memset(st, 0, sizeof(*st));
    st->st_uid = 0;
    st->st_gid = priv ? 0 : m->group;
    st->st_nlink = type == S_IFDIR ? 2 : 1;
    st->st_size = size;
    st->st_mtime = st->st_ctime = st->st_atime = mtime ? mtime : m->started;
    if (type == S_IFDIR)
        st->st_mode = S_IFDIR | (priv ? 0700 : 0750);
    else if (type == S_IFLNK)
        st->st_mode = S_IFLNK | 0777;
    else
        st->st_mode = S_IFREG | (priv ? 0600 : 0640);
}

int cfs_mount_attr_of(cfs_mount *m, const char *path, struct stat *st)
{
    if (strcmp(path, "/") == 0) {
        fill(m, st, path, S_IFDIR, 0, 0);
        st->st_mode = S_IFDIR | 0755; /* every account may list the root */
        return 0;
    }
    if (strcmp(path, "/" LOCAL_LINK) == 0) {
        fill(m, st, path, S_IFLNK, (off_t)(strlen("nodes/") + strlen(m->host)), 0);
        return 0;
    }
    if (is_virtual(path)) {
        char *text = NULL;
        size_t len = 0;
        int rc = cfs_mount_virtual(m, path + 1, &text, &len);
        free(text);
        if (rc != 0)
            return rc;
        fill(m, st, path, S_IFREG, (off_t)len, time(NULL));
        st->st_mode = S_IFREG | 0444;
        return 0;
    }
    if (!cfs_path_valid(path, false))
        return -ENOENT;
    char parent[CFS_PATH_MAX + 1], name[CFS_PATH_MAX + 1];
    snprintf(parent, sizeof(parent), "%s", path);
    char *slash = strrchr(parent, '/');
    snprintf(name, sizeof(name), "%s", slash + 1); /* before the parent is cut, which may overwrite it */
    find_child f = {.want = name};
    if (slash == parent)
        parent[1] = '\0';
    else
        *slash = '\0';
    if (cfs_store_list(m->node->ctx.store, parent, find_cb, &f) < 0 || !f.found)
        return -ENOENT;
    if (f.is_dir) {
        fill(m, st, path, S_IFDIR, 0, 0);
        return 0;
    }
    int64_t version = 0, mtime = 0;
    uint8_t *data = NULL;
    size_t len = 0;
    if (cfs_store_get(m->node->ctx.store, path, &version, &mtime, &data, &len) != CFS_OK)
        return -ENOENT;
    free(data);
    fill(m, st, path, S_IFREG, (off_t)len, (time_t)mtime);
    return 0;
}

/* ---- FUSE operations ---- */

static void reply_entry(fuse_req_t req, cfs_mount *m, const char *path)
{
    struct fuse_entry_param e;
    memset(&e, 0, sizeof(e));
    int rc = cfs_mount_attr_of(m, path, &e.attr);
    if (rc != 0) {
        fuse_reply_err(req, -rc);
        return;
    }
    e.ino = ino_of(m, path);
    if (!e.ino) {
        fuse_reply_err(req, ENOMEM);
        return;
    }
    e.attr.st_ino = e.ino;
    /* Nothing cached: a change applied on any node shows at once. */
    e.attr_timeout = 0;
    e.entry_timeout = 0;
    fuse_reply_entry(req, &e);
}

static void op_lookup(fuse_req_t req, fuse_ino_t parent, const char *name)
{
    cfs_mount *m = fuse_req_userdata(req);
    const char *dir = path_of(m, parent);
    char path[CFS_PATH_MAX + 1];
    if (!dir || child_path(dir, name, path, sizeof(path)) != 0) {
        fuse_reply_err(req, ENOENT);
        return;
    }
    reply_entry(req, m, path);
}

static void op_getattr(fuse_req_t req, fuse_ino_t ino, struct fuse_file_info *fi)
{
    (void)fi;
    cfs_mount *m = fuse_req_userdata(req);
    const char *path = path_of(m, ino);
    struct stat st;
    int rc = path ? cfs_mount_attr_of(m, path, &st) : -ENOENT;
    if (rc != 0) {
        fuse_reply_err(req, -rc);
        return;
    }
    st.st_ino = ino;
    fuse_reply_attr(req, &st, 0);
}

static void op_readlink(fuse_req_t req, fuse_ino_t ino)
{
    cfs_mount *m = fuse_req_userdata(req);
    const char *path = path_of(m, ino);
    if (!path || strcmp(path, "/" LOCAL_LINK) != 0) {
        fuse_reply_err(req, EINVAL);
        return;
    }
    char target[300];
    snprintf(target, sizeof(target), "nodes/%s", m->host);
    fuse_reply_readlink(req, target);
}

typedef struct {
    fuse_req_t req;
    cfs_mount *m;
    const char *dir;
    char *buf;
    size_t len, cap;
    bool failed;
} listing;

static void add_entry(listing *l, const char *name, mode_t type)
{
    struct stat st;
    memset(&st, 0, sizeof(st));
    st.st_mode = type;
    size_t need = fuse_add_direntry(l->req, NULL, 0, name, NULL, 0);
    if (l->len + need > l->cap) {
        size_t cap = (l->cap ? l->cap * 2 : 4096) + need;
        char *b = realloc(l->buf, cap);
        if (!b) {
            l->failed = true;
            return;
        }
        l->buf = b;
        l->cap = cap;
    }
    char path[CFS_PATH_MAX + 1];
    st.st_ino = strcmp(name, ".") == 0 ? ino_of(l->m, l->dir)
                : strcmp(name, "..") == 0 || child_path(l->dir, name, path, sizeof(path)) != 0
                    ? FUSE_ROOT_ID
                    : ino_of(l->m, path);
    fuse_add_direntry(l->req, l->buf + l->len, l->cap - l->len, name, &st, (off_t)(l->len + need));
    l->len += need;
}

static int list_cb(void *ctx, const char *name, size_t name_len, bool is_dir, int64_t version, int64_t size)
{
    (void)version;
    (void)size;
    listing *l = ctx;
    char copy[CFS_PATH_MAX + 1];
    if (name_len >= sizeof(copy))
        return 0;
    memcpy(copy, name, name_len);
    copy[name_len] = '\0';
    add_entry(l, copy, is_dir ? S_IFDIR : S_IFREG);
    return l->failed ? 1 : 0;
}

static void op_readdir(fuse_req_t req, fuse_ino_t ino, size_t size, off_t off, struct fuse_file_info *fi)
{
    (void)fi;
    cfs_mount *m = fuse_req_userdata(req);
    const char *dir = path_of(m, ino);
    struct stat st;
    if (!dir || cfs_mount_attr_of(m, dir, &st) != 0 || !S_ISDIR(st.st_mode)) {
        fuse_reply_err(req, dir ? ENOTDIR : ENOENT);
        return;
    }
    listing l = {.req = req, .m = m, .dir = dir};
    add_entry(&l, ".", S_IFDIR);
    add_entry(&l, "..", S_IFDIR);
    if (strcmp(dir, "/") == 0) {
        add_entry(&l, LOCAL_LINK, S_IFLNK);
        add_entry(&l, VIRTUAL_MEMBERS, S_IFREG);
        add_entry(&l, VIRTUAL_QUORUM, S_IFREG);
        add_entry(&l, VIRTUAL_VERSION, S_IFREG);
    }
    cfs_store_list(m->node->ctx.store, dir, list_cb, &l);
    if (l.failed) {
        free(l.buf);
        fuse_reply_err(req, ENOMEM);
        return;
    }
    if ((size_t)off >= l.len)
        fuse_reply_buf(req, NULL, 0);
    else
        fuse_reply_buf(req, l.buf + off, l.len - (size_t)off < size ? l.len - (size_t)off : size);
    free(l.buf);
}

static void op_open(fuse_req_t req, fuse_ino_t ino, struct fuse_file_info *fi)
{
    cfs_mount *m = fuse_req_userdata(req);
    const char *path = path_of(m, ino);
    struct stat st;
    if (!path || cfs_mount_attr_of(m, path, &st) != 0) {
        fuse_reply_err(req, ENOENT);
        return;
    }
    if ((fi->flags & O_ACCMODE) != O_RDONLY) {
        fuse_reply_err(req, EROFS);
        return;
    }
    fi->direct_io = 1; /* sizes of virtual files change between reads */
    fuse_reply_open(req, fi);
}

static void op_read(fuse_req_t req, fuse_ino_t ino, size_t size, off_t off, struct fuse_file_info *fi)
{
    (void)fi;
    cfs_mount *m = fuse_req_userdata(req);
    const char *path = path_of(m, ino);
    uint8_t *data = NULL;
    char *text = NULL;
    size_t len = 0;
    int rc;
    if (!path) {
        fuse_reply_err(req, ENOENT);
        return;
    }
    if (is_virtual(path)) {
        rc = cfs_mount_virtual(m, path + 1, &text, &len);
        data = (uint8_t *)text;
    } else {
        int64_t version = 0, mtime = 0;
        rc = cfs_store_get(m->node->ctx.store, path, &version, &mtime, &data, &len) == CFS_OK ? 0 : -ENOENT;
    }
    if (rc != 0) {
        free(data);
        fuse_reply_err(req, -rc);
        return;
    }
    if ((size_t)off >= len)
        fuse_reply_buf(req, NULL, 0);
    else
        fuse_reply_buf(req, (const char *)data + off, len - (size_t)off < size ? len - (size_t)off : size);
    free(data);
}

static void op_statfs(fuse_req_t req, fuse_ino_t ino)
{
    (void)ino;
    struct statvfs st;
    memset(&st, 0, sizeof(st));
    st.f_bsize = 4096;
    st.f_namemax = 255;
    st.f_flag = ST_RDONLY;
    fuse_reply_statfs(req, &st);
}

static const struct fuse_lowlevel_ops ops = {
    .lookup = op_lookup,
    .getattr = op_getattr,
    .readlink = op_readlink,
    .readdir = op_readdir,
    .open = op_open,
    .opendir = NULL,
    .read = op_read,
    .statfs = op_statfs,
};

/* ---- The session ---- */

static int ready(void *arg)
{
    cfs_mount *m = arg;
    int n = fuse_session_receive_buf(m->se, &m->buf);
    if (n == -EINTR || n == -EAGAIN)
        return 0;
    if (n <= 0 || fuse_session_exited(m->se)) {
        /* Unmounted from outside: the daemon stops and systemd starts it again, which mounts again. */
        fprintf(stderr, "hyperlite-cfs: the mount of %s went away\n", m->mountpoint);
        return -1;
    }
    fuse_session_process_buf(m->se, &m->buf);
    return 0;
}

static cfs_mount *alloc_mount(cfs_node *node, gid_t group, const char *host)
{
    cfs_mount *m = calloc(1, sizeof(*m));
    if (!m)
        return NULL;
    m->node = node;
    m->group = group;
    snprintf(m->host, sizeof(m->host), "%s", host);
    m->started = time(NULL);
    return m;
}

cfs_mount *cfs_mount_for_tests(cfs_node *node, gid_t group, const char *host)
{
    return alloc_mount(node, group, host);
}

cfs_mount *cfs_mount_open(cfs_node *node, const char *mountpoint, gid_t group, char *err, size_t errlen)
{
    char host[256] = "";
    if (gethostname(host, sizeof(host) - 1) != 0)
        snprintf(host, sizeof(host), "localhost");
    char *dot = strchr(host, '.');
    if (dot)
        *dot = '\0';
    cfs_mount *m = alloc_mount(node, group, host);
    if (!m) {
        snprintf(err, errlen, "out of memory");
        return NULL;
    }
    m->mountpoint = strdup(mountpoint);
    char *argv[] = {"hyperlite-cfs", "-o", "ro,allow_other,default_permissions,fsname=hyperlite-cfs,subtype=hyperlite-cfs,nosuid,nodev",
                    NULL};
    struct fuse_args args = FUSE_ARGS_INIT(3, argv);
    m->se = fuse_session_new(&args, &ops, sizeof(ops), m);
    fuse_opt_free_args(&args);
    if (!m->se) {
        snprintf(err, errlen, "cannot start the FUSE session");
        cfs_mount_close(m);
        return NULL;
    }
    if (fuse_session_mount(m->se, mountpoint) != 0) {
        snprintf(err, errlen, "cannot mount %s (is it an empty directory, /dev/fuse there?)", mountpoint);
        fuse_session_destroy(m->se);
        m->se = NULL;
        cfs_mount_close(m);
        return NULL;
    }
    return m;
}

cfs_source cfs_mount_source(cfs_mount *m)
{
    return (cfs_source){.fd = fuse_session_fd(m->se), .ready = ready, .arg = m};
}

void cfs_mount_close(cfs_mount *m)
{
    if (!m)
        return;
    if (m->se) {
        fuse_session_unmount(m->se);
        fuse_session_destroy(m->se);
    }
    free(m->buf.mem);
    for (size_t i = 0; i < m->npaths; i++)
        free(m->paths[i]);
    free(m->paths);
    free(m->mountpoint);
    free(m);
}
