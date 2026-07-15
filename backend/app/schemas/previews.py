from __future__ import annotations

from datetime import datetime

from pydantic import Field, model_validator

from app.schemas.base import CamelModel
from app.schemas.video import AccessMode


class CreatePreviewRequest(CamelModel):
    video_stream_id: str | None = Field(default=None, min_length=1, max_length=36)
    audio_stream_id: str | None = Field(default=None, min_length=1, max_length=36)
    access_mode: AccessMode

    @model_validator(mode="after")
    def distinct_tracks(self) -> CreatePreviewRequest:
        if self.video_stream_id is None and self.audio_stream_id is None:
            raise ValueError("at least one preview stream is required")
        if self.audio_stream_id == self.video_stream_id:
            raise ValueError("video and audio streams must be different")
        return self


class PreviewTrackRead(CamelModel):
    stream_id: str
    mime_type: str
    codec_string: str


class PreviewRead(CamelModel):
    id: str
    manifest_url: str
    expires_at: datetime
    duration: int
    video: PreviewTrackRead | None
    audio: PreviewTrackRead | None
