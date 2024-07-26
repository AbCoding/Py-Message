import pandas as pd
import re
from collections import Counter
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
    if dictionary_path:
        with open(dictionary_path, 'r') as file:
            dictionary_words = set(word.strip().lower() for word in file)
    if BlacklistWords:
        dictionary_words.difference_update(BlacklistWords)

    # Filter out rows from the DataFrame where 'Word' is in the dictionary
    filtered_df = df[~df['Word'].str.lower().isin(dictionary_words)]

    return filtered_df
def preprocess_text(text):
    if isinstance(text, str):  # Ensure text is a string
        text = text.lower()
        # Keep contractions and other common word forms intact
        text = re.sub(r"[^a-zA-Z0-9\s']", '', text)
        return text.split()
    return []  # Return an empty list if not a string

def MakeFrequencyTable(filename,FilterWords=False,dictionary_path=None,BlacklistWords=None):
    df = pd.read_csv(filename + ".csv")
    print(df)
    all_words = []
    for message in df['Content']:
        all_words.extend(preprocess_text(message))

    # Count word frequencies
    word_freq = Counter(all_words)

    # Create and save DataFrame to CSV
    word_freq_df = pd.DataFrame(word_freq.items(), columns=['Word', 'Frequency'])
    word_freq_df = word_freq_df.sort_values(by='Frequency', ascending=False)

    if FilterWords:
        word_freq_df = filter_words_from_dataframe(word_freq_df, dictionary_path=dictionary_path, BlacklistWords=BlacklistWords)
    # Save to CSV
    word_freq_df.to_csv(filename + '_word_frequencies.csv', index=False)
    return word_freq_df
