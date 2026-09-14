import asyncio
import logging
import os
import sys
import threading

# logging
logger = logging.getLogger(__name__)


def running_in_spyder() -> bool:
    """Return True when running inside Spyder or its kernel process."""

    return (
        "SPYDER_ARGS" in os.environ
        or "SPYDER_PARENT_DIR" in os.environ
        or "spyder" in sys.modules
        or "spyder_kernels" in sys.modules
    )


def main_thread_has_running_loop() -> bool:
    """Return True when the current thread already has an active asyncio loop."""

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


def run_in_new_thread(coro_factory, *args, **kwargs):
    """Run the coroutine in a fresh thread with a Proactor loop on Windows."""
    
    result_holder = {}
    error_holder = {}

    def _run_in_thread():
        try:
            if sys.platform == "win32":
                loop = asyncio.ProactorEventLoop()
                asyncio.set_event_loop(loop)
                try:
                    result_holder["value"] = loop.run_until_complete(
                        coro_factory(*args, **kwargs)
                    )
                finally:
                    loop.run_until_complete(loop.shutdown_asyncgens())
                    loop.close()
            else:
                result_holder["value"] = asyncio.run(coro_factory(*args, **kwargs))
        except BaseException as exc:
            error_holder["value"] = exc

    thread = threading.Thread(target=_run_in_thread)
    thread.start()
    thread.join()

    if "value" in error_holder:
        raise error_holder["value"]

    return result_holder.get("value")


def run_async_entrypoint(coro_factory, *args, **kwargs):
    """
    Terminal:
      - use asyncio.run normally
    Spyder / already-running loop:
      - use a fresh thread
    """
    
    if running_in_spyder() or main_thread_has_running_loop():
        return run_in_new_thread(coro_factory, *args, **kwargs)

    return asyncio.run(coro_factory(*args, **kwargs))

