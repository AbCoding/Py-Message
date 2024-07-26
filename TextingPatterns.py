import numpy as np
import matplotlib.pyplot as plt
from datetime import datetime, timedelta
import pandas as pd
import re


def ConvertDatesToDateTime(filename):
    # Read the CSV file into a DataFrame
    df = pd.read_csv(filename + ".csv")

    # Convert the 'Timestamp' column to datetime objects
    date_strings = df['Timestamp'].tolist()
    dates = [datetime.strptime(date, '%Y-%m-%d %H:%M:%S') for date in date_strings]

    # Create a new DataFrame with the 'datetime' column
    df['Timestamp'] = dates

    return df


def SeparateBySender(df, sender):
    # Filter the DataFrame to include only rows where 'Sender' matches the provided sender string
    sender_df = df[df['Sender'] == sender]


    # Return the DataFrame with only the 'datetime' column
    return sender_df


def GroupByTime(df, timeframe="week"):
    if timeframe == "week":
        df['GroupTime'] = df['Timestamp'].dt.to_period('W').apply(lambda r: r.start_time)
    elif timeframe == "month":
        df['GroupTime'] = df['Timestamp'].dt.to_period('M').apply(lambda r: r.start_time)
    else:
        raise ValueError("Timeframe should be 'week' or 'month'")

    # Group by the new 'GroupTime' column and aggregate messages
    grouped_df = df.groupby('GroupTime').agg(
        MessageCount=('Content', 'size'),
        Messages=('Content', lambda x: list(x))
    ).reset_index()

    return grouped_df


def seperate_message_by_words(text):
    if isinstance(text, str):  # Ensure text is a string
        text = text.lower()
        text = re.sub(r'\W+', ' ', text)
        return text.split()
    elif isinstance(text, (int, float)):
        text = str(text)
        text = re.sub(r'\W+', ' ', text)
        return text.split()
    else:
        return []


def getWordInfo(GroupedMessages):
    NumWordArr = []
    NumCharArr = []
    MessageCountArr = []

    for ListOfMessages in GroupedMessages["Messages"]:
        num_words = 0
        num_chars = 0


        for message in ListOfMessages:
            m = seperate_message_by_words(message)
            for word in m:
                num_chars += len(word)
            num_words += len(m)


        NumWordArr.append(num_words)
        NumCharArr.append(num_chars)

    GroupedMessages["number_of_words"] = NumWordArr
    GroupedMessages["number_of_chars"] = NumCharArr
    GroupedMessages["avg_words_per_message"] = GroupedMessages["number_of_words"] / GroupedMessages[
        "MessageCount"]
    GroupedMessages["avg_chars_per_message"] = GroupedMessages["number_of_chars"] / GroupedMessages[
        "MessageCount"]
    GroupedMessages["AverageWordLength"] = GroupedMessages["number_of_chars"] / GroupedMessages[
        "number_of_words"]
    return GroupedMessages

def plot_avg_word_count(df):
    plt.figure(figsize=(12, 6))
    plt.plot(df['GroupTime'], df['AverageWordLength'], marker='o', linestyle='-', color='b')
    plt.title('Average Words per Message Over Time')
    plt.xlabel('Time')
    plt.ylabel('Average Words per Message')
    plt.grid(True)
    plt.show()
# # Example usage
df = ConvertDatesToDateTime("GeneratedFiles/Houston Rizzlers - 190").dropna(subset=['Content'])

df = SeparateBySender(df,"+19014211459")
grouped_by_week = GroupByTime(df, timeframe="week")
grouped_by_week= (getWordInfo(grouped_by_week))

print((grouped_by_week["number_of_chars"]/grouped_by_week["number_of_words"]).mean())

df = ConvertDatesToDateTime("GeneratedFiles/Houston Rizzlers - 190").dropna(subset=['Content'])

df = SeparateBySender(df,"Me")
grouped_by_week = GroupByTime(df, timeframe="week")
grouped_by_week= getWordInfo(grouped_by_week)
print((grouped_by_week["number_of_chars"]/grouped_by_week["number_of_words"]).mean())

