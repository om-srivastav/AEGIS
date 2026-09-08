from __future__ import annotations
import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Callable

from app.models.schemas import PerturbationKind


BASE_FILES = {
    "sales.csv": (
        "order_id,quantity,price\n"
        "A001,2,100\n"
        "A002,1,250\n"
        "A003,3,50\n"
    )
}
BASE_RESOURCE_IDS = {"sales.csv": "sales_input"}


class ToolTimeoutError(RuntimeError):
    pass


def _hash_files(files: dict[str, str]) -> str:
    payload = json.dumps(files, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(payload).hexdigest()


@dataclass
class VirtualFileEnvironment:
    perturbation: PerturbationKind = PerturbationKind.NONE
    files: dict[str, str] = field(default_factory=lambda: deepcopy(BASE_FILES))
    resource_ids: dict[str, str] = field(default_factory=lambda: deepcopy(BASE_RESOURCE_IDS))
    read_count: int = 0
    perturbation_activated: bool = False
    activation_receipts: list[dict[str, Any]] = field(default_factory=list)
    event_hook: Callable[[str, dict[str, Any]], None] | None = None
    listing_complete: bool = True

    def __post_init__(self) -> None:
        if self.perturbation == PerturbationKind.FIELD_RENAMED:
            content = self.files["sales.csv"]
            self.files["sales.csv"] = content.replace("price", "unit_price")

    def snapshot_hash(self) -> str:
        return _hash_files(self.files)

    def _activate_file_move(self, trigger_path: str) -> None:
        if self.perturbation != PerturbationKind.FILE_MOVED or self.perturbation_activated:
            return
        if trigger_path != "sales.csv" or "sales.csv" not in self.files:
            return
        before = self.snapshot_hash()
        content = self.files.pop("sales.csv")
        rid = self.resource_ids.pop("sales.csv")
        self.files["archive/sales.csv"] = content
        self.resource_ids["archive/sales.csv"] = rid
        after = self.snapshot_hash()
        self.perturbation_activated = True
        receipt = {
            "kind": "file_moved",
            "logical_resource_id": rid,
            "from_path": "sales.csv",
            "to_path": "archive/sales.csv",
            "before_state_hash": before,
            "after_state_hash": after,
            "content_hash": hashlib.sha256(content.encode()).hexdigest(),
        }
        self.activation_receipts.append(receipt)
        if self.event_hook:
            self.event_hook("file_moved_activated", receipt)

    def list_files(self, scope_prefix: str = "") -> dict[str, Any]:
        entries = []
        for path in sorted(self.files):
            if scope_prefix and not path.startswith(scope_prefix):
                continue
            entries.append({
                "path": path,
                "resource_id": self.resource_ids.get(path),
                "readable": True,
            })
        return {"complete": self.listing_complete, "entries": entries}

    def read_file(self, path: str) -> str:
        self.read_count += 1
        self._activate_file_move(path)
        if self.perturbation == PerturbationKind.TOOL_TIMEOUT and self.read_count == 1:
            raise ToolTimeoutError("temporary read timeout")
        if path not in self.files:
            raise FileNotFoundError(path)
        content = self.files[path]
        if self.perturbation == PerturbationKind.PARTIAL_RESPONSE and self.read_count == 1:
            lines = content.splitlines()
            return "\n".join(lines[:2]) + "\n"
        return content

    def get_resource_identity(self, path: str) -> str | None:
        return self.resource_ids.get(path)

    def write_json(self, path: str, data: dict[str, Any]) -> None:
        self.files[path] = json.dumps(data, indent=2, sort_keys=True)
        self.resource_ids.setdefault(path, f"generated:{path}")

    def read_json(self, path: str) -> dict[str, Any]:
        if path not in self.files:
            raise FileNotFoundError(path)
        return json.loads(self.files[path])
