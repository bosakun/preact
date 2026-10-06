"""Keep blocking persistence off the loop without orphaning in-flight writes."""

import asyncio


async def durable_io(operation, *args, **kwargs):
    task = asyncio.create_task(asyncio.to_thread(operation, *args, **kwargs))
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            # Cancelling a Python thread cannot roll back a database/file write.
            # Drain it before the caller reconciles intent or records final status.
            cancelled = True
        except Exception:
            break
    if cancelled:
        # Retrieve any failure too; cancellation must not hide an orphaned task.
        if not task.cancelled():
            task.exception()
        raise asyncio.CancelledError
    return task.result()
