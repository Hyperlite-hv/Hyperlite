"""The Terminal's relay (docs/design/workbench.md, section 4): copies bytes between a listening end and a connecting
end, nothing else. Inside the box it listens on 127.0.0.1:8443 and connects to the UNIX socket bound into it; outside
it listens on that socket and connects to the API on 127.0.0.1:8000. TLS goes through it untouched.

    relay.py tcp-to-unix 127.0.0.1 8443 /run/workbench/api.sock
    relay.py unix-to-tcp /run/hyperlite/workbench/<sid>/api.sock 127.0.0.1 8000
"""

import asyncio
import os
import sys


async def _pipe(reader, writer):
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    except (ConnectionError, asyncio.IncompleteReadError):
        pass
    finally:
        writer.close()


async def _serve(connect):
    async def handle(reader, writer):
        try:
            up_reader, up_writer = await connect()
        except OSError:
            writer.close()
            return
        await asyncio.gather(_pipe(reader, up_writer), _pipe(up_reader, writer))

    return handle


async def main(argv):
    mode = argv[0]
    if mode == "tcp-to-unix":
        host, port, path = argv[1], int(argv[2]), argv[3]
        server = await asyncio.start_server(await _serve(lambda: asyncio.open_unix_connection(path)), host, port)
    elif mode == "unix-to-tcp":
        path, host, port = argv[1], argv[2], int(argv[3])
        if os.path.exists(path):
            os.unlink(path)
        server = await asyncio.start_unix_server(await _serve(lambda: asyncio.open_connection(host, port)), path)
        os.chmod(path, 0o660)
    else:
        raise SystemExit(f"unknown mode {mode}")
    async with server:
        await server.serve_forever()


def _not_dumpable():
    """The relay outside the box runs as the box's own uid: made not dumpable, a process of the box cannot open its
    memory (/proc/<pid>/mem) and use its way out of the box's network."""
    import ctypes

    PR_SET_DUMPABLE = 4
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(PR_SET_DUMPABLE, 0, 0, 0, 0) != 0:
        raise SystemExit(f"prctl(PR_SET_DUMPABLE) failed: {os.strerror(ctypes.get_errno())}")


if __name__ == "__main__":
    _not_dumpable()
    asyncio.run(main(sys.argv[1:]))
