from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol


class DongleState(str, Enum):
    PRESENT = "present"
    MISSING = "missing"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class DongleStatus:
    state: DongleState
    message: str

    @property
    def available(self) -> bool:
        return self.state is DongleState.PRESENT


class DongleAdapter(Protocol):
    def check(self) -> DongleStatus: ...


class MockDongleAdapter:
    """Replace this adapter with the hardware vendor SDK implementation later."""

    def __init__(self, configured_state: str = "present"):
        self.configured_state = configured_state

    def check(self) -> DongleStatus:
        try:
            state = DongleState(self.configured_state)
        except ValueError:
            state = DongleState.ERROR
        messages = {
            DongleState.PRESENT: "已检测到加密锁",
            DongleState.MISSING: "未检测到加密锁",
            DongleState.ERROR: "请检查加密锁",
        }
        return DongleStatus(state=state, message=messages[state])

