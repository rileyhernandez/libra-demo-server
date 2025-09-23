from dataclasses import dataclass
from typing import Self


@dataclass
class ClientData:
    model: str
    number: str
    timestamp: str
    ingredient: str

    @classmethod
    def from_log(cls, log: dict) -> Self:
        return ClientData(log['model'], log['number'], log['timestamp'], log['ingredient'])