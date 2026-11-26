"""Compatibility helpers for texting-pattern analysis; importing performs no I/O."""
import pandas as pd
from pymessage.text import statistics, tokens


def ConvertDatesToDateTime(filename):
    frame = pd.read_csv(str(filename)+'.csv',keep_default_na=False)
    frame['Timestamp'] = pd.to_datetime(frame['Timestamp'])
    return frame


def SeparateBySender(df, sender):
    return df.loc[df['Sender']==sender].copy()


def GroupByTime(df, timeframe='week'):
    periods = {'week':'W','month':'M'}
    if timeframe not in periods:
        raise ValueError('Timeframe should be week or month')
    frame = df.copy()
    frame['GroupTime'] = frame['Timestamp'].dt.to_period(periods[timeframe]).dt.start_time
    return frame.groupby('GroupTime').agg(MessageCount=('Content','size'),Messages=('Content',list)).reset_index()


def seperate_message_by_words(text):
    return list(tokens(text if isinstance(text,str) else ''))


def getWordInfo(grouped):
    frame = grouped.copy()
    words, characters = [], []
    for messages in frame['Messages']:
        metrics = [statistics(value if isinstance(value,str) else '') for value in messages]
        words.append(sum(value['word_count'] for value in metrics))
        characters.append(sum(value['word_characters'] for value in metrics))
    frame['number_of_words'] = words
    frame['number_of_chars'] = characters
    count = frame['MessageCount'].replace(0,float('nan'))
    frame['avg_words_per_message'] = frame['number_of_words']/count
    frame['avg_chars_per_message'] = frame['number_of_chars']/count
    frame['AverageWordLength'] = frame['number_of_chars']/frame['number_of_words'].replace(0,float('nan'))
    return frame


def plot_avg_word_count(df):
    import matplotlib.pyplot as plt
    figure,axis = plt.subplots(figsize=(12,6))
    axis.plot(df['GroupTime'],df['avg_words_per_message'],marker='o')
    axis.set(title='Average words per message over time',xlabel='Time',ylabel='Words per message')
    axis.grid(True)
    figure.tight_layout()
    plt.show()
    plt.close(figure)
