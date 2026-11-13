import re
from dataclasses import dataclass
from pathlib import Path


VALID_PAIR_ROLES = {"INV", "PAY", "BOTH"}

_PAIR_TAG_RE = re.compile(r"\[(PAIR-[A-Z0-9_-]+)\]", re.IGNORECASE)
_ROLE_TAG_RE = re.compile(r"\[([A-Z]+)\]", re.IGNORECASE)


@dataclass(frozen=True)
class PairTag:
    pair_id: str
    pair_role: str | None
    clean_filename: str
    invalid_role: str | None = None


def _clean_filename(filename: str, tags: list[str]) -> str:
    clean = filename
    for tag in tags:
        clean = clean.replace(tag, "")
    return " ".join(clean.split()).strip()


def parse_pair_tag(source_file: str) -> PairTag | None:
    filename = Path(source_file).name
    pair_match = _PAIR_TAG_RE.search(filename)
    if pair_match is None:
        return None

    pair_id = pair_match.group(1).upper()
    role = None
    invalid_role = None
    matched_tags = [pair_match.group(0)]

    for role_match in _ROLE_TAG_RE.finditer(filename):
        raw_tag = role_match.group(0)
        raw_role = role_match.group(1).upper()
        if raw_role == pair_id:
            continue
        if raw_role in VALID_PAIR_ROLES:
            role = raw_role
            matched_tags.append(raw_tag)
            break
        if raw_role != pair_id.replace("-", ""):
            invalid_role = raw_role
            matched_tags.append(raw_tag)
            break

    return PairTag(
        pair_id=pair_id,
        pair_role=role,
        clean_filename=_clean_filename(filename, matched_tags),
        invalid_role=invalid_role,
    )
