/* libFuzzer target: any byte string as a request body, through the decoder and the whole handler, against a real
 * store in a temporary directory. Built with meson -Dfuzz=true (clang), run with ./fuzz_request -max_total_time=60. */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "handler.h"

static cfs_ctx ctx;

static void setup(void)
{
    char dir[] = "/tmp/cfs-fuzz-XXXXXX";
    char db[64], err[256];
    if (!mkdtemp(dir))
        abort();
    snprintf(db, sizeof(db), "%s/config.db", dir);
    ctx.store = cfs_store_open(db, err, sizeof(err));
    if (!ctx.store) {
        fprintf(stderr, "%s\n", err);
        abort();
    }
    ctx.quorate = true;
}

int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size);

int LLVMFuzzerTestOneInput(const uint8_t *data, size_t size)
{
    if (!ctx.store)
        setup();
    cfs_writer out;
    cfs_writer_init(&out);
    /* uid 0 so the fuzzer also reaches priv/ paths. */
    cfs_handle(&ctx, data, size, 0, 1000, &out);
    if (out.len < 9)
        abort(); /* every request gets at least a framed status */
    cfs_writer_free(&out);
    return 0;
}
