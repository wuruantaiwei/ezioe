"""Word lists for free-text blanks (frequency ordered, then full coverage)."""

from __future__ import annotations

import os
import re

from .paths import data_dir

DATA = data_dir()

_common: list[str] | None = None
_all: list[str] | None = None
_by_len: dict[int, list[str]] = {}


def _read(name: str) -> list[str]:
    path = os.path.join(DATA, name)
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8", errors="ignore") as fh:
        return [w.strip().lower() for w in fh if w.strip()]


def common() -> list[str]:
    """Frequency ordered words: 50k corpus, then the smaller topic list."""
    global _common
    if _common is None:
        _common = _read("en50k.txt") + _read("en10k.txt")
    return _common


def every() -> list[str]:
    global _all
    if _all is None:
        _all = _read("words_alpha.txt")
    return _all


def _zipf_rank(size: int, words: list[str]) -> list[str]:
    """Re-rank by corpus frequency if `wordfreq` is available (cached on disk)."""
    path = os.path.join(DATA, f"rank_{size}.txt")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            ranked = [w.strip() for w in fh if w.strip()]
        if ranked:
            known = set(ranked)
            return ranked + [w for w in words if w not in known]
    try:
        from wordfreq import zipf_frequency
    except ImportError:
        return words
    scored = sorted(((zipf_frequency(w, "en"), w) for w in words), reverse=True)
    ranked = [w for _, w in scored]
    try:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(chr(10).join(ranked))
    except OSError:
        pass
    return ranked


def by_length(size: int) -> list[str]:
    """Words of exactly `size` letters, most frequent first."""
    if size in _by_len:
        return _by_len[size]
    words: list[str] = []
    seen: set[str] = set()
    for word in common():
        if len(word) == size and word.isalpha() and word not in seen:
            seen.add(word)
            words.append(word)
    for word in every():
        if len(word) == size and word not in seen:
            seen.add(word)
            words.append(word)
    words = _zipf_rank(size, words)
    _by_len[size] = words
    return words


def starting_with(cluster: str, size: int = 0) -> list[str]:
    cluster = cluster.lower()
    out = [w for w in common() if w.startswith(cluster) and len(w) > len(cluster)]
    out += [w for w in every() if w.startswith(cluster) and len(w) > len(cluster) and w not in out]
    return out if not size else [w for w in out if len(w) == size]


_rank: dict[str, int] = {}
_comp: dict[int, list[str]] = {}


def rank() -> dict[str, int]:
    """Frequency index of a word (smaller = more common).

    The corpus is `en50k` (frequency ordered) followed by `en10k` (alphabetical),
    so duplicates must keep the FIRST index - `dict.update` used to overwrite
    "to"/"of"/"in" with their alphabetical position 50k later and every
    frequency comparison was then backwards.
    """
    if not _rank:
        for index, word in enumerate(common()):
            _rank.setdefault(word, index)
    return _rank


def comparatives(size: int) -> list[str]:
    """-er / -est forms of a known adjective, ordered by adjective frequency."""
    if size in _comp:
        return _comp[size]
    ranks = rank()
    found: list[tuple[int, int, str]] = []
    for word in by_length(size):
        base = word[:-2] if word.endswith("er") else word[:-3] if word.endswith("est") else ""
        if not base:
            continue
        own = ranks.get(word)
        for stem in (base, base + "e", base[:-1]):
            base_rank = ranks.get(stem)
            if base_rank is not None and (own is not None or base_rank <= 3000):
                found.append((0 if own is not None else 1, base_rank, word))
                break
    found.sort()
    _comp[size] = [word for _, _, word in found]
    return _comp[size]


COMPARATIVE = re.compile(r"\bthan\b|\bmore\b|\bthe\b[^.]*\*{3,}", re.I)


STOPWORDS = (
    "the a an is are was were be been being it its that than of in on at to so too such more "
    "most as not no very much many his her their they he she you we i me my your our this these "
    "those there here what who which when where how has have had do does did can could will "
    "would should must and but or if"
).split()

ADJECTIVES = (
    "big small tall short long wide narrow deep high low fast slow early late hard soft "
    "cheap expensive cold hot warm cool dry wet clean dirty safe dangerous quiet noisy "
    "busy free easy difficult happy sad angry tired hungry thirsty strong weak young old "
    "new good bad far near heavy light dark bright rich poor healthy unhealthy friendly "
    "polite rude careful careless comfortable convenient modern ancient peaceful crowded "
    "beautiful ugly interesting boring exciting important popular famous fresh green "
    "frequent simple happy lucky funny serious confident nervous calm curious active "
    "creative energetic generous honest kind lazy lovely patient practical reliable"
).split()


def adjective_forms(size: int, base: bool = False) -> list[str]:
    """Adjective forms (base, comparative, superlative) of the given length."""
    vowels = set("aeiou")
    out: list[str] = []
    for word in ADJECTIVES:
        forms = [word]
        if word.endswith("y"):
            forms += [word[:-1] + "ier", word[:-1] + "iest"]
        else:
            forms += [word + "er", word + "est"]
            if len(word) <= 4 and word[-1] not in vowels and word[-2] in vowels:
                forms += [word + word[-1] + "er", word + word[-1] + "est"]
        forms += {"good": ["better", "best"], "bad": ["worse", "worst"], "far": ["farther", "further"]}.get(word, [])
        if base:
            forms = forms[:1]
        for form in forms:
            if len(form) == size and form not in out:
                out.append(form)
    return out


BASE_VERBS = (
    "go come get make take see know think say tell give find use work call try ask need feel "
    "become leave put mean keep let begin seem help talk turn start show hear play run move "
    "live believe bring happen write sit stand lose pay meet include continue set learn "
    "change lead understand watch follow stop create speak read spend grow open walk win "
    "offer remember love consider appear buy wait serve die send expect build stay fall cut "
    "reach kill remain suggest raise pass sell require report decide pull discuss improve "
    "hope develop carry break receive agree support hit produce eat cover catch draw choose "
    "cause point listen realize place close involve increase reduce explain study wear "
    "suffer depend prevent prepare visit travel enjoy prefer practice practise feed harvest "
    "cultivate plant water clean cook wash teach solve answer arrive return finish join "
    "share borrow lend invite protect save spend waste relax improve attend complete "
    "download upload search check repair decorate celebrate organize collect deliver "
).split()


ADVERBS = (
    "sometimes usually often always never rarely seldom occasionally frequently normally "
    "generally hardly already almost also just still even only always together quickly "
    "slowly carefully quietly loudly easily happily sadly early late soon then today "
    "tomorrow yesterday always never twice once again"
).split()


def verb_set() -> set[str]:
    return set(BASE_VERBS)


def verb_forms(size: int) -> list[str]:
    vowels = set("aeiou")
    bases: list[str] = []
    derived: list[str] = []
    for word in BASE_VERBS:
        forms = []
        stem = word
        if word.endswith("e"):
            forms.append(word + "d")
            stem = word[:-1]
        forms.append(word + "s")
        forms.append(stem + "ing")
        forms.append(word + "ed")
        if word.endswith("y"):
            forms.append(word[:-1] + "ies")
            forms.append(word[:-1] + "ied")
        if len(word) <= 4 and word[-1] not in vowels and word[-2] in vowels:
            forms.append(word + word[-1] + "ed")
            forms.append(word + word[-1] + "ing")
        if len(word) == size and word not in bases:
            bases.append(word)
        for form in forms:
            if len(form) == size and form not in derived:
                derived.append(form)
    return bases + derived


def adverbs(size: int) -> list[str]:
    return [w for w in dict_uniq(ADVERBS) if len(w) == size]


_SKIP: set[str] | None = None


def noun_ish(words: list[str]) -> list[str]:
    """Drop stopwords / verbs / adjectives / adverbs, keep noun-looking words."""
    global _SKIP
    if _SKIP is None:
        _SKIP = set(STOPWORDS) | set(BASE_VERBS) | set(ADJECTIVES) | set(ADVERBS)
    return [w for w in words if w not in _SKIP]


def dict_uniq(words: list[str]) -> list[str]:
    out = []
    for word in words:
        if word not in out:
            out.append(word)
    return out
