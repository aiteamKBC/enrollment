"""Keep video iterators incremental under both WSGI and ASGI."""
import asyncio

from django.core.handlers.asgi import ASGIRequest


async def _async_chunks(chunks):
    iterator = iter(chunks)
    end = object()
    try:
        while True:
            read = asyncio.create_task(asyncio.to_thread(next, iterator, end))
            try:
                chunk = await asyncio.shield(read)
            except asyncio.CancelledError:
                # A disconnected client cannot interrupt a blocking socket read.
                # Let that read finish before closing its running generator.
                await read
                raise
            if chunk is end:
                break
            yield chunk
    finally:
        close = getattr(iterator, 'close', None)
        if close:
            await asyncio.to_thread(close)


def video_chunks(request, chunks):
    # Django buffers the entire body when the iterator type does not match
    # the server interface. Offload only the next blocking read under ASGI.
    return _async_chunks(chunks) if isinstance(request, ASGIRequest) else chunks
