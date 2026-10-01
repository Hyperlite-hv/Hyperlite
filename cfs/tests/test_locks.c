#include "locks.h"

#include "check.h"

int main(void)
{
    static cfs_lock_table t;
    char holder[CFS_NAME_MAX + 1];
    cfs_locks_init(&t);

    CHECK_EQ(cfs_lock_acquire(&t, "vm:100", "migrate@pve1", 30, 1000, holder), CFS_OK);
    CHECK_EQ(cfs_lock_acquire(&t, "vm:100", "backup@pve2", 30, 1001, holder), CFS_LOCKED);
    CHECK(strcmp(holder, "migrate@pve1") == 0);
    CHECK_EQ(cfs_lock_acquire(&t, "vm:100", "migrate@pve1", 30, 1010, holder), CFS_OK); /* renewal */
    CHECK_EQ(cfs_lock_release(&t, "vm:100", "backup@pve2", 1011), CFS_FORBIDDEN);
    CHECK_EQ(cfs_lock_release(&t, "vm:100", "migrate@pve1", 1012), CFS_OK);
    CHECK_EQ(cfs_lock_release(&t, "vm:100", "migrate@pve1", 1013), CFS_NOT_FOUND);

    /* A holder that hangs loses the lock when its time to live ends. */
    CHECK_EQ(cfs_lock_acquire(&t, "vm:101", "a", 10, 2000, holder), CFS_OK);
    CHECK_EQ(cfs_lock_acquire(&t, "vm:101", "b", 10, 2009, holder), CFS_LOCKED);
    CHECK_EQ(cfs_lock_acquire(&t, "vm:101", "b", 10, 2010, holder), CFS_OK);

    CHECK_EQ(cfs_lock_acquire(&t, "x", "a", 0, 3000, holder), CFS_INVALID);
    CHECK_EQ(cfs_lock_acquire(&t, "x", "a", CFS_LOCK_TTL_MAX + 1, 3000, holder), CFS_INVALID);

    cfs_locks_init(&t);
    char name[32];
    for (int i = 0; i < CFS_LOCKS_MAX; i++) {
        snprintf(name, sizeof(name), "l%d", i);
        CHECK_EQ(cfs_lock_acquire(&t, name, "o", 60, 4000, holder), CFS_OK);
    }
    CHECK_EQ(cfs_lock_acquire(&t, "one-more", "o", 60, 4000, holder), CFS_TOO_LARGE);
    CHECK_EQ(cfs_lock_acquire(&t, "one-more", "o", 60, 4060, holder), CFS_OK); /* the others expired */
    return check_failures;
}
