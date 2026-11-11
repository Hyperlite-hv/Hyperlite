"""A large paste reaches the program whole, through a non-blocking pty whose buffer fills up."""

import asyncio
import contextlib
import os
import pty

from app.core import pty_session


def test_a_paste_larger_than_the_pty_buffer_arrives_whole():
    master, slave = pty.openpty()
    os.set_blocking(master, False)
    data = b"x" * 200_000 + b"\n"
    received = bytearray()

    async def main():
        loop = asyncio.get_running_loop()

        async def drain():
            while len(received) < len(data):
                await asyncio.sleep(0.01)
                with contextlib.suppress(BlockingIOError):
                    received.extend(os.read(slave, 65536))

        os.set_blocking(slave, False)
        await asyncio.wait_for(asyncio.gather(pty_session._write_all(loop, master, data), drain()), 20)

    try:
        import termios
        import tty

        tty.setraw(slave, termios.TCSANOW)  # no line discipline: every byte goes through as written
        asyncio.run(main())
    finally:
        os.close(master)
        os.close(slave)
    assert bytes(received) == data
