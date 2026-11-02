import asyncio
import os
import sys
from asyncio.subprocess import PIPE
from pathlib import Path

CHILD = Path(__file__).with_name("normalize_child.py")
# More children than cores only fight for them, and each one holds a whole decoded photo in memory.
# shortcut: the queue of waiting photos has no limit, answer 503 past some length if real traffic ever gets near it.
_SLOTS = asyncio.Semaphore(os.process_cpu_count() or 1)


class BadImage(Exception):
    pass


class NotDecoded(BadImage):
    """The child ran out of time or was killed: this says nothing about the image itself."""


async def normalize(data: bytes, max_side: int = 2048) -> bytes:
    # The wait for a slot stays outside the 20 s limit, otherwise a busy server would call good photos bad.
    async with _SLOTS:
        # shortcut: the child can still reach the network, the owner accepted this; use a network-less container if decoder exploits become a real concern.
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-I", str(CHILD), str(max_side), stdin=PIPE, stdout=PIPE, stderr=PIPE, env={}
        )
        try:
            async with asyncio.timeout(20):
                out, err = await proc.communicate(data)
        except TimeoutError:
            raise NotDecoded("timed out") from None
        finally:
            if proc.returncode is None:
                proc.kill()
                await proc.wait()
    if proc.returncode < 0:
        raise NotDecoded(f"killed by signal {-proc.returncode}")
    if proc.returncode != 0:
        raise BadImage(err.decode(errors="replace")[-300:])
    return out


def write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)
