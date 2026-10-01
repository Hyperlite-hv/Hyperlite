/* A minimal test harness: CHECK records a failure with its line and goes on; main returns the failure count. */

#ifndef CFS_CHECK_H
#define CFS_CHECK_H

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static int check_failures = 0;

#define CHECK(cond)                                                                                                    \
    do {                                                                                                               \
        if (!(cond)) {                                                                                                 \
            fprintf(stderr, "%s:%d: CHECK failed: %s\n", __FILE__, __LINE__, #cond);                                  \
            check_failures++;                                                                                          \
        }                                                                                                              \
    } while (0)

#define CHECK_EQ(a, b)                                                                                                 \
    do {                                                                                                               \
        long long check_a = (long long)(a), check_b = (long long)(b);                                                  \
        if (check_a != check_b) {                                                                                      \
            fprintf(stderr, "%s:%d: %s == %s failed: %lld != %lld\n", __FILE__, __LINE__, #a, #b, check_a, check_b); \
            check_failures++;                                                                                          \
        }                                                                                                              \
    } while (0)

/* A fresh database file in a temporary directory; the caller removes it with temp_db_remove. */
static inline void temp_db(char *path, size_t cap)
{
    char dir[] = "/tmp/cfs-test-XXXXXX";
    if (!mkdtemp(dir)) {
        perror("mkdtemp");
        exit(1);
    }
    snprintf(path, cap, "%s/config.db", dir);
}

static inline void temp_db_remove(const char *path)
{
    char buf[512];
    const char *suffixes[] = {"", "-wal", "-shm"};
    for (size_t i = 0; i < 3; i++) {
        snprintf(buf, sizeof(buf), "%s%s", path, suffixes[i]);
        unlink(buf);
    }
    snprintf(buf, sizeof(buf), "%s", path);
    char *slash = strrchr(buf, '/');
    if (slash) {
        *slash = '\0';
        rmdir(buf);
    }
}

#endif
