"""Game (bài luyện) layer: open a session, answer, submit.

Flow used by the official client (cocos games served from /lam-bai/<game>/):

    TuLuyen/setinfo  -> urlgame?token=...&key=...
    game/getinfo     -> questions + exam session token
    game/startgame   -> starts the clock
    game/answercheck -> client side validation, `point > 0` means correct
    game/finishgame  -> submit the answer list, the server scores it
    TuLuyen/comfirmresult -> persist the whole round

Two quirks of the gateway are handled here:

* ``answercheck`` scores the submitted value (0..Point), so a wrong option can
  simply be retried - that is the "answers are validated on the client" hole.
* ``finishgame`` rejects a submission ("Bài làm không hợp lệ") when ``totalTime``
  is shorter than the real elapsed time since ``startgame``, therefore the
  clock is measured and the request is padded.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import re
import random
import threading
import time
import urllib.parse
import uuid

from . import asr, dicts
from . import api, schema
from .api import AUTH_ERRORS, ApiError, Client
from .words import WORDS

GAME_ROOT = api.GAME_ROOT  # giữ tên cũ cho test cũ; thực tế dùng api.game_root()

# the answer bank is shared by every account running in parallel
BANK_LOCK = threading.Lock()

# QuestionDTO.type values seen in the wild
T_FILL = 3      # words dragged into a masked sentence / paragraph
T_PAIR = 7      # matching pairs (left card | right card)
T_CHOICE = 10   # multiple choice, usually listening
AI_CONFIG: dict = {}


def use_ai_hints(config: dict) -> None:
    """Bật/tắt phần gợi ý bằng AI cho cả tiến trình (cli gọi lúc bắt đầu)."""
    AI_CONFIG.clear()
    AI_CONFIG.update(config or {})


T_SPEAK = 12    # AI scored pronunciation (word)
T_SPEAK_TEXT = 13  # AI scored pronunciation (sentence) - cùng hợp đồng với T_SPEAK
SPEAK_TYPES = (T_SPEAK, T_SPEAK_TEXT)
T_ORDER = 5     # words put back into a sentence
T_DICTATION = 1 # listen and type what you hear
T_SHAPE = 2     # fill the missing letters of a word

# the submission is rejected when totalTime looks too fast
MIN_EXAM_SECONDS = 18.0
# turbo keeps one exercise (all questions + submit) inside this budget
TURBO_SECONDS = 24.0
# never let a single attempt run longer than this (10 minutes)
MAX_EXAM_SECONDS = 540.0

STATUS_NOT_STARTED = 0
STATUS_CAN_PLAY = 1
STATUS_FINISHED = 2
STATUS_LOCKED = 3


def qkey(question: dict) -> str:
    text = question["content"]["content"]
    if question["type"] == T_SHAPE:
        # nội dung chỉ là "******": lấy cả câu để không lẫn giữa các câu
        text = gap_sentence(question)[0] or text
    raw = f"{question['type']}|{text}".encode()
    return hashlib.sha1(raw).hexdigest()[:16]


def _ordered(question: dict) -> list[str]:
    """Answer words of a fill question, in the order of the sentence.

    ``numTChar`` is the length of the FIRST blank, not the number of blanks, so
    the answer list is only trimmed when it really has more words than blanks
    (otherwise a 5-blank passage lost its last answer and the whole exercise
    scored 0).
    """
    ans = question.get("ans") or []
    ordered = sorted(ans, key=lambda a: a.get("orderTrue") or 0)
    holes = len(_masks(str((question.get("Description") or {}).get("content") or "")))
    limit = question.get("numTChar") or 0
    if holes and holes == len(ordered):
        return [a["content"] for a in ordered]
    if 0 < limit < len(ordered):
        ordered = ordered[:limit]
    return [a["content"] for a in ordered]


def _join(question: dict, words: list[str]) -> str:
    if question["type"] == T_PAIR:
        left = question["content"]["content"]
        return f"{left}|{words[0]}" if words else ""
    return "|".join(words)


STOPWORDS = (
    "the a an is are was were be been being it its that than of in on at to so "
    "too such more most as not no very much many his her their they he she you we "
    "i me my your our this these those there here what who which when where how "
    "has have had do does did can could will would should must and but or if"
).split()

VOWELS = set("aeiou")


def _inflections(word: str) -> list[str]:
    """Cheap morphological derivatives of a content word."""
    low = word.lower()
    out = []
    if len(low) < 3 or not low.isalpha():
        return out
    if low.endswith("y"):
        out += [low[:-1] + "ier", low[:-1] + "iest"]
    out += [low + "er", low + "est"]
    if low[-1] not in VOWELS and low[-2] in VOWELS:
        out += [low + low[-1] + "er", low + low[-1] + "est"]
    out += [low + "s", low + "ing", low + "ed"]
    return out


def _masks(template: str) -> list[int]:
    return [len(m) for m in re.findall(r"\*+", template)]


def _pool(question: dict, sizes: list[int]) -> list[list[str]]:
    """Candidate words per mask, best guess first."""
    text = question["content"]["content"]
    words = [w.strip(".,!?;:()'\"").lower() for w in text.split()]
    ordered: list[str] = []
    seen: set[str] = set()
    for word in words:
        forms = [word] if word in STOPWORDS else [word] + _inflections(word)
        for candidate in forms:
            if candidate and candidate not in seen:
                seen.add(candidate)
                ordered.append(candidate)
    for word in STOPWORDS:
        if word not in seen:
            seen.add(word)
            ordered.append(word)
    return [[w for w in ordered if len(w) == size][:9] or
            [w for w in ordered if abs(len(w) - size) < 2][:9] for size in sizes]


def template_candidates(question: dict, limit: int = 120) -> list[str]:
    """Rewrite items: the answer hides in the masked template of `Description`."""
    template = str((question.get("Description") or {}).get("content") or "")
    sizes = _masks(template)
    if not sizes or len(sizes) > 5:
        return []
    pools = []
    for size in sizes:
        graded = _ranked_slots(list(_pool(question, [size])[0]) + dicts.by_length(size),
                               size, template, _context(question))
        if not graded:
            return []
        pools.append(graded[:8])
    combos = itertools.product(*pools)
    out = []
    for combo in itertools.islice(combos, limit):
        out.append("|".join(combo))
    return out


def _audio_url(question: dict) -> str:
    desc = question.get("Description") or {}
    url = str(desc.get("content") or "")
    return url if url.startswith("http") and ".mp3" in url.lower() else ""


def heard_candidates(question: dict) -> list[str]:
    """What the recording says - the whole point of the listening games."""
    url = _audio_url(question)
    if not url or not asr.available():
        return []
    text = asr.transcribe(url)
    if not text:
        return []
    if question["type"] == T_DICTATION or "*" not in question["content"]["content"]:
        return [text]
    words = asr.masked_answers(question, text)
    out: list[str] = []
    for word in words:
        if word not in out:
            out.append(word)
    return out


GAP = re.compile(r"\*+|_{2,}")


def gap_sentence(question: dict) -> tuple[str, int]:
    """Câu có chỗ trống, chỗ trống đưa về đúng số dấu `*` cần điền.

    Game điền từ giấu cả từ bằng dấu `*`, nhưng nhiều đề chỉ để `___` trong
    `Description` rồi trả `content` rỗng: độ dài lấy từ `numTChar`.
    """
    desc = str((question.get("Description") or {}).get("content") or "")
    own = str(question["content"]["content"] or "")
    # Description có thể là URL file nghe: chỉ dùng khi nó thật sự có chỗ trống
    text = desc if (desc and GAP.search(desc)) else own
    hit = GAP.search(text)
    length = int(question.get("numTChar") or 0)
    if not hit:
        return text, length
    if " " not in text.strip():                 # cả nội dung chỉ là một từ bị giấu
        return text, len(text.strip())
    if not length:
        length = hit.end() - hit.start()
    return text[:hit.start()] + "*" * length + text[hit.end():], length


def _context(question: dict) -> tuple[str, str]:
    text, _length = gap_sentence(question)
    if "*" not in text:
        return "", ""
    parts = text.split("*", 1)
    before = parts[0].rstrip().split()[-1].lower().strip(".,!?;:()\"'") if parts[0].strip() else ""
    tail = [w.strip("*") for w in parts[1].strip().split()[:2]] if len(parts) > 1 else []
    after = " ".join(w for w in (t.lower().strip(".,!?;:()\"'") for t in tail) if w)
    return before, after


DETERMINERS = {"a", "an", "the", "this", "that", "my", "his", "her", "their", "our", "its"}

def _hinted(words: list[str], context: tuple[str, str]) -> list[str]:
    """Words that fit the grammatical slot, by plain suffix heuristics."""
    before, after = context
    tokens = [t for t in after.split() if t not in ("and", "or")]
    plural = any(t.endswith("s") and not t.endswith(("ss", "us", "is")) for t in tokens)
    singular = before in DETERMINERS and (not after or after.split()[0] in ("is", "was", "and", "or"))
    third = after in ("and", "or") and before.endswith("s") and before not in DETERMINERS
    out = []
    for word in dicts.noun_ish([w.lower() for w in words]) if plural else words:
        low = word.lower()
        if plural and low.endswith("s"):
            out.append(low)
        elif singular and not low.endswith("s"):
            out.append(low)
        elif third:
            out.append(low)
    return out


MASK = re.compile(r"[*_]+")


AUX_BEFORE = {
    "is", "are", "was", "were", "has", "have", "had", "can", "could", "will", "would",
    "should", "must", "do", "does", "did", "to", "not", "been", "being", "isn't", "wasn't",
    "don't", "doesn't", "didn't", "can't", "won't", "let", "lets", "may", "might",
}
COMPARATIVE_HINT = re.compile(r"\b(than|more|most|less|least)\b", re.I)
INTENSIFIERS = {"very", "too", "so", "really", "quite", "extremely", "fairly", "rather", "pretty"}
ADJ_BASES = set(dicts.ADJECTIVES)
PREPOSITIONS = {"in", "on", "at", "by", "for", "of", "to", "from", "with", "about", "after",
                "before", "during", "until", "since", "under", "over", "through", "across",
                "into", "near", "between", "against", "off", "up", "down", "out", "around",
                "along", "behind", "beside", "within", "without", "per"}
ARTICLES = {"the", "a", "an"}
PRE_NOMINAL = DETERMINERS | {"one", "two", "some", "any", "each", "every", "no", "another",
                             "both", "all", "several", "many", "much", "few"}
PERFECT = {"has", "have", "had"}
BE = {"is", "are", "was", "were", "been", "being"}
MODALS = {"will", "would", "can", "could", "should", "must", "may", "might", "shall",
          "do", "does", "did", "to", "not", "let", "lets"}
SUBJECTS = {"i", "you", "we", "they", "he", "she", "it", "people", "someone", "nobody",
            "everyone", "somebody", "anyone", "who", "that", "which"}
# "wouldn", "isn", ... are contractions the corpus stores without the apostrophe
CONTRACTIONS = {"wouldn", "couldn", "shouldn", "doesn", "didn", "isn", "aren", "wasn", "weren",
                "hasn", "haven", "hadn", "mustn", "needn", "won", "ain", "cannot", "gonna"}
PARTICIPLES = {
    "bought", "brought", "built", "caught", "chosen", "cut", "done", "driven", "eaten", "found",
    "given", "gone", "grown", "heard", "held", "kept", "known", "left", "lost", "made", "meant",
    "met", "paid", "put", "read", "risen", "said", "seen", "sent", "shown", "sold", "spent",
    "stolen", "taken", "taught", "told", "thought", "thrown", "understood", "won", "written",
}


def _comparative_form(word: str) -> bool:
    """"-er"/"-est" of an adjective we know (not 'heller' from 'hell')."""
    for suffix in ("est", "er"):
        if word.endswith(suffix):
            base = word[:-len(suffix)]
            if any(stem in ADJ_BASES for stem in (base, base + "e", base[:-1])):
                return True
    return False
PREP_AFTER = {"in", "on", "at", "of", "for", "from", "with", "to", "during", "after", "before",
              "by", "about", "because", "when", "while", "as", "than", "over", "under", "into",
              "near", "around", "through", "across", "between", "against"}


def _freq(word: str) -> int:
    """Corpus rank of a word - 10^7 when the corpus does not know it."""
    return dicts.rank().get(word.lower(), 10 ** 7)


def _slot_scorer(size: int, text: str, context: tuple[str, str]):
    """Score how well a word of `size` letters fits the empty slot.

    Grammar only ranks (it never drops words): the oracle still decides, so a
    wrong guess merely costs one request. The point is that a real word such as
    "noted" beats the machine-made inflection "noter".
    """
    before, after = context
    next_word = (after.split() or [""])[0]
    verbs = set(dicts.verb_forms(size))
    adverbs = dicts.adverbs(size)
    adjectives = set(dicts.adjective_forms(size))
    comparatives = set(dicts.comparatives(size))
    comparative = bool(COMPARATIVE_HINT.search(text)) or before in {"more", "most", "less", "least"}
    in_text = set(re.findall(r"[a-z]+", text.lower()))   # IOE reuses the exercise words
    after_nounish = bool(next_word) and bool(dicts.noun_ish([next_word]))
    determiner = before in DETERMINERS
    verbs_all = dicts.verb_set()
    # chỗ trống là động từ: "surfaces ___ the air quality", "They ___ football"
    verb_slot = (before in SUBJECTS or bool(next_word) and next_word in DETERMINERS | PRE_NOMINAL
                 and bool(before) and bool(dicts.noun_ish([before])))
    # chỗ trống là tính từ: "use ___ resources", "a ___ story"
    adj_slot = bool(before) and before in verbs_all and after_nounish and not determiner
    noun_slot_2 = next_word in AUX_BEFORE or next_word in verbs_all    # "good ___ can"
    is_adj = lambda w: bool(w) and w in dicts.adjective_forms(len(w))   # noqa: E731
    noun_after_adj = is_adj(before) or before in PREPOSITIONS          # "great ___ for"

    def score(word: str) -> int:
        low = word.lower()
        points = 0
        if comparative:
            if low in comparatives or _comparative_form(low):
                points += 8
            elif low in adjectives:
                points += 2
            if low.endswith("ing") or (low in verbs and low not in adjectives):
                points -= 4
        if before in AUX_BEFORE and not comparative:
            base_verb = low in dicts.verb_set()
            participle = low in PARTICIPLES or low.endswith("ed")
            if before in MODALS:
                # "will ____": động từ nguyên mẫu, không phải quá khứ phân từ
                points += 6 if base_verb else 0
                points -= 6 if participle and not base_verb else 0
            elif before in PERFECT or before in BE:
                if participle:
                    points += 6
                elif base_verb:
                    points += 3
                elif before in BE and low in adjectives:
                    points += 2
                elif not after_nounish:    # "is the capital": danh từ theo sau -> giữ
                    points -= 5
            if low.endswith("ly"):
                points -= 3
        if next_word in AUX_BEFORE or next_word in dicts.verb_set():
            points += 4 if low in adverbs or low.endswith("ly") else 0
        if before in INTENSIFIERS or next_word in PREP_AFTER:
            # "very ___", "___ in the mountains": adverb or adjective, not a bare verb
            points += 4 if low in adverbs or low.endswith("ly") else 0
            points += 2 if low in adjectives else 0
            points -= 3 if low in verbs and low not in adjectives and not low.endswith("ing") else 0
        if before in SUBJECTS:
            # "They ______ football": the slot is the verb
            points += 6 if low in verbs or low in PARTICIPLES else 0
            points -= 4 if low in adverbs or low.endswith("ly") else 0
        if low in CONTRACTIONS and before not in AUX_BEFORE:
            points -= 6
        if determiner:
            points += 2 if low not in verbs and not low.endswith(("ed", "ing", "ly")) else 0
        if verb_slot:
            if low in verbs and not low.endswith(("ed", "ing")):
                points += 10
            elif low in verbs or low.endswith(("ed", "ing")):
                points += 4
            points -= 8 if low in PREPOSITIONS or low in adverbs or low.endswith("ly") else 0
        if adj_slot:
            points += 8 if low in adjectives or low.endswith(("ed", "ing")) else 0
            points -= 5 if low in adverbs or low in PREPOSITIONS else 0
        if noun_after_adj:
            points += 8 if low in dicts.noun_ish([low]) else 0
            points -= 4 if low in adverbs or low.endswith("ly") else 0
        if noun_slot_2:
            points += 8 if low in dicts.noun_ish([low]) else 0
            points -= 5 if low in adverbs else 0
        if low == next_word:
            points -= 4
        if low in in_text:
            points += 3
        if size <= 6:
            # "the journey __ time", "___ capital of ..." - chỗ trống là hư từ
            noun_slot = (bool(before) and bool(dicts.noun_ish([before]))
                         and bool(next_word) and bool(dicts.noun_ish([next_word])))
            if noun_slot:
                points += 8 if low in PREPOSITIONS else 0
                points -= 5 if low in SUBJECTS or low in AUX_BEFORE else 0
            if after_nounish and (not before or before in {"is", "are", "was", "were"}):
                    points += 8 if low in ARTICLES else 0
            if next_word in PRE_NOMINAL:
                points += 8 if low in PREPOSITIONS else 0
                points -= 4 if low in SUBJECTS or low in AUX_BEFORE else 0
            if not before and next_word in MODALS:
                # "____ will it strike?" - mở đầu câu hỏi
                points += 8 if low in {"when", "what", "how", "where", "who", "why", "which"} else 0
        return points

    return score


def _ranked_slots(candidates, size: int, text: str, context: tuple[str, str]) -> list[str]:
    """Grammar fit first, then corpus frequency, then alphabetic for stability."""
    score = _slot_scorer(size, text, context)
    unique = list(dict.fromkeys(candidates))
    return sorted(unique, key=lambda word: (-score(word), _freq(word), word))


def _masked_token(text: str) -> str:
    """The word carrying the stars/underscores."""
    for token in text.split():
        if MASK.search(token):
            return token.strip(".,!?;:()\"'")
    return ""


def shape_candidates(question: dict, pool: list[str], limit: int = 60) -> list[str]:
    """Từ còn thiếu của một chỗ trống bị giấu ("BIR*HDAY" -> "t").

    Đề có thể giấu cả từ (`content` toàn dấu `*`) hoặc chỉ để `___` trong câu
    và cho `numTChar`: khi đó phải đoán đúng độ dài, xếp theo ngữ pháp rồi để
    oracle quyết.
    """
    sentence, length = gap_sentence(question)
    own = str(question["content"]["content"] or "").strip()
    one_word = bool(own) and bool(GAP.search(own)) and " " not in own
    token = own if one_word else "*" * length           # "BIR*HDAY" giữ chữ đã cho
    length = len(token) if token else length
    holes = [i for i, ch in enumerate(token) if ch == "*"]
    if not holes or len(holes) > 12 or len(token) != length:
        return []
    pattern = "".join("[a-z]" if ch == "*" else re.escape(ch) for ch in token.lower())
    out: list[str] = []
    seen: set[str] = set()
    before, after = _context(question)
    words = _ranked_slots(list(pool) + dicts.by_length(length), length,
                          sentence or token, (before, after))
    for word in words:
        low = word.lower()
        if len(low) != length or low in seen or not re.fullmatch(pattern, low):
            continue
        seen.add(low)
        if len(holes) == length:                        # giấu cả từ
            out.append(low)
        else:
            letters = "".join(low[i] for i in holes)
            for value in (letters, "|".join(letters), low):
                if value and value not in out:
                    out.append(value)
        if len(out) >= limit:
            break
    return out[:limit]

def pool_words(questions: list[dict], passage: str = "") -> list[str]:
    """Vocabulary hints: words from the exercise itself, then the built-in list."""
    out: list[str] = []
    seen: set[str] = set()
    texts = [passage] if passage else []
    for question in questions:
        for key in ("content", "Description"):
            texts.append(str((question.get(key) or {}).get("content") or ""))
    for text in texts:
        for word in re.split(r"[^A-Za-z']+", text):
            if len(word) < 3:
                continue
            low = word.lower()
            for form in [low] + _inflections(low):
                if form not in seen:
                    seen.add(form)
                    out.append(form)
    for word in WORDS:
        if word not in seen:
            seen.add(word)
            out.append(word)
    return out


def free_candidates(question: dict, limit: int = 90) -> list[str]:
    """Open blanks ("The air is ______ in the mountains than ...") -> word lists."""
    text = str((question.get("Description") or {}).get("content") or "")
    size = question.get("numTChar") or len(_masked_token(text))
    if not size:
        return []
    out: list[str] = []
    seen: set[str] = set()

    def add(word: str) -> None:
        if word not in seen:
            seen.add(word)
            out.append(word)

    if "/" in text:
        cluster = re.search(r"/([a-z]+)/", text)
        if cluster:
            for word in dicts.starting_with(cluster.group(1)):
                add(word)
    sizes = [size, size + 1, size - 1, size + 2]
    parts = MASK.split(text, maxsplit=1)
    before = parts[0].rstrip().split() if parts else []
    after = parts[1].strip().split() if len(parts) > 1 else []
    last = before[-1].strip(".,!?").lower() if before else ""
    nxt = after[0].strip(".,!?").lower() if after else ""
    AUX = {"may", "must", "can", "could", "will", "would", "should", "do", "does", "did",
           "to", "not", "they", "she", "we", "he", "you", "i", "let", "lets"}
    verbish = nxt in dicts.verb_set() or nxt.endswith(("ing", "ed"))
    prepish = nxt in {"in", "than", "at", "of", "for", "from", "on", "with", "to", "during",
                      "after", "before", "by", "about", "because", "when", "while", "as"}
    if verbish:
        for word in dicts.adverbs(size):
            add(word)
    elif nxt in {"the", "a", "an", "my", "his", "her", "their", "our", "some", "these", "those"}             and (last in dicts.verb_set() or last.endswith(("y", "e"))):
        for word in dicts.verb_forms(size):
            add(word)
    elif last in AUX or (last.endswith("s") and last not in
                          {"is", "was", "has", "its", "his", "this", "us", "less", "more", "always"}):
        for word in dicts.verb_forms(size):
            add(word)
    analytic = re.search("(more|less)", text)
    if analytic:
        for value in sizes:
            for word in dicts.adjective_forms(value, base=True):
                add(word)
    if dicts.COMPARATIVE.search(text):
        for value in sizes:
            for word in dicts.adjective_forms(value):
                add(word)
        for value in sizes:
            for word in dicts.comparatives(value):
                add(word)
    for value in sizes:
        for word in dicts.by_length(value):
            add(word)
    return _ranked_slots(out, size, text, (last, nxt))[:limit]


def candidates(question: dict, limit: int = 200, pool: list[str] | None = None) -> list[str]:
    """Values worth submitting for a question, most likely first."""
    answers = [a["content"] for a in question.get("ans") or []]
    if question["type"] == T_FILL:
        words = _ordered(question)
        masks = sizes_of(question)
        if masks and len(masks) != len(words):
            derived = template_candidates(question, limit)
            if derived:
                return derived
        out = [_join(question, words)] if words else []
        if 2 <= len(words) <= 5:
            for perm in itertools.islice(itertools.permutations(words), 1, 60):
                out.append(_join(question, list(perm)))
        return out[:limit]
    if question["type"] == T_SHAPE:
        given = [a["content"] for a in sorted(question.get("ans") or [], key=lambda a: a.get("orderTrue") or 0) if a["content"].strip()]
        if given:
            return given
        sentence, gap = gap_sentence(question)
        masked = bool(sentence and gap)
        if masked:
            wide = 12000 if sentence.count("*") == gap else min(limit, 60)
            return (shape_candidates(question, pool or [], wide)
                    or free_candidates(question, 700))
        return free_candidates(question, 700)
    if question["type"] == T_ORDER:
        words = _ordered(question)
        return ["|".join(words), " ".join(words)] or [question["content"]["content"]]
    if question["type"] == T_DICTATION:
        text = question["content"]["content"]
        if "/" in text:
            return free_candidates(question, 40) or [text]
        if not text:
            return [""]
        return [text, text.rstrip("."), re.sub(r"[^\w\s]", "", text).lower()]
    if not any(o.strip() for o in answers):
        text = question["content"]["content"]
        return [text, text.rstrip(".")] if text else [""]
    if question["type"] == T_PAIR:
        return [_join(question, [a]) for a in answers] or [question["content"]["content"]]
    return answers or [question["content"]["content"]]


def sizes_of(question: dict) -> list[int]:
    return _masks(str((question.get("Description") or {}).get("content") or ""))


def _store(bank: dict, question: dict, answer: str, point: int) -> None:
    entry = {
        "type": question["type"],
        "content": question["content"]["content"],
        "answer": answer,
        "point": point,
        "seen": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    with BANK_LOCK:
        bank[qkey(question)] = entry


class Session:
    """One live exam attempt."""

    def __init__(self, client: Client, exercise_id: int, game_id: int):
        self.client = client
        self.exercise_id = exercise_id
        self.game_id = game_id
        self.token = ""
        self.exam_key = ""
        self.questions: list[dict] = []
        self.urlgame = ""
        self.exam_time = 1200
        self.pool: list[str] = []
        self.passage = ""
        self.game_desc = ""
        self.truth_game = False
        self.schema_drift: list[str] = []
        self.oracle_dead = False

    # ------------------------------------------------------------- lifecycle
    def open(self) -> "Session":
        data = self.client.post(
            "/ioe-service/v3/TuLuyen/setinfo",
            {
                "idExercise": self.exercise_id,
                "round": 0,
                "gameId": self.game_id,
                "redirectUrl": "https://ioe.vn/hoc-sinh/tu-luyen",
            },
        )
        self.urlgame = data["urlgame"]
        short = urllib.parse.parse_qs(urllib.parse.urlparse(self.urlgame).query)["token"][0]

        info = self._game(
            "getinfo",
            {
                "api_key": "string",
                "token": short,
                "gameId": self.game_id,
                "Level": "8",
                "Round": "1",
                "deviceId": "string",
                "sign": "string",
            },
        )
        self.token = info["token"]
        self.exam_key = info["game"]["examKey"]
        self.questions = info["game"]["question"]
        self.passage = str((info["game"].get("Subject") or {}).get("content") or "")
        self.game_desc = str(info.get("gameDesc") or "")
        self.truth_game = "true or false" in self.game_desc.lower() or "true or false" in self.game_desc.lower()
        self.pool = pool_words(self.questions, self.passage)
        self.exam_time = info.get("examTime") or 1200
        drift = schema.record("getinfo", info)
        if drift:
            self.schema_drift = drift[:6]
        return self

    def start(self) -> None:
        self._game("startgame", {**self._base(), "gameId": self.game_id})

    def _base(self) -> dict:
        return {
            "api_key": "string",
            "serviceCode": "IOE",
            "token": self.token,
            "examKey": self.exam_key,
            "IPClient": "string",
            "deviceId": "string",
            "gameId": self.game_id,
        }

    def _game(self, action: str, body: dict) -> dict:
        resp = self.client._request(api.game_root() + action, body, referer="https://ioe.vn/")
        if resp.get("code") in AUTH_ERRORS and self.client.refresh_token:
            self.client.refresh()
            body = {**body, "token": body.get("token") or self.token}
            resp = self.client._request(api.game_root() + action, body, referer="https://ioe.vn/")
        if resp.get("code"):
            raise ApiError(resp.get("code", 0), resp.get("message", action), resp)
        return resp.get("data") or {}

    # ---------------------------------------------------------------- answers
    def check(self, question: dict, answer: str, position: int = 1,
              exam_count: int | None = None) -> int:
        ans = {"ans": answer, "point": question["Point"], "questId": question["id"],
               "position": position}
        if exam_count is not None:
            ans["examCount"] = exam_count
        data = self._game("answercheck", {**self._base(), "ans": ans})
        return int(data.get("point") or 0)

    def submit(self, answers: list[dict], seconds: int) -> dict:
        body = {**self._base(), "ans": answers, "totalTime": seconds}
        resp = self.client._request(api.game_root() + "finishgame", body, referer="https://ioe.vn/")
        if resp.get("code") in AUTH_ERRORS and self.client.refresh_token:
            self.client.refresh()
            resp = self.client._request(api.game_root() + "finishgame", body, referer="https://ioe.vn/")
        return resp

    def sweep(self, question: dict, values: list[str], position: int,
              deadline: float, workers: int = 10) -> tuple[str, int]:
        """Thử nhiều đáp án song song (câu điền-từ giấu cả từ: phải dò cả danh sách).

        Chỉ dùng cho danh sách dài; câu thường vẫn đi tuần tự như trước.
        """
        import concurrent.futures

        best = values[0] if values else ""
        best_point = 0
        if not values:
            return best, 0

        def attempt(value: str) -> tuple[str, int]:
            try:
                return value, self.check(question, value, position)
            except ApiError as exc:
                if exc.code in AUTH_ERRORS or exc.code in (0, None):
                    raise
                return value, 0

        def done() -> tuple[str, int]:
            if best_point > 0:               # gửi lại đáp án đúng để server lưu nó
                try:
                    self.check(question, best, position)
                except ApiError:
                    pass
            return best, best_point

        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
            for start in range(0, len(values), workers * 3):
                batch = values[start:start + workers * 3]
                futures = [pool.submit(attempt, value) for value in batch]
                hit = False
                for future in concurrent.futures.as_completed(futures):
                    value, point = future.result()
                    if point > best_point:
                        best, best_point = value, point
                    hit = hit or best_point >= question["Point"]
                if hit:
                    # batch đã gửi xong hết: gửi lại đáp án đúng để nó là bản ghi cuối
                    return done()
                if time.time() > deadline:
                    break
        return done()

    def answer(self, question: dict, position: int, bank: dict, budget: float = 45.0,
               use_oracle: bool = True) -> tuple[str, int]:
        """Chọn đáp án. Không có oracle thì vẫn trả về đoán tốt nhất (điểm 0).

        Khi oracle chết giữa chừng (IOE vá sang chấm server-side), session tự
        tắt oracle cho các câu sau để không phí 1 request mỗi câu.
        """
        known = (bank.get(qkey(question)) or {}).get("answer")
        heard = heard_candidates(question)
        choices = [a["content"] for a in question.get("ans") or [] if a["content"].strip()]
        if heard and len(choices) > 1:
            # listening questions offer options: let the audio rank them
            values = asr.rank_options(question, choices, heard[0])
        elif heard:
            values = heard[:2]
        else:
            values = candidates(question, pool=self.pool)
        if self.truth_game and question["type"] == T_DICTATION:
            values = ["True", "False"]
        if known:
            values = [known] + [v for v in values if v != known]
        if len(values) > 24 and AI_CONFIG.get("aihint"):
            # chỗ trống bị giấu cả từ: AI gợi ý vài từ, oracle vẫn chấm từng từ
            from . import aihint

            hints = aihint.candidates(question, AI_CONFIG, skip=values)
            if hints:
                values = hints + values
        best, best_point = values[0] if values else "", 0
        if not use_oracle or self.oracle_dead:
            return best, 0
        deadline = time.time() + budget
        if len(values) > 24:                 # dò cả danh sách: chạy song song cho nhanh
            return self.sweep(question, values, position, deadline)
        for value in values:
            try:
                point = self.check(question, value, position)
            except ApiError as exc:
                if exc.code in AUTH_ERRORS or exc.code in (0, None):
                    raise
                self.oracle_dead = True
                return best, 0
            if point > best_point:
                best, best_point = value, point
            if point >= question["Point"]:
                break
            if len(values) <= 8:
                time.sleep(random.uniform(0.12, 0.25))
            if time.time() > deadline:
                break
        return best, best_point


# ---------------------------------------------------------------------- round
ROUND_PATH = "/ioe-service/v3/TuLuyen/getlistGameAndResultMax"


def round_payload(client: Client) -> dict:
    """One call gives both the open round's games and the round counters.

    ``lstgames``/``bilInfo``/``WaitConfirm`` all live inside ``roundInfoRes``;
    reading ``bilInfo`` from the top level silently yields ``{}`` and makes the
    tool believe no round is open (that is what made a fresh account look
    "finished" while ``current_round < sysRound``).
    """
    data = client.post(ROUND_PATH, {"serviceCode": "IOE"}) or {}
    schema.record("round", data)
    res = data.get("roundInfoRes") or {}
    return {
        "games": res.get("lstgames") or [],
        "info": res.get("bilInfo") or {},
        "wait_confirm": bool(res.get("WaitConfirm")),
    }


CLOSED_RE = re.compile(
    r"từ\s+(\d{1,2})/(\d{1,2})/(\d{4})\s+(\d{1,2}):(\d{2}):(\d{2})"
    r"\s*-\s*(\d{1,2})/(\d{1,2})/(\d{4})\s+(\d{1,2}):(\d{2}):(\d{2})", re.I)


def closed_window(payload_or_error) -> tuple[float, float] | None:
    """Khoảng thời gian IOE đóng tính năng Tự luyện, nếu thông báo có kèm."""
    message = getattr(payload_or_error, "message", "") or str(payload_or_error or "")
    match = CLOSED_RE.search(message)
    if not match:
        return None
    part = [int(value) for value in match.groups()]

    def stamp(offset: int) -> float:
        day, month, year, hour, minute, second = part[offset:offset + 6]
        return time.mktime((year, month, day, hour, minute, second, 0, 0, -1))

    return stamp(0), stamp(6)


def round_info(client: Client) -> dict:
    """`current_round` (how far THIS account got) vs `sysRound` (released)."""
    return round_payload(client)["info"]


def rounds_left(info: dict) -> int:
    """How many rounds of this account are already open and can be played."""
    if not info:
        return 0
    return max(0, int(info.get("sysRound") or 0) - int(info.get("current_round") or 0))


def list_exercises(client: Client) -> list[dict]:
    return round_payload(client)["games"]


def pending_exercises(client: Client) -> list[dict]:
    """Rank exercises of the open round that still need work."""
    return [e for e in list_exercises(client)
            if e.get("isRank") and e["Status"] not in (STATUS_FINISHED, STATUS_LOCKED)]


def confirm_round(client: Client) -> dict:
    return client.post("/ioe-service/v3/TuLuyen/comfirmresult", {})


def cancel_round(client: Client) -> dict:
    return client.post("/ioe-service/v3/TuLuyen/cancelresult", {})


def play_exercise(
    client: Client,
    exercise: dict,
    bank: dict,
    *,
    min_seconds: float = 1.8,
    delay: tuple[float, float] = (0.3, 0.9),
    pace: tuple[float, float] = (60.0, 300.0),
    attempts: int = 3,
    turbo: bool = False,
    dry_run: bool = False,
    speaking: dict | None = None,
    use_oracle: bool = True,
) -> dict:
    """Solve one exercise and submit it, retrying while the attempt fails.

    The whole exercise gets one random time budget (``pace``, capped below
    ``MAX_EXAM_SECONDS``) that is shared by the retries, the pauses and the
    answer search, so a single exercise never drags on.
    """
    if turbo:
        # turbo = one short pass so the whole exercise lands in ~25s
        pace = (TURBO_SECONDS, TURBO_SECONDS + 8.0)
        attempts = 1
    elif _is_search(client, exercise):
        attempts = 1
    hard_stop = time.time() + MAX_EXAM_SECONDS
    total = max(1, attempts)
    result: dict = {}
    for done in range(total):
        left = hard_stop - time.time()
        if left < 25:
            break
        share = left / (total - done)
        budget = min(random.uniform(*pace), share)
        if total == 1 and not turbo:
            # a single long attempt is only worth it outside turbo
            budget = min(left, max(budget, random.uniform(240.0, 470.0)))
        budget = max(20.0, budget)
        result = _attempt(client, exercise, bank, min_seconds=min_seconds,
                          delay=delay, deadline=time.time() + budget,
                          turbo=turbo, dry_run=dry_run, speaking=speaking)
        if result.get("isPass") or result.get("point") == result.get("examScore"):
            break
    return result


# ------------------------------------------------------------------- speaking
FTECH_INIT = "https://fcloud-api-gateway.ftech.ai/auth/sdk/init"
FTECH_STREAM = "https://fcloud-streaming-socket-api.ai.ftech.ai/ioe/start_stream_v3"
FTECH_STOP = "https://fcloud-streaming-socket-api.ai.ftech.ai/ioe/stop_stream_v3"
FTECH_VAD = "https://vad-api.ai.ftech.ai/vad-file"
FTECH_APP = {"app_id": "100034", "secret_key": "264a5ead3a63c4f33f5e337d192fb605"}
SPEAK_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")


def _http(url: str, data: bytes | None = None, headers: dict | None = None,
          timeout: int = 60) -> tuple[int, bytes]:
    import urllib.error
    import urllib.request

    request = urllib.request.Request(url, data=data, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()[:200]


def _multipart(fields: dict, name: str, blob: bytes) -> tuple[bytes, str]:
    boundary = "----ezioe" + uuid.uuid4().hex
    out = b""
    for key, value in fields.items():
        out += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{key}\"\r\n\r\n"
                f"{value}\r\n").encode()
    out += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
            f"filename=\"{name}\"\r\nContent-Type: audio/wav\r\n\r\n").encode()
    out += blob + f"\r\n--{boundary}--\r\n".encode()
    return out, f"multipart/form-data; boundary={boundary}"


def ftech_token() -> str:
    """Token của dịch vụ chấm phát âm (chung cho mọi tài khoản IOE)."""
    _status, body = _http(FTECH_INIT, json.dumps(FTECH_APP).encode(),
                          {"Content-Type": "application/json", "Origin": "https://ioe.vn",
                           "User-Agent": SPEAK_UA})
    return json.loads(body)["data"]["token"]


def speak_answer(question: dict, voice: str = "", token: str = "",
                 game_token: str = "", alt_voice: str = "") -> tuple[str, int]:
    """Nộp audio cho một câu nói, trả (request_id, điểm chấm của AI).

    Hợp đồng (dò từ SDK của IOE, 19/11/2026):
      0. `extra_data` phải đúng **game token** (JWT): server dùng nó gắn bản ghi
         với bài làm, thiếu thì mọi câu trả `point: 0` dù AI chấm 99.
      1. `POST /ioe/start_stream_v3` {reference_text, english_accent, extra_data}
         -> {request_id, minio_link}
      2. `POST /vad-api.../vad-file` (multipart file) -> {is_silence}
      3. `POST /ioe/stop_stream_v3` (multipart file + request_id + extra_data)
         -> {data:{score_data:{accuracy_score}}}
      4. `answercheck` với `ans` = {questId, examCount, ans: request_id, position}

    `ans` **không phải** audio base64 mà là `request_id` phía server. Game cho
    ghi 2 lần nên `alt_voice` cho phép thu 2 bản và giữ bản điểm cao hơn.
    """
    from . import tts

    text = str(question["content"]["content"] or "").strip()
    if not text:
        return "", 0
    extra = game_token or "{}"          # multipart bỏ field rỗng -> lỗi 4004
    bearer = token or ftech_token()
    best: tuple[str, int] = ("", 0)
    for option in (voice, alt_voice):
        if not option and best[0]:
            continue
        got = _record(text, option, extra, bearer)
        if got[1] > best[1]:
            best = got
    return best


def _record(text: str, voice: str, extra: str, bearer: str) -> tuple[str, int]:
    from . import tts

    wav = tts.speak(text, voice)
    if not wav:
        return "", 0
    auth = {"Origin": "https://ioe.vn", "User-Agent": SPEAK_UA,
            "Authorization": "Bearer " + bearer}
    _status, body = _http(FTECH_STREAM, json.dumps({
        "reference_text": text, "english_accent": "en-us", "extra_data": extra}).encode(),
        {"Content-Type": "application/json", **auth})
    try:
        request_id = (json.loads(body).get("data") or {}).get("request_id") or ""
    except ValueError:
        return "", 0
    if not request_id:
        return "", 0
    vad_body, vad_type = _multipart({}, request_id + ".wav", wav)
    _http(FTECH_VAD, vad_body, {"Content-Type": vad_type, **auth})      # im lặng vẫn gửi
    body_bytes, content_type = _multipart({
        "request_id": request_id, "reference_text": text, "extra_data": extra,
        "english_accent": "en-us"}, request_id + ".wav", wav)
    _status, body = _http(FTECH_STOP, body_bytes, {"Content-Type": content_type, **auth})
    try:
        score = ((json.loads(body).get("data") or {}).get("score_data") or {}).get("accuracy_score")
    except ValueError:
        score = 0
    return request_id, int(score or 0)


def _is_search(client: Client, exercise: dict) -> bool:
    """Exams whose blanks must be found in a dictionary need one long pass."""
    try:
        probe = Session(client, exercise["idexercise"], exercise["gameId"]).open()
    except ApiError:
        return False
    return any(
        q["type"] == T_SHAPE and "*" in q["content"]["content"]
        and not any(a["content"].strip() for a in q.get("ans") or [])
        for q in probe.questions
    )


def _attempt(
    client: Client,
    exercise: dict,
    bank: dict,
    *,
    min_seconds: float,
    delay: tuple[float, float],
    deadline: float,
    turbo: bool = False,
    dry_run: bool = False,
    speaking: dict | None = None,
    use_oracle: bool = True,
) -> dict:
    """One answer pass. ``deadline`` bounds both the search and the pauses."""
    session = Session(client, exercise["idexercise"], exercise["gameId"]).open()
    session.start()
    # vocabulary already met in earlier exercises helps ranking a lot
    seen = [w.lower() for entry in bank.values() for w in re.findall(r"[A-Za-z]+", entry.get("answer", ""))]
    if seen:
        session.pool = seen + session.pool
    started = time.time()
    speak_token = ""
    if speaking and speaking.get("submit") and any(q["type"] in SPEAK_TYPES for q in session.questions):
        try:
            speak_token = ftech_token()
        except Exception:                                        # noqa: BLE001
            speak_token = ""
    questions = session.questions
    step = max(0.0, deadline - started) / max(1, len(questions))

    answers: list[dict] = []
    for index, question in enumerate(questions, 1):
        remaining_q = len(questions) - index + 1
        room = deadline - time.time()
        search = max(4.0, min(30.0, room / remaining_q + 2.0))
        if turbo:
            search = 6.0
        if question["type"] == T_SHAPE:
            sentence, gap = gap_sentence(question)
            if sentence.count("*") == gap:
                search = max(search, 400.0)     # phải dò cả từ: cần nhiều lượt thử
        if speaking and speaking.get("submit") and question["type"] in SPEAK_TYPES:
            value, point = speak_answer(question, str(speaking.get("voice") or ""),
                                        speak_token, session.token,
                                        str(speaking.get("alt_voice") or ""))
            if value:                           # báo server biết đã nộp câu này
                try:
                    point = session.check(question, value, index, exam_count=1) or point
                except ApiError:
                    pass
        else:
            value, point = session.answer(question, index, bank, budget=search,
                                          use_oracle=use_oracle)
        if value and point > 0:
            _store(bank, question, value, point)
        if question["type"] in SPEAK_TYPES and speaking and speaking.get("submit"):
            answers.append({"ans": value, "point": point, "questId": question["id"],
                            "position": index, "examCount": 1})
        else:
            answers.append({"ans": value, "point": point, "questId": question["id"],
                            "position": index})
        if index < len(questions):
            pause = random.uniform(delay[0], delay[1]) + random.uniform(step * 0.5, step * 1.4)
            left = deadline - time.time()
            time.sleep(max(0.0, min(pause, max(0.0, left))))

    if answers:
        wanted = max(min_seconds * len(answers), MIN_EXAM_SECONDS)
        remaining = min(wanted, deadline) - (time.time() - started)
        if remaining > 0:
            time.sleep(remaining)
    elapsed = int(time.time() - started) + 1
    if dry_run:
        return {"point": 0, "examScore": 0, "isPass": True, "dry_run": True,
                "questions": [{"content": q["content"]["content"], "answer": a["ans"]}
                              for q, a in zip(questions, answers)]}

    result = session.submit(answers, elapsed)
    if result.get("code") != 0:
        raise ApiError(result.get("code", 0), result.get("message", "submit failed"))
    return result.get("data") or {}


def next_round_start(client: Client) -> float | None:
    """Unix time when the next round opens (None while work is still open)."""
    try:
        payload = round_payload(client)
    except ApiError:
        return None
    if any(e.get("isRank") and e["Status"] not in (STATUS_FINISHED, STATUS_LOCKED)
           for e in payload["games"]):
        return None
    for row in client.schedule():
        if row.get("status") == 3 and row.get("startDate"):
            try:
                import datetime as _dt

                return _dt.datetime.fromisoformat(row["startDate"]).timestamp()
            except ValueError:
                continue
    return None


def solve_round(
    client: Client,
    bank: dict,
    *,
    only: int | None = None,
    delay: tuple[float, float] = (0.3, 0.9),
    pace: tuple[float, float] = (60.0, 300.0),
    attempts: int = 3,
    turbo: bool = False,
    dry_run: bool = False,
    speaking: dict | None = None,
    include_optional: bool = False,
    use_oracle: bool = True,
    on_event=None,
) -> list[dict]:
    """Play the rank exercises of the open round, then persist the result."""
    exercises = list_exercises(client)
    if only is None:
        exercises = [e for e in exercises
                     if e.get("isRank") or include_optional]
    else:
        exercises = [e for e in exercises if e["idexercise"] == only]
    targets = {e["idexercise"] for e in exercises if e.get("isRank")}
    results = []
    for position, exercise in enumerate(exercises, 1):
        if exercise["Status"] in (STATUS_FINISHED, STATUS_LOCKED) and only is None:
            if on_event:
                on_event("skip", exercise, None)
            continue
        if on_event:
            on_event("start", exercise, position)
        started = time.time()
        try:
            data = play_exercise(client, exercise, bank, delay=delay, pace=pace,
                                 attempts=attempts, turbo=turbo, dry_run=dry_run,
                                 speaking=speaking, use_oracle=use_oracle)
            try:
                from . import report

                report.add_exercise(getattr(client, "username", ""), exercise, data,
                                    time.time() - started)
            except Exception:
                pass
        except ApiError as exc:
            if on_event:
                on_event("failed", exercise, exc)
            continue
        results.append({"exercise": exercise, "result": data})
        if on_event:
            on_event("done", exercise, data)
    if only is None:
        # the server swaps in the NEXT round's games as soon as this one is done,
        # so "finished" has to be judged on the ids we actually played.
        left = [e for e in list_exercises(client)
                if e["idexercise"] in targets
                and e["Status"] not in (STATUS_FINISHED, STATUS_LOCKED)]
        if left:
            if on_event and not dry_run:      # dry-run never submits
                on_event("unfinished", left[0], len(left))
        else:
            try:
                confirm_round(client)
            except ApiError as exc:
                if on_event:
                    on_event("confirm_failed", None, exc)
    return results


def dump_bank(bank: dict) -> str:
    return json.dumps(bank, ensure_ascii=False, indent=1, sort_keys=True)

