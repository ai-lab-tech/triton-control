"""Bounded, sequential parsing of Prometheus text-format label sets."""

import re

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
