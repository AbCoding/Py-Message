"""Unicode sequence fixtures; no network or message database needed."""
from importlib.resources import files
import json
import unittest

from pymessage.emoji import context_term, context_tokens
from pymessage.semantic import words


class EmojiTests(unittest.TestCase):
    def test_picker_catalog_matches_supported_sequences(self):
        data = json.loads(files('pymessage').joinpath('web/emoji-picker.json').read_text(encoding='utf-8'))
        supported = {line for line in files('pymessage').joinpath('data/emoji-17.txt').read_text(encoding='utf-8').splitlines()
                     if line and not line.startswith('# ')}
        self.assertEqual({item['emoji'] for item in data['emoji']},supported)
        self.assertEqual(len(data['emoji']),len(supported))
        self.assertTrue(all(item['name'] and item['group'] for item in data['emoji']))

    def test_sequences_stay_whole_and_keep_word_order(self):
        text = 'Hi😂👍🏽 family 👨‍👩‍👧‍👦 🇺🇸 1️⃣ #️⃣ 👩🏾‍💻 end'
        self.assertEqual(list(context_tokens(text)),
                         ['hi','😂','👍🏽','family','👨‍👩‍👧‍👦','🇺🇸','1️⃣','#️⃣','👩🏾‍💻','end'])

    def test_presentation_variants_normalize_but_skin_tones_do_not(self):
        self.assertEqual(context_term('❤'),context_term('❤️'))
        self.assertEqual(context_term('❤︎'),context_term('❤️'))
        self.assertNotEqual(context_term('👍'),context_term('👍🏽'))
        self.assertEqual(context_term("I’M"),"i'm")
        self.assertEqual(context_term('CAFE\u0301'),'café')

    def test_tag_flags_and_keycaps(self):
        england = '\U0001f3f4\U000e0067\U000e0062\U000e0065\U000e006e\U000e0067\U000e007f'
        self.assertEqual(list(context_tokens(england+' 🇨🇦 123 1\u20e3')), [england,'🇨🇦','1️⃣'])

    def test_invalid_inputs_are_not_silently_shortened(self):
        for value in ('😂😂','love😂','👍🏽🏽','123','🏽','🇺','',None,'two words','https://example.invalid/😂'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                context_term(value)

    def test_vocabulary_tokenizer_remains_words_only(self):
        self.assertEqual(words('hello 😂 ❤️ family 👨‍👩‍👧‍👦 1️⃣'),['hello','family'])

    def test_every_bundled_sequence_round_trips_as_one_token(self):
        sequences = [line for line in files('pymessage').joinpath('data/emoji-17.txt').read_text(encoding='utf-8').splitlines()
                     if line and not line.startswith('# ')]
        self.assertGreater(len(sequences),3000)
        for sequence in sequences:
            with self.subTest(sequence=sequence):
                self.assertEqual(context_term(sequence),sequence)
                self.assertEqual(list(context_tokens(sequence)),[sequence])


if __name__ == '__main__':
    unittest.main()
