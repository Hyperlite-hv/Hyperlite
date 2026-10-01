#include "path.h"

#include <string.h>

#include "cfs.h"
#include "check.h"

int main(void)
{
    CHECK(cfs_path_valid("/cluster/settings.json", false));
    CHECK(cfs_path_valid("/nodes/pve1/qemu/100.json", false));
    CHECK(cfs_path_valid("/a", false));
    CHECK(cfs_path_valid("/", true));
    CHECK(!cfs_path_valid("/", false));

    const char *bad[] = {"",       "relative", "/a/",     "//a",   "/a//b", "/./a", "/a/..",  "/a/../b",
                         "/a b",   "/a;b",     "/a\\b",   "/é",    "/a/*",  "/~",   "/a\nb",  "/$x"};
    for (size_t i = 0; i < sizeof(bad) / sizeof(bad[0]); i++)
        CHECK(!cfs_path_valid(bad[i], true));
    CHECK(cfs_path_valid("/.hidden", false)); /* a dot inside a name is fine, only "." and ".." are refused */

    char longp[CFS_PATH_MAX + 2];
    longp[0] = '/';
    memset(longp + 1, 'a', CFS_PATH_MAX);
    longp[CFS_PATH_MAX + 1] = '\0';
    CHECK(!cfs_path_valid(longp, false)); /* 256 bytes */
    longp[CFS_PATH_MAX] = '\0';
    CHECK(cfs_path_valid(longp, false)); /* 255 bytes */

    char deep[128] = "";
    for (int i = 0; i < CFS_PATH_DEPTH_MAX; i++)
        strcat(deep, "/d");
    CHECK(cfs_path_valid(deep, false));
    strcat(deep, "/d");
    CHECK(!cfs_path_valid(deep, false));

    CHECK(cfs_path_is_private("/priv/authkey"));
    CHECK(cfs_path_is_private("/nodes/pve1/priv/key"));
    CHECK(cfs_path_is_private("/priv"));
    CHECK(!cfs_path_is_private("/private/x"));
    CHECK(!cfs_path_is_private("/nodes/privx/y"));
    CHECK(!cfs_path_is_private("/cluster/settings.json"));

    CHECK(cfs_name_valid("vm:100"));
    CHECK(cfs_name_valid("migrate@pve1"));
    CHECK(!cfs_name_valid(""));
    CHECK(!cfs_name_valid("a b"));
    CHECK(!cfs_name_valid("a/b"));
    return check_failures;
}
