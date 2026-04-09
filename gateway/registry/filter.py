from enum import Enum
from dataclasses import dataclass, field
import fnmatch
import gateway.labels as lbl


class FilterMode(Enum):
    ALLOW_ALL = "allow_all"
    ALLOWLIST = "allowlist"
    DENYLIST = "denylist"
    ERROR = "error"


@dataclass
class FilterSpec:
    mode: FilterMode
    tags: list[str] = field(default_factory=list)
    paths: list[str] = field(default_factory=list)
    operations: list[str] = field(default_factory=list)


def parse_labels(labels: dict) -> FilterSpec:
    has_filter = lbl.has_prefix(labels, "filter.")
    has_exclude = lbl.has_prefix(labels, "exclude.")

    if has_filter and has_exclude:
        return FilterSpec(mode=FilterMode.ERROR)

    if has_filter:
        return FilterSpec(
            mode=FilterMode.ALLOWLIST,
            tags=lbl.parse_csv(labels, "filter.tags"),
            paths=lbl.parse_csv(labels, "filter.paths"),
            operations=lbl.parse_csv(labels, "filter.operations"),
        )

    if has_exclude:
        return FilterSpec(
            mode=FilterMode.DENYLIST,
            tags=lbl.parse_csv(labels, "exclude.tags"),
            paths=lbl.parse_csv(labels, "exclude.paths"),
            operations=lbl.parse_csv(labels, "exclude.operations"),
        )

    return FilterSpec(mode=FilterMode.ALLOW_ALL)


def apply_filter(spec: FilterSpec, route: dict) -> bool:
    if spec.mode == FilterMode.ERROR:
        return False
    if spec.mode == FilterMode.ALLOW_ALL:
        return True

    path = route.get("path", "")
    tags = route.get("tags", [])
    op_id = route.get("operationId", "")

    if spec.mode == FilterMode.ALLOWLIST:
        if spec.tags and any(t in tags for t in spec.tags):
            return True
        if spec.paths and any(fnmatch.fnmatch(path, p) for p in spec.paths):
            return True
        if spec.operations and op_id in spec.operations:
            return True
        return False

    if spec.mode == FilterMode.DENYLIST:
        tag_block = spec.tags and any(t in tags for t in spec.tags)
        path_block = spec.paths and any(fnmatch.fnmatch(path, p) for p in spec.paths)
        op_block = spec.operations and op_id in spec.operations
        return not (tag_block or path_block or op_block)

    return True
