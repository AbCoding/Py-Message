import pandas as pd
from collections import Counter
from pymessage.text import tokens
from importlib.resources import files

def common_dictionary_path():
    return files('pymessage').joinpath('data/common_words.txt')

def filter_words_from_dataframe(df, dictionary_path=None,BlacklistWords=None):
    """
    Filters out rows from the DataFrame where the 'Word' column contains words present in the dictionary.

    Parameters:
    df (pd.DataFrame): The DataFrame containing 'Word' and 'Frequency' columns.
    dictionary_path (str): The path to the file containing dictionary words.

    Returns:
    pd.DataFrame: The filtered DataFrame.
    """
    # Load English dictionary words into a set
    dictionary_words = set()
    if dictionary_path:
        with open(dictionary_path, 'r', encoding='utf-8') as file:
            dictionary_words = set(word.strip().lower() for word in file)
    if BlacklistWords:
        dictionary_words.update(word.lower() for word in BlacklistWords)

    # Filter out rows from the DataFrame where 'Word' is in the dictionary
    filtered_df = df[~df['Word'].str.lower().isin(dictionary_words)]

    return filtered_df
def preprocess_text(text):
    return list(tokens(text)) if isinstance(text, str) else []

def MakeFrequencyTable(filename,FilterWords=False,dictionary_path=None,BlacklistWords=None):
    df = pd.read_csv(str(filename) + ".csv", keep_default_na=False)
    word_freq = Counter()
    for message in df['Content']:
        word_freq.update(tokens(message) if isinstance(message, str) else ())

    # Create and save DataFrame to CSV
    word_freq_df = pd.DataFrame(word_freq.items(), columns=['Word', 'Frequency'])
    word_freq_df = word_freq_df.sort_values(by='Frequency', ascending=False)

    if FilterWords:
        if dictionary_path is None:
            dictionary_path = common_dictionary_path()
        word_freq_df = filter_words_from_dataframe(word_freq_df, dictionary_path=dictionary_path, BlacklistWords=BlacklistWords)
    elif BlacklistWords:
        word_freq_df = filter_words_from_dataframe(word_freq_df, BlacklistWords=BlacklistWords)
    # Save to CSV
    word_freq_df.to_csv(str(filename) + '_word_frequencies.csv', index=False)
    return word_freq_df
