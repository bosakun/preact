"""Bounded GPU launcher cleanup, independent of any simulator/model SDK."""

import asyncio
import os
import signal


async def stop_process(process):
    evidence = {
        "operation_kind": "local_process",
        "request_accepted": False,
        "terminal_state_confirmed": False,
    }
    try:
        # Only terminate the session created specifically for this invocation.
        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
        except ProcessLookupError:
            pass
        else:
            evidence["request_accepted"] = True
        async with asyncio.timeout(5):
            await process.wait()
        evidence["terminal_state_confirmed"] = True
        evidence["worker_status"] = "cancelled"
    except Exception as error:
        evidence["error"] = type(error).__name__
    return evidence


async def reap_cancelled(process, error):
    cleanup = asyncio.create_task(stop_process(process))
    cancelled_again = False
    while not cleanup.done():
        try:
            await asyncio.shield(cleanup)
        except asyncio.CancelledError:
            cancelled_again = True
    if cancelled_again and not isinstance(error, asyncio.CancelledError):
        error = asyncio.CancelledError()
    error.preact_cleanup = cleanup.result()
    raise error from None
