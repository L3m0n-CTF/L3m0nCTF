"""Verification codes: `L3M0N26-<CAT>-<5 letters>`, how they are made and how a typed code is read back.

The five letters come from 21 consonants (no vowels): no I or O to mistake for 1 or 0 next to the zero of L3M0N, and no accidental words.
21**5 is about 4.08 million possible suffixes for roughly 1,500 people, so a random guess finds a real code about once in 2,700 tries;
the portal's rate limit and the low value of what a code shows (a username, a studio, a category) make guessing pointless.

The suffix is unique across ALL categories, and a code is checked as a whole: a real suffix with the wrong category is not a code.
"""
from __future__ import annotations

import re
import secrets

PREFIX = "L3M0N26"
CATEGORIES = ("ORG", "PAR", "ACH", "APP")
ALPHABET = "BCDFGHJKLMNPQRSTVWXYZ"
SUFFIX_LEN = 5

CODE_RE = re.compile(rf"^{PREFIX}-({'|'.join(CATEGORIES)})-([{ALPHABET}]{{{SUFFIX_LEN}}})\Z")


def make(category: str, suffix: str) -> str:
    code = f"{PREFIX}-{category}-{suffix}"
    if not CODE_RE.match(code):
        raise ValueError(f"not a valid code: {code!r}")
    return code


def new_suffix(taken: set[str], rand=secrets.choice) -> str:
    """A fresh random suffix that is not in `taken` (a cryptographic random choice; `rand` is replaceable only for tests)."""
    for _ in range(10_000):
        suffix = "".join(rand(ALPHABET) for _ in range(SUFFIX_LEN))
        if suffix not in taken:
            return suffix
    raise RuntimeError("could not find a free code suffix")  # unreachable with 4 million suffixes and a few thousand people


def normalize(text: str) -> str | None:
    """A code as a person may type it -> the canonical code, or None when it cannot be one.

    Accepted: any case, spaces anywhere, dashes or no dashes, a pasted link ending in the code, and the letter O typed for the zero of L3M0N.
    Nothing else is guessed: a wrong letter stays wrong.
    """
    if not isinstance(text, str) or len(text) > 200:
        return None
    s = text.strip()
    if "/" in s:
        s = s.rstrip("/").rsplit("/", 1)[-1]
    s = re.sub(r"[\s\-_.]+", "", s.upper())
    m = re.fullmatch(rf"L3M[0O]N26({'|'.join(CATEGORIES)})([A-Z]{{{SUFFIX_LEN}}})", s)
    if not m:
        return None
    code = f"{PREFIX}-{m.group(1)}-{m.group(2)}"
    return code if CODE_RE.match(code) else None


def ordinal(n: int) -> str:
    """1st 2nd 3rd 4th ... 11th 12th 13th ... 21st 22nd 23rd ... 101st 111th."""
    if not isinstance(n, int) or isinstance(n, bool) or n < 1:
        raise ValueError(f"a rank is a whole number from 1: {n!r}")
    tens, ones = n % 100, n % 10
    if 11 <= tens <= 13:
        return f"{n}th"
    suffix = {1: "st", 2: "nd", 3: "rd"}.get(ones, "th")
    return f"{n}{suffix}"
