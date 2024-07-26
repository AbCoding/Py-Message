import pandas as pd
from wordcloud import WordCloud
import matplotlib.pyplot as plt
import FrequencyTableGenerator

def GenerateWordCloud(filename,FilterDictionary=False, BlackListedwords=None):
    # Load the frequency table
    df= FrequencyTableGenerator.MakeFrequencyTable(filename, FilterWords=FilterDictionary, dictionary_path="CommonEnglishWords.txt", BlacklistWords=BlackListedwords)
    print(df)

    filtered_word_freq = {word: freq for word, freq in zip(df['Word'], df['Frequency'])}


    # Generate the word cloud
    wordcloud = WordCloud(width=800, height=400, background_color='white').generate_from_frequencies(filtered_word_freq)

    # Display the word cloud
    plt.figure(figsize=(10, 5))
    plt.imshow(wordcloud, interpolation='bilinear')
    plt.axis('off')  # Hide axes
    plt.show()

# Example usage:# GenerateWordCloud('word_frequencies.csv', DeleteEnglishWords=True, IncludeWords=['example', 'test'])
