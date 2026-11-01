/* The tree as files (mount.h): what each path shows (type, mode, owner, size) and the virtual files, without a
 * kernel. The mount itself is exercised by tests/python/test_mount.py. */

#include <errno.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>

#include "mount.h"
#include "node.h"
#include "store.h"

#include "check.h"

static int no_send(void *arg, const uint8_t *msg, size_t len)
{
    (void)arg;
    (void)msg;
    (void)len;
    return 0;
}

static void put(cfs_store *s, const char *path, const char *text)
{
    int64_t version = 0;
    CHECK_EQ(cfs_store_put(s, path, (const uint8_t *)text, strlen(text), CFS_ANY_VERSION, 1700000000, &version), CFS_OK);
}

static void attributes(void)
{
    char db[256], err[256];
    temp_db(db, sizeof(db));
    cfs_store *s = cfs_store_open(db, err, sizeof(err));
    CHECK(s != NULL);
    put(s, "/db/users/alice", "{\"role\": \"admin\"}");
    put(s, "/priv/cluster/authkey", "secret");
    put(s, "/nodes/pve-a/priv/key", "k");
    put(s, "/nodes/pve-a/hyperlite.crt", "crt");
    static cfs_node n;
    cfs_node_init(&n, s, CFS_MODE_LOCAL, 1, (cfs_transport){.send = no_send});
    cfs_mount *m = cfs_mount_for_tests(&n, 33, "pve-a");
    struct stat st;

    CHECK_EQ(cfs_mount_attr_of(m, "/", &st), 0);
    CHECK(S_ISDIR(st.st_mode) && (st.st_mode & 0777) == 0755);

    CHECK_EQ(cfs_mount_attr_of(m, "/db/users/alice", &st), 0);
    CHECK(S_ISREG(st.st_mode));
    CHECK_EQ(st.st_mode & 0777, 0640);
    CHECK_EQ(st.st_gid, 33);
    CHECK_EQ(st.st_size, (long long)strlen("{\"role\": \"admin\"}"));
    CHECK_EQ(st.st_mtime, 1700000000);

    CHECK_EQ(cfs_mount_attr_of(m, "/db/users", &st), 0);
    CHECK(S_ISDIR(st.st_mode) && (st.st_mode & 0777) == 0750 && st.st_gid == 33);

    /* The secrets: root's alone, the cluster's and each node's. */
    CHECK_EQ(cfs_mount_attr_of(m, "/priv/cluster/authkey", &st), 0);
    CHECK(S_ISREG(st.st_mode) && (st.st_mode & 0777) == 0600 && st.st_gid == 0);
    CHECK_EQ(cfs_mount_attr_of(m, "/priv", &st), 0);
    CHECK(S_ISDIR(st.st_mode) && (st.st_mode & 0777) == 0700 && st.st_gid == 0);
    CHECK_EQ(cfs_mount_attr_of(m, "/nodes/pve-a/priv/key", &st), 0);
    CHECK_EQ(st.st_mode & 0777, 0600);
    CHECK_EQ(cfs_mount_attr_of(m, "/nodes/pve-a/hyperlite.crt", &st), 0);
    CHECK_EQ(st.st_mode & 0777, 0640);

    CHECK_EQ(cfs_mount_attr_of(m, "/local", &st), 0);
    CHECK(S_ISLNK(st.st_mode));
    CHECK_EQ(st.st_size, (long long)strlen("nodes/pve-a"));

    CHECK_EQ(cfs_mount_attr_of(m, "/db/nope", &st), -ENOENT);
    CHECK_EQ(cfs_mount_attr_of(m, "/db/../priv", &st), -ENOENT);

    cfs_mount_close(m);
    cfs_node_free(&n);
    cfs_store_close(s);
    temp_db_remove(db);
}

static void virtual_files(void)
{
    char db[256], err[256];
    temp_db(db, sizeof(db));
    cfs_store *s = cfs_store_open(db, err, sizeof(err));
    put(s, "/cluster/corosync.json", "{\"cluster\": \"lab\"}");
    static cfs_node n;
    cfs_node_init(&n, s, CFS_MODE_LOCAL, 1, (cfs_transport){.send = no_send});
    cfs_node_quorum(&n, true);
    uint32_t self = 1;
    cfs_node_membership(&n, &self, 1);
    n.synced = true; /* what the daemon's local loop gives once this node's state came back to it */
    cfs_mount *m = cfs_mount_for_tests(&n, 33, "pve-a");
    char *text = NULL;
    size_t len = 0;

    CHECK_EQ(cfs_mount_virtual(m, ".quorum", &text, &len), 0);
    CHECK(strcmp(text, "1\n") == 0);
    free(text);

    CHECK_EQ(cfs_mount_virtual(m, ".version", &text, &len), 0);
    CHECK(strstr(text, "\"version\": ") != NULL && strstr(text, "\"checksum\": \"") != NULL);
    CHECK(strstr(text, "\"entries\": 1") != NULL);
    free(text);

    CHECK_EQ(cfs_mount_virtual(m, ".members", &text, &len), 0);
    CHECK(strstr(text, "\"self\": 1") != NULL && strstr(text, "\"online\": [1]") != NULL);
    CHECK(strstr(text, "\"configuration\": {\"cluster\": \"lab\"}") != NULL);
    free(text);

    struct stat st;
    CHECK_EQ(cfs_mount_attr_of(m, "/.quorum", &st), 0);
    CHECK(S_ISREG(st.st_mode) && (st.st_mode & 0777) == 0444 && st.st_size == 2);

    cfs_node_quorum(&n, false);
    CHECK_EQ(cfs_mount_virtual(m, ".quorum", &text, &len), 0);
    CHECK(strcmp(text, "0\n") == 0);
    free(text);
    cfs_node_quorum(&n, true);
    n.synced = false; /* synchronising: read-only too */
    CHECK_EQ(cfs_mount_virtual(m, ".quorum", &text, &len), 0);
    CHECK(strcmp(text, "0\n") == 0);
    free(text);

    cfs_mount_close(m);
    cfs_node_free(&n);
    cfs_store_close(s);
    temp_db_remove(db);
}

int main(void)
{
    attributes();
    virtual_files();
    return check_failures ? 1 : 0;
}
