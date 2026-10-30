"""The target's side of a disk coming from another cluster (PUT /sites/incoming/{id}/disks/{dev}): the request's body,
read by the event loop, decoded on a thread (app/core/site_stream.py: every frame checked) into the pipe
hyperlite-daemon handed over for that disk (transfer.import)."""

import asyncio
import contextlib
import os
import queue

from fastapi.concurrency import run_in_threadpool

from app.core import site_stream

DEPTH = 16  # chunks waiting between the event loop and the decoding thread: what a slow disk holds in memory


class ReceiveError(Exception):
    pass


async def receive(body, fd, size):
    """The stream's digest once every frame was written into the pipe `fd` (closed here). Raises ReceiveError with
    the first rule broken; the daemon's helper then sees the pipe close without its end, and fails."""
    chunks = queue.Queue(maxsize=DEPTH)

    def take():
        while True:
            chunk = chunks.get()
            if chunk is None:
                return
            yield chunk

    def decode():
        with os.fdopen(fd, "wb") as pipe:
            sink = site_stream.ImportSink(pipe)
            digest = site_stream.decode(take(), size, sink.write, sink.zero)
            sink.end()
            return digest

    worker = asyncio.ensure_future(run_in_threadpool(decode))

    def give(chunk):
        while not worker.done():
            try:
                chunks.put(chunk, timeout=1)
                return
            except queue.Full:
                continue

    try:
        async for chunk in body:
            if worker.done():
                break
            if chunk:
                await run_in_threadpool(give, chunk)
        await run_in_threadpool(give, None)
        return await worker
    except (site_stream.StreamError, BrokenPipeError) as e:
        raise ReceiveError(f"The disk was refused: {e}") from None
    finally:
        if not worker.done():
            await run_in_threadpool(give, None)
            with contextlib.suppress(Exception):
                await worker
