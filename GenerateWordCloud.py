from wordcloud import WordCloud
import matplotlib.pyplot as plt
import FrequencyTableGenerator

def GenerateWordCloud(filename,FilterDictionary=False, BlackListedwords=None, *, output_file=None, show=True, frequency_table=None):
    # Load the frequency table
    df = frequency_table if frequency_table is not None else FrequencyTableGenerator.MakeFrequencyTable(
        filename, FilterWords=FilterDictionary,
        dictionary_path=FrequencyTableGenerator.common_dictionary_path(),
        BlacklistWords=BlackListedwords,
    )

    filtered_word_freq = {word: freq for word, freq in zip(df['Word'], df['Frequency'])}
    if not filtered_word_freq:
        return None


    # Generate the word cloud
    wordcloud = WordCloud(width=800, height=400, background_color='white', random_state=42).generate_from_frequencies(filtered_word_freq)
    if output_file:
        wordcloud.to_file(str(output_file))

    # Display the word cloud
    if show:
        figure, axis = plt.subplots(figsize=(10, 5))
        axis.imshow(wordcloud, interpolation='bilinear')
        axis.axis('off')
        plt.show()
        plt.close(figure)
    return wordcloud

# Example usage:# GenerateWordCloud('word_frequencies.csv', DeleteEnglishWords=True, IncludeWords=['example', 'test'])
