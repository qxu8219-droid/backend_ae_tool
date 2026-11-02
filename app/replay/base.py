"""Pure replay sampling values; no worker or transport dependency."""
from dataclasses import dataclass
from replay_protocol import ChannelQuality


class SamplingError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


@dataclass(frozen=True)
class SampleValue:
    value: float | None
    quality: ChannelQuality
