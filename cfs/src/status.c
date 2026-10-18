#include "status.h"

#include <stdlib.h>
#include <string.h>
#include <time.h>

void cfs_stat_init(cfs_stat_table *t)
{
    memset(t, 0, sizeof(*t));
}

void cfs_stat_free(cfs_stat_table *t)
{
    for (size_t i = 0; i < t->n; i++)
        free(t->e[i].data);
    memset(t, 0, sizeof(*t));
}

int64_t cfs_stat_now_ms(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (int64_t)ts.tv_sec * 1000 + ts.tv_nsec / 1000000;
}

int cfs_stat_set(cfs_stat_table *t, uint32_t node, const uint8_t *data, size_t len, int64_t now_ms)
{
    if (len > CFS_STAT_MAX)
        return CFS_TOO_LARGE;
    uint8_t *copy = malloc(len ? len : 1);
    if (!copy)
        return CFS_INTERNAL;
    if (len)
        memcpy(copy, data, len);
    size_t slot = t->n;
    for (size_t i = 0; i < t->n; i++)
        if (t->e[i].node == node)
            slot = i;
    if (slot == t->n && t->n == CFS_MEMBERS_MAX) {
        slot = 0; /* full: the oldest status goes */
        for (size_t i = 1; i < t->n; i++)
            if (t->e[i].at_ms < t->e[slot].at_ms)
                slot = i;
    }
    if (slot < t->n)
        free(t->e[slot].data);
    else
        t->n++;
    t->e[slot] = (cfs_stat_entry){.node = node, .data = copy, .len = (uint32_t)len, .at_ms = now_ms};
    return CFS_OK;
}

int cfs_stat_write(const cfs_stat_table *t, uint32_t node, int64_t now_ms, cfs_writer *w)
{
    if (node == 0) {
        cfs_write_u32(w, (uint32_t)t->n);
        for (size_t i = 0; i < t->n; i++) {
            cfs_write_u32(w, t->e[i].node);
            cfs_write_i64(w, now_ms - t->e[i].at_ms);
            cfs_write_u32(w, t->e[i].len);
        }
        return CFS_OK;
    }
    for (size_t i = 0; i < t->n; i++) {
        if (t->e[i].node == node) {
            cfs_write_i64(w, now_ms - t->e[i].at_ms);
            cfs_write_blob(w, t->e[i].data, t->e[i].len);
            return CFS_OK;
        }
    }
    return CFS_NOT_FOUND;
}
