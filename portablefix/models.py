from dataclasses import dataclass
from enum import Enum


class RiskLevel(str, Enum):
    SAFE = "SAFE"
    MODERATE = "MODERATE"
    DESTRUCTIVE = "DESTRUCTIVE"
    REQUIRES_REBOOT = "REQUIRES_REBOOT"


@dataclass
class ActionDef:
    id: str
    label_sk: str
    label_en: str
    risk: RiskLevel
    command: str
    description_sk: str = ""
    description_en: str = ""

    def label(self, language: str) -> str:
        return self.label_en if language == "en" else self.label_sk

    def description(self, language: str) -> str:
        return self.description_en if language == "en" else self.description_sk


@dataclass
class ModuleDef:
    module_id: str
    actions: list[ActionDef]
