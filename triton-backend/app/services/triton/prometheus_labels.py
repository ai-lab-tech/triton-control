"""Bounded, sequential parsing of Prometheus text-format label sets."""

import re
from collections.abc import Iterator
from math import isfinite

MAX_LABEL_TOKEN_BYTES = 64 * 1024
_LABEL_NAME = re.compile(r"[a-zA-Z_][a-zA-Z0-9_]*")
_ESCAPES = {"n": "\n", '"': '"', "\\": "\\"}


def _value(text: str, start: int, end: int) -> tuple[str, int] | None:
    characters: list[str] = []
    position = start
    while position < end:
        character = text[position]
        position += 1
        if character == '"':
            return "".join(characters), position
        if character == "\\":
            if position >= end or text[position] not in _ESCAPES:
                return None
            character = _ESCAPES[text[position]]
            position += 1
        elif character in "\r\n":
            return None
        characters.append(character)
    return None


def parse_labels(token: str) -> dict[str, str]:
    """Reject oversized/malformed sets atomically; never search again at each offset."""
    if len(token) > MAX_LABEL_TOKEN_BYTES or len(token.encode("utf-8")) > MAX_LABEL_TOKEN_BYTES:
        return {}
    opening = token.find("{")
    if opening < 0 or not token.endswith("}"):
        return {}
    position, end = opening + 1, len(token) - 1
    labels: dict[str, str] = {}
    while position < end:
        while position < end and token[position] in " \t":
            position += 1
        if position == end:
            break
        name = _LABEL_NAME.match(token, position)
        if name is None:
            return {}
        key = name.group()
        position = name.end()
        if key in labels or token[position:position + 2] != '="':
            return {}
        parsed = _value(token, position + 2, end)
        if parsed is None:
            return {}
        labels[key], position = parsed
        while position < end and token[position] in " \t":
            position += 1
        if position < end:
            if token[position] != ",":
                return {}
            position += 1
    return labels

_METRIC_NAME = re.compile(r"[a-zA-Z_:][a-zA-Z0-9_:]*")


def samples(text: str) -> Iterator[tuple[str, dict[str, str], float]]:
    """Read a metric token, numeric value and optional integer timestamp."""
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        end = line.rfind("}")
        if end >= 0:
            if not line[end + 1:end + 2].isspace():
                continue
            token = line[:end + 1]
            name = token.partition("{")[0]
            labels = parse_labels(token)
            if not labels and token != name + "{}":
                continue
            fields = line[end + 1:].split()
        else:
            name, *fields = line.split()
            labels = {}
        if not _METRIC_NAME.fullmatch(name) or len(fields) not in (1, 2):
            continue
        try:
            value = float(fields[0])
            if len(fields) == 2:
                int(fields[1])
        except ValueError:
            continue
        if isfinite(value) and value >= 0:
            yield name, labels, value
