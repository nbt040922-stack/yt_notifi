from dataclasses import dataclass


@dataclass(frozen=True)
class VideoEvent:
    video_id: str
    channel_id: str
    title: str
    published: str
    updated: str
    url: str
    source: str = "websub"
