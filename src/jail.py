import datetime
from typing import Dict, NamedTuple


class JailSentence(NamedTuple):
    """
    The goo jail role is the source of truth for who is locked up. This tracks
    when they get out and how long they were put away for, which is what the
    jailbreak roll is weighted against.
    """
    release_at: datetime.datetime
    total_seconds: int

    @classmethod
    def begin(cls, now: datetime.datetime, total_seconds: int) -> 'JailSentence':
        return cls(now + datetime.timedelta(seconds=total_seconds), total_seconds)

    def seconds_remaining(self, now: datetime.datetime) -> float:
        return max((self.release_at - now).total_seconds(), 0.0)

    def fraction_remaining(self, now: datetime.datetime) -> float:
        if self.total_seconds <= 0:
            return 0.0
        return min(self.seconds_remaining(now) / self.total_seconds, 1.0)

    def extended_by(self, seconds: int) -> 'JailSentence':
        return JailSentence(
            self.release_at + datetime.timedelta(seconds=seconds),
            self.total_seconds + seconds,
        )


JailCounter = Dict[int, JailSentence]
