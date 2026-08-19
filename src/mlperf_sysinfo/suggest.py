# Copyright 2026 MLCommons contributors
# SPDX-License-Identifier: Apache-2.0
""""Did you mean ...?" for anything the user can misspell.

Config options, profile names and command-line flags are all short vocabularies
that people type from memory, and the difference between a typo and a wrong
guess is invisible from the message alone. Saying which real name is closest
turns both into a one-keystroke fix.

The matching deliberately does more than edit distance:

* leading dashes are ignored, so ``-v`` can reach ``--version``;
* a prefix counts as a match, so ``-v`` and ``sub`` reach their longer names
  even though no distance measure would rank them close;
* underscores and hyphens are treated alike, because ``include-local`` and
  ``include_local`` are the same mistake.
"""

from __future__ import annotations

from difflib import SequenceMatcher
from types import UnionType
from typing import Any, Union, get_args, get_origin

from pydantic import BaseModel

#: Below this, a suggestion is more likely to mislead than to help. Chosen so
#: that one wrong or transposed character in a short word still matches
#: ("capure"/"capture" is 0.92) while unrelated words do not ("show"/"init" is
#: 0.0).
_MIN_RATIO = 0.6

#: More than a few candidates stops being a suggestion and becomes a list --
#: which the caller is usually printing anyway.
_MAX_SUGGESTIONS = 3


def _normalise(value: str) -> str:
    return value.strip().lstrip("-").replace("-", "_").casefold()


def _score(typed: str, candidate: str) -> float:
    """How close two names are, once spelling noise is removed.

    A prefix scores above the threshold on purpose: ``v`` is a plausible stab
    at ``version`` but shares too little of it to rank close on ratio alone.
    The prefix test also runs with word separators dropped, so ``sshkey``
    reaches ``ssh_key_preconfigured`` -- against a name that much longer, a
    ratio is dominated by the length difference and scores 0.44.

    Only a *prefix* gets this treatment, not any substring: matching anywhere
    would make every short word a hit somewhere in a long name.
    """
    a, b = _normalise(typed), _normalise(candidate)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if b.startswith(a) or b.replace("_", "").startswith(a.replace("_", "")):
        # Ranks below an exact match, above anything found by ratio alone.
        return max(0.9, SequenceMatcher(None, a, b).ratio())
    return SequenceMatcher(None, a, b).ratio()


def closest(typed: str, candidates: object, *, limit: int = _MAX_SUGGESTIONS) -> list[str]:
    """The candidates worth suggesting for ``typed``, best first."""
    scored = []
    for candidate in candidates:
        score = _score(typed, candidate)
        if score >= _MIN_RATIO:
            scored.append((score, candidate))
    # Sort by score, then by name so equal scores come out in a stable order
    # rather than in whatever order the caller happened to build the list.
    scored.sort(key=lambda pair: (-pair[0], str(pair[1])))
    return [candidate for _, candidate in scored[:limit]]


def did_you_mean(typed: str, candidates: object, *, quote: str = '"') -> str:
    """A ready-to-append hint, or an empty string when nothing is close.

    Returning "" rather than None keeps call sites to one f-string: a message
    can always be built as ``f"unknown thing {name!r}.{hint}"``.
    """
    matches = closest(typed, candidates)
    if not matches:
        return ""
    shown = ", ".join(f"{quote}{m}{quote}" for m in matches)
    if len(matches) == 1:
        return f" Did you mean {shown}?"
    return f" Did you mean one of {shown}?"


# ---------------------------------------------------------------------------
# which option names are valid at a point in a nested schema
# ---------------------------------------------------------------------------


def _as_model(annotation: Any) -> type[BaseModel] | None:
    try:
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            return annotation
    except TypeError:  # a subscripted generic, e.g. list[str] on 3.10
        pass
    return None


def _strip_optional(annotation: Any) -> Any:
    """``X | None`` -> ``X``. Anything else is returned unchanged."""
    if get_origin(annotation) not in (Union, UnionType):
        return annotation
    args = [a for a in get_args(annotation) if a is not type(None)]
    return args[0] if len(args) == 1 else annotation


def _descend(annotation: Any, part: object) -> Any | None:
    """The annotation one hop further along a validation-error location.

    ``part`` is whatever pydantic put in the location tuple: a field name
    inside a model, a key inside a mapping, or an index inside a sequence. The
    hop is chosen by the annotation rather than by the part, so a free-form
    mapping like ``nodes.groups`` is followed to its *value* type without
    caring what the key was called.
    """
    annotation = _strip_optional(annotation)
    origin = get_origin(annotation)
    if origin is dict:
        args = get_args(annotation)
        return args[1] if len(args) == 2 else None
    if origin is list:
        args = get_args(annotation)
        return args[0] if args else None
    model = _as_model(annotation)
    if model is None:
        return None
    field = model.model_fields.get(str(part))
    return field.annotation if field is not None else None


def options_at(root: type[BaseModel], loc: tuple) -> list[str]:
    """Option names valid where a rejected key sits, for suggesting a fix.

    ``loc`` is pydantic's location tuple for the key that was *rejected*, so
    the walk stops one hop short of the end. An empty list means there is no
    fixed vocabulary there and nothing honest to suggest.
    """
    annotation: Any = root
    for part in loc[:-1]:
        annotation = _descend(annotation, part)
        if annotation is None:
            return []
    model = _as_model(_strip_optional(annotation))
    return list(model.model_fields) if model is not None else []
