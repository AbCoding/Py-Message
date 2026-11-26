"""Offline Unicode Emoji 17 sequences for word-context tokenization.

Longest matching sequences keep modifiers, flags, keycaps, and ZWJ families
together. Presentation selectors normalize to the fully-qualified display form.
Vocabulary statistics retain their existing word-only tokenizer.
"""
from functools import lru_cache
from importlib.resources import files
import unicodedata

from .text import tokens

CONTEXT_VERSION = 'words-emoji-17-1'


def normalized(text):
    return unicodedata.normalize('NFC',text).casefold().replace('’',"'").replace('\ufe0f','').replace('\ufe0e','')


@lru_cache(maxsize=1)
def emoji_trie():
    root = {}
    for sequence in files('pymessage').joinpath('data/emoji-17.txt').read_text(encoding='utf-8').splitlines():
        if not sequence or sequence.startswith('# '): continue
        branch = root
        for char in normalized(sequence):
            branch = branch.setdefault(char,{})
        branch[None] = sequence
    return root


def context_tokens(text):
    """Yield normalized words and whole emoji in their original order."""
    text = normalized(text or '')
    root = emoji_trie()
    index = start = 0
    while index < len(text):
        branch = root
        end = index
        match = None
        while end < len(text) and text[end] in branch:
            branch = branch[text[end]]
            end += 1
            if None in branch: match = (end,branch[None])
        if match:
            yield from (word for word in tokens(text[start:index]) if any(c.isalpha() for c in word))
            yield match[1]
            start = index = match[0]
        else:
            index += 1
    yield from (word for word in tokens(text[start:]) if any(c.isalpha() for c in word))


def context_term(value):
    if not isinstance(value,str) or not value.strip() or len(value)>80:
        raise ValueError('Choose one word or emoji per entry.')
    found = list(context_tokens(value.strip()))
    if len(found)!=1 or normalized(found[0])!=normalized(value.strip()):
        raise ValueError('Choose one word or emoji per entry; separate selections with commas or spaces.')
    return found[0]
