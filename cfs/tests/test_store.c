#include <stdint.h>
#include "store.h"

#include <stdlib.h>
#include <string.h>

#include "check.h"

static cfs_store *open_store(const char *path)
{
    char err[256];
    cfs_store *s = cfs_store_open(path, err, sizeof(err));
    if (!s) {
        fprintf(stderr, "open: %s\n", err);
        exit(1);
    }
    return s;
}

static int put(cfs_store *s, const char *path, const char *text, int64_t expected, int64_t *version)
{
    int64_t v = 0;
    int rc = cfs_store_put(s, path, (const uint8_t *)text, strlen(text), expected, 1000, &v);
    if (version)
        *version = v;
    return rc;
}

typedef struct {
    char names[16][64];
    int dirs[16];
    int n;
} listing;

static int collect(void *p, const char *name, size_t len, bool is_dir, int64_t version, int64_t size)
{
    (void)version;
    (void)size;
    listing *l = p;
    if (l->n == 16 || len >= 64)
        return 1;
    memcpy(l->names[l->n], name, len);
    l->names[l->n][len] = '\0';
    l->dirs[l->n] = is_dir;
    l->n++;
    return 0;
}

static void test_versions_and_compare_and_set(cfs_store *s)
{
    int64_t v1, v2, v3;
    CHECK_EQ(put(s, "/cluster/a.json", "{}", CFS_MUST_NOT_EXIST, &v1), CFS_OK);
    CHECK_EQ(v1, 1);
    CHECK_EQ(put(s, "/cluster/a.json", "{}", CFS_MUST_NOT_EXIST, NULL), CFS_CONFLICT); /* create only */
    CHECK_EQ(put(s, "/cluster/a.json", "{\"x\":1}", v1, &v2), CFS_OK);
    CHECK_EQ(v2, 2);
    CHECK_EQ(put(s, "/cluster/a.json", "stale", v1, NULL), CFS_CONFLICT); /* someone else wrote in between */
    CHECK_EQ(put(s, "/cluster/missing.json", "x", 7, NULL), CFS_NOT_FOUND);
    CHECK_EQ(put(s, "/cluster/b.json", "b", CFS_ANY_VERSION, &v3), CFS_OK);
    CHECK_EQ(v3, 3);

    int64_t version, mtime;
    uint8_t *data;
    size_t len;
    CHECK_EQ(cfs_store_get(s, "/cluster/a.json", &version, &mtime, &data, &len), CFS_OK);
    CHECK_EQ(version, 2);
    CHECK(len == 7 && memcmp(data, "{\"x\":1}", 7) == 0);
    CHECK_EQ(mtime, 1000); /* the time the change carried, not this machine's clock */
    free(data);
    CHECK_EQ(cfs_store_get(s, "/nope", &version, &mtime, &data, &len), CFS_NOT_FOUND);

    /* An empty file is a real entry. */
    CHECK_EQ(put(s, "/cluster/empty", "", CFS_ANY_VERSION, NULL), CFS_OK);
    CHECK_EQ(cfs_store_get(s, "/cluster/empty", &version, &mtime, &data, &len), CFS_OK);
    CHECK(len == 0 && data == NULL);

    CHECK_EQ(cfs_store_delete(s, "/cluster/b.json", 1), CFS_CONFLICT);
    CHECK_EQ(cfs_store_delete(s, "/cluster/b.json", v3), CFS_OK);
    CHECK_EQ(cfs_store_delete(s, "/cluster/b.json", CFS_ANY_VERSION), CFS_NOT_FOUND);
    CHECK_EQ(cfs_store_delete(s, "/cluster/empty", CFS_ANY_VERSION), CFS_OK);
}

static void test_files_and_directories_do_not_overlap(cfs_store *s)
{
    CHECK_EQ(put(s, "/nodes/pve1/qemu/100.json", "{}", CFS_ANY_VERSION, NULL), CFS_OK);
    CHECK_EQ(put(s, "/nodes/pve1", "file over a directory", CFS_ANY_VERSION, NULL), CFS_INVALID);
    CHECK_EQ(put(s, "/nodes/pve1/qemu/100.json/x", "under a file", CFS_ANY_VERSION, NULL), CFS_INVALID);
    CHECK(strstr(cfs_store_error(s), "is a file") != NULL);

    listing l = {0};
    CHECK_EQ(put(s, "/nodes/pve1/qemu/101.json", "{}", CFS_ANY_VERSION, NULL), CFS_OK);
    CHECK_EQ(put(s, "/nodes/pve1/lxc/200.json", "{}", CFS_ANY_VERSION, NULL), CFS_OK);
    CHECK_EQ(put(s, "/nodes/pve1/config", "{}", CFS_ANY_VERSION, NULL), CFS_OK);
    CHECK_EQ(put(s, "/nodes/pve1.old", "x", CFS_ANY_VERSION, NULL), CFS_OK); /* sorts between "pve1" and "pve1/" */
    CHECK_EQ(cfs_store_list(s, "/nodes/pve1", collect, &l), CFS_OK);
    CHECK_EQ(l.n, 3);
    CHECK(strcmp(l.names[0], "config") == 0 && !l.dirs[0]);
    CHECK(strcmp(l.names[1], "lxc") == 0 && l.dirs[1]);
    CHECK(strcmp(l.names[2], "qemu") == 0 && l.dirs[2]);

    listing root = {0};
    CHECK_EQ(cfs_store_list(s, "/", collect, &root), CFS_OK);
    CHECK_EQ(root.n, 2);
    CHECK(strcmp(root.names[0], "cluster") == 0 && strcmp(root.names[1], "nodes") == 0);

    listing none = {0};
    CHECK_EQ(cfs_store_list(s, "/nowhere", collect, &none), CFS_NOT_FOUND);
    CHECK_EQ(cfs_store_list(s, "/nodes/pve1/config", collect, &none), CFS_INVALID);
}

static void test_rename(cfs_store *s)
{
    int64_t v, nv, version, mtime;
    uint8_t *data;
    size_t len;
    CHECK_EQ(put(s, "/r/one", "payload", CFS_ANY_VERSION, &v), CFS_OK);
    CHECK_EQ(put(s, "/r/two", "taken", CFS_ANY_VERSION, NULL), CFS_OK);
    CHECK_EQ(cfs_store_rename(s, "/r/one", "/r/two", CFS_ANY_VERSION, 1000, &nv), CFS_CONFLICT);
    CHECK_EQ(cfs_store_rename(s, "/r/one", "/r/three", v + 100, 1000, &nv), CFS_CONFLICT);
    CHECK_EQ(cfs_store_rename(s, "/r/one", "/r/one", CFS_ANY_VERSION, 1000, &nv), CFS_INVALID);
    CHECK_EQ(cfs_store_rename(s, "/r/one", "/r/two/below", CFS_ANY_VERSION, 1000, &nv), CFS_INVALID);
    /* A refused rename leaves the source in place (the transaction rolled back). */
    CHECK_EQ(cfs_store_get(s, "/r/one", &version, &mtime, &data, &len), CFS_OK);
    free(data);
    CHECK_EQ(cfs_store_rename(s, "/r/one", "/r/moved/one", v, 1000, &nv), CFS_OK);
    CHECK(nv > v);
    CHECK_EQ(cfs_store_get(s, "/r/one", &version, &mtime, &data, &len), CFS_NOT_FOUND);
    CHECK_EQ(cfs_store_get(s, "/r/moved/one", &version, &mtime, &data, &len), CFS_OK);
    CHECK(len == 7 && memcmp(data, "payload", 7) == 0 && version == nv);
    free(data);
    CHECK_EQ(cfs_store_rename(s, "/r/ghost", "/r/x", CFS_ANY_VERSION, 1000, &nv), CFS_NOT_FOUND);
}

static void test_limits(cfs_store *s)
{
    size_t big = CFS_FILE_MAX + 1;
    uint8_t *buf = calloc(1, big);
    int64_t v;
    CHECK_EQ(cfs_store_put(s, "/big", buf, big, CFS_ANY_VERSION, 1000, &v), CFS_TOO_LARGE);
    CHECK_EQ(cfs_store_put(s, "/big", buf, CFS_FILE_MAX, CFS_ANY_VERSION, 1000, &v), CFS_OK);
    CHECK_EQ(cfs_store_delete(s, "/big", CFS_ANY_VERSION), CFS_OK);
    free(buf);
}

static void test_ids_status_and_persistence(const char *db)
{
    cfs_store *s = open_store(db);
    int64_t id1, id2, version, entries, bytes, v;
    uint8_t sum1[CFS_CHECKSUM_LEN], sum2[CFS_CHECKSUM_LEN], sum3[CFS_CHECKSUM_LEN];
    CHECK_EQ(cfs_store_next_id(s, &id1), CFS_OK);
    CHECK_EQ(cfs_store_next_id(s, &id2), CFS_OK);
    CHECK_EQ(id1, CFS_FIRST_ID);
    CHECK_EQ(id2, CFS_FIRST_ID + 1);
    CHECK_EQ(put(s, "/p/x", "x", CFS_ANY_VERSION, NULL), CFS_OK);
    CHECK_EQ(cfs_store_status(s, &version, sum1, &entries, &bytes), CFS_OK);
    CHECK_EQ(entries, 1);
    CHECK_EQ(bytes, 1);
    int64_t before = version;
    cfs_store_close(s);

    /* Reopened: the counters and the tree are the same, so is the checksum. */
    s = open_store(db);
    CHECK_EQ(cfs_store_status(s, &version, sum2, &entries, &bytes), CFS_OK);
    CHECK_EQ(version, before);
    CHECK(memcmp(sum1, sum2, CFS_CHECKSUM_LEN) == 0);
    CHECK_EQ(cfs_store_next_id(s, &id1), CFS_OK);
    CHECK_EQ(id1, CFS_FIRST_ID + 2);
    CHECK_EQ(put(s, "/p/x", "y", CFS_ANY_VERSION, &v), CFS_OK);
    CHECK_EQ(cfs_store_status(s, &version, sum3, &entries, &bytes), CFS_OK);
    CHECK(memcmp(sum1, sum3, CFS_CHECKSUM_LEN) != 0); /* any change of content changes the checksum */

    /* A counter received from a peer at its very end: refused, not wrapped around (found by the fuzzer). */
    CHECK_EQ(cfs_store_status(s, &version, sum3, &entries, &bytes), CFS_OK);
    CHECK_EQ(cfs_store_replace_begin(s), CFS_OK);
    CHECK_EQ(cfs_store_replace_commit(s, version + 1, INT64_MAX, 0), CFS_OK);
    CHECK_EQ(cfs_store_next_id(s, &id1), CFS_INTERNAL);
    CHECK_EQ(cfs_store_next_id(s, &id1), CFS_INTERNAL);
    cfs_store_close(s);
}

static void test_locks(cfs_store *s)
{
    char holder[CFS_NAME_MAX + 1];
    int64_t before, after, entries, bytes;
    uint8_t sum1[CFS_CHECKSUM_LEN], sum2[CFS_CHECKSUM_LEN];
    CHECK_EQ(cfs_store_status(s, &before, sum1, &entries, &bytes), CFS_OK);
    CHECK_EQ(cfs_store_lock(s, "vm:100", "migrate@pve1", 1, 30, 1000, holder), CFS_OK);
    CHECK_EQ(cfs_store_status(s, &after, sum2, &entries, &bytes), CFS_OK);
    CHECK_EQ(after, before + 1);                          /* a lock is a change like any other */
    CHECK(memcmp(sum1, sum2, CFS_CHECKSUM_LEN) != 0);       /* and part of the state the nodes compare */
    CHECK_EQ(cfs_store_lock(s, "vm:100", "backup@pve2", 2, 30, 1001, holder), CFS_LOCKED);
    CHECK(strcmp(holder, "migrate@pve1") == 0);
    CHECK_EQ(cfs_store_lock(s, "vm:100", "migrate@pve1", 1, 30, 1010, holder), CFS_OK); /* renewal */
    CHECK_EQ(cfs_store_unlock(s, "vm:100", "backup@pve2", 1011), CFS_FORBIDDEN);
    CHECK_EQ(cfs_store_unlock(s, "vm:100", "migrate@pve1", 1012), CFS_OK);
    CHECK_EQ(cfs_store_unlock(s, "vm:100", "migrate@pve1", 1013), CFS_NOT_FOUND);

    /* A holder that hangs loses the lock when its time to live ends. */
    CHECK_EQ(cfs_store_lock(s, "vm:101", "a", 1, 10, 2000, holder), CFS_OK);
    CHECK_EQ(cfs_store_lock(s, "vm:101", "b", 2, 10, 2009, holder), CFS_LOCKED);
    CHECK_EQ(cfs_store_lock(s, "vm:101", "b", 2, 10, 2010, holder), CFS_OK);
    CHECK_EQ(cfs_store_lock(s, "x", "a", 1, 0, 3000, holder), CFS_INVALID);
    CHECK_EQ(cfs_store_lock(s, "x", "a", 1, CFS_LOCK_TTL_MAX + 1, 3000, holder), CFS_INVALID);
    /* A time no clock gives, from a damaged or forged change: refused, not an expiry that overflows. */
    CHECK_EQ(cfs_store_lock(s, "x", "a", 1, 60, INT64_MAX, holder), CFS_INVALID);
    CHECK_EQ(cfs_store_lock(s, "x", "a", 1, 60, -1, holder), CFS_INVALID);

    /* The locks taken from a node that left are released; the others stay. */
    CHECK_EQ(cfs_store_lock(s, "vm:200", "ha@node3", 3, 600, 4000, holder), CFS_OK);
    CHECK_EQ(cfs_store_lock(s, "vm:201", "ha@node1", 1, 600, 4000, holder), CFS_OK);
    uint32_t members[] = {1, 2};
    CHECK_EQ(cfs_store_drop_locks(s, members, 2), CFS_OK);
    CHECK_EQ(cfs_store_lock(s, "vm:200", "other", 1, 60, 4001, holder), CFS_OK);
    CHECK_EQ(cfs_store_lock(s, "vm:201", "other", 2, 60, 4001, holder), CFS_LOCKED);
    CHECK_EQ(cfs_store_status(s, &before, sum1, &entries, &bytes), CFS_OK);
    CHECK_EQ(cfs_store_drop_locks(s, members, 2), CFS_OK); /* nothing to release: no change */
    CHECK_EQ(cfs_store_status(s, &after, sum2, &entries, &bytes), CFS_OK);
    CHECK_EQ(after, before);

    /* The table is bounded. */
    char name[32];
    int taken = 0;
    for (int i = 0; i < CFS_LOCKS_MAX + 5; i++) {
        snprintf(name, sizeof(name), "l%d", i);
        if (cfs_store_lock(s, name, "o", 1, 60, 5000, holder) == CFS_OK)
            taken++;
    }
    CHECK(taken < CFS_LOCKS_MAX + 5);
    CHECK_EQ(cfs_store_lock(s, "one-more", "o", 1, 60, 5000, holder), CFS_TOO_LARGE);
    CHECK_EQ(cfs_store_lock(s, "one-more", "o", 1, 60, 5060, holder), CFS_OK); /* the others expired */
}

int main(void)
{
    char db[256];
    temp_db(db, sizeof(db));
    cfs_store *s = open_store(db);
    test_versions_and_compare_and_set(s);
    test_files_and_directories_do_not_overlap(s);
    test_rename(s);
    test_limits(s);
    test_locks(s);
    cfs_store_close(s);
    temp_db_remove(db);

    temp_db(db, sizeof(db));
    test_ids_status_and_persistence(db);
    temp_db_remove(db);
    return check_failures;
}
