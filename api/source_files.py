"""Keep source media behind the authorized download endpoints."""
from pathlib import Path

from fastapi import HTTPException
from fastapi.staticfiles import StaticFiles


class PlaybackStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope):
        if Path(path).name.lower() in {"original.mp4", "original.mkv", "original.webm", "original.mov"}:
            raise HTTPException(status_code=404, detail="Not found")
        return await super().get_response(path, scope)
