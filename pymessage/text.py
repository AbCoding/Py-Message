"""Reproducible Unicode tokens; preserved source text is never normalized in place."""
from collections import Counter
from functools import lru_cache
from importlib.resources import files
import re
import unicodedata

TOKENIZER_VERSION = 'unicode-1'
WORD = re.compile(r"[^\W_]+(?:['’][^\W_]+)*", re.UNICODE)
CJK = re.compile(r'[\u3400-\u9fff\u3040-\u30ff\uac00-\ud7af]')


def tokens(text):
    normalized = unicodedata.normalize('NFC', text or '').casefold().replace('’', "'")
    for match in WORD.finditer(normalized):
        token = match.group()
        # Explicit deterministic character segmentation for CJK text. This is
        # character frequency, not a claim of language-specific word boundaries.
        for piece in re.split(r'([\u3400-\u9fff\u3040-\u30ff\uac00-\ud7af])', token):
            if piece:
                yield piece


@lru_cache(maxsize=1)
def common_words():
    return frozenset(files('pymessage').joinpath('data/common_words.txt').read_text(encoding='utf-8').split())


def statistics(text):
    text = text or ''
    frequencies = Counter(tokens(text))
    # Unicode symbol counts are named symbols rather than full emoji grapheme counts.
    symbols = Counter(char for char in text if unicodedata.category(char) == 'So')
    return {'tokens': frequencies, 'word_count': sum(frequencies.values()),
            'word_characters': sum(len(word) * count for word, count in frequencies.items()),
            'characters': len(text), 'symbols': symbols}
