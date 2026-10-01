#include "locks.h"

#include <stdio.h>
#include <string.h>

void cfs_locks_init(cfs_lock_table *t)
{
    t->count = 0;
}

static void expire(cfs_lock_table *t, time_t now)
{
    size_t i = 0;
    while (i < t->count) {
        if (t->items[i].expires <= now)
            t->items[i] = t->items[--t->count];
        else
            i++;
    }
}

static cfs_lock *find(cfs_lock_table *t, const char *name)
{
    for (size_t i = 0; i < t->count; i++)
        if (strcmp(t->items[i].name, name) == 0)
            return &t->items[i];
    return NULL;
}

int cfs_lock_acquire(cfs_lock_table *t, const char *name, const char *owner, uint32_t ttl, time_t now,
                     char holder[CFS_NAME_MAX + 1])
{
    holder[0] = '\0';
    if (ttl == 0 || ttl > CFS_LOCK_TTL_MAX)
        return CFS_INVALID;
    expire(t, now);
    cfs_lock *l = find(t, name);
    if (l) {
        if (strcmp(l->owner, owner) != 0) {
            snprintf(holder, CFS_NAME_MAX + 1, "%s", l->owner);
            return CFS_LOCKED;
        }
        l->expires = now + (time_t)ttl;
        return CFS_OK;
    }
    if (t->count == CFS_LOCKS_MAX)
        return CFS_TOO_LARGE;
    l = &t->items[t->count++];
    snprintf(l->name, sizeof(l->name), "%s", name);
    snprintf(l->owner, sizeof(l->owner), "%s", owner);
    l->expires = now + (time_t)ttl;
    return CFS_OK;
}

int cfs_lock_release(cfs_lock_table *t, const char *name, const char *owner, time_t now)
{
    expire(t, now);
    cfs_lock *l = find(t, name);
    if (!l)
        return CFS_NOT_FOUND;
    if (strcmp(l->owner, owner) != 0)
        return CFS_FORBIDDEN;
    *l = t->items[--t->count];
    return CFS_OK;
}
