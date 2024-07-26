import pandas as pd
import matplotlib.pyplot as plt
from datetime import datetime

import numpy as np
def ExtractDates(filename):
    # Read the CSV file into a DataFrame
    df = pd.read_csv(filename + ".csv")

    # Convert the 'Timestamp' column to datetime objects
    date_strings = df['Timestamp'].tolist()
    dates = [datetime.strptime(date, '%Y-%m-%d %H:%M:%S') for date in date_strings]

    # Create a new DataFrame with the 'datetime' column
    return_df = pd.DataFrame(dates, columns=['datetime'])

    # Add the 'sender' column from the original DataFrame
    return_df['Sender'] = df['Sender'].tolist()

    return return_df

def SeparateBySender(df, sender):
    # Filter the DataFrame to include only rows where 'Sender' matches the provided sender string
    sender_df = df[df['Sender'] == sender]

    # Return the DataFrame with only the 'datetime' column
    return sender_df[['datetime']]

def GetUniqueSenders(df):
    # Get unique senders from the 'Sender' column
    unique_senders = df['Sender'].unique()

    return unique_senders

def GenerateTimeFrames(filename, sender="all"):
    df = ExtractDates(filename)

    if isinstance(sender, (list,tuple,set)):
        timeFrames = []
        for s in sender:
            timeFrames.append((s, SortByTime(SeparateBySender(df, s))))
        return timeFrames
    elif isinstance(sender, str):
        if sender == "all":
            ListOfSenders = GetUniqueSenders(df)
            timeFrames = []
            for s in ListOfSenders:
                timeFrames.append((s, SortByTime(SeparateBySender(df, s))))

            return timeFrames
        else:
            try:
                return(sender, SortByTime(SeparateBySender(df, sender)))
            except:
                raise  Exception("Sender not in DB")

    else:
        raise TypeError("Parameter must be a string or an array (list, tuple, set)")

def SortByTime(df):
    # By TimeOfDay
    df['hour'] = df['datetime'].dt.hour
    time_of_day = df['hour'].value_counts().sort_index()

    # By DayOfWeek
    df['day'] = df['datetime'].dt.weekday
    day_names = {0: 'Monday', 1: 'Tuesday', 2: 'Wednesday', 3: 'Thursday', 4: 'Friday', 5: 'Saturday', 6: 'Sunday'}
    df['day_name'] = df['day'].map(day_names)
    time_of_week = df['day_name'].value_counts().reindex(list(day_names.values())).fillna(0).astype(int)

    # By Day
    df['date'] = df['datetime'].dt.date
    by_day = df['date'].value_counts().sort_index()

    # By Week
    df['week'] = df['datetime'].dt.to_period('W')
    by_week = df['week'].value_counts().sort_index()

    # By Month
    df['month'] = df['datetime'].dt.to_period('M')
    by_month = df['month'].value_counts().sort_index()

    return [time_of_day, time_of_week, by_day, by_week, by_month]

def plot_shared_timeline(data_frames, labels, title=""):
    plt.figure(figsize=(10, 5))
    width = 0.1  # Width of each bar
    positions = range(len(data_frames[0]))  # X-axis positions for the first set of bars

    colors =plt.get_cmap('prism')(np.linspace(0.2, 0.7, len(data_frames))) # Generate different colors for each label


    for i, (df, label) in enumerate(zip(data_frames, labels)):
        df.plot(kind='bar', width=width, position=i - (len(data_frames) / 2) * width, alpha=0.7, label=label, color=colors[i % len(colors)])

    plt.title(title)
    plt.xlabel('Time')
    plt.ylabel('Count')
    plt.legend()
    plt.xticks(positions, data_frames[0].index, rotation=45)  # Use the index of the first dataframe for x-ticks
    plt.tight_layout()
    plt.show()

def PlotType(filename, time_data, GroupBy, labels=None):
    if GroupBy == "all":
        plot_shared_timeline([td[0] for td in time_data], labels, filename + " Sorted by Time of Day")
        plot_shared_timeline([td[1] for td in time_data], labels, filename + " Sorted by Day of Week")
        plot_shared_timeline([td[2] for td in time_data], labels, filename + " Sorted by Day")
        plot_shared_timeline([td[3] for td in time_data], labels, filename + " Sorted by Week")
        plot_shared_timeline([td[4] for td in time_data], labels, filename + " Sorted by Month")
    elif GroupBy == "time_of_day":
        plot_shared_timeline([td[0] for td in time_data], labels, filename + " Sorted by Time of Day")
    elif GroupBy == "day_of_week":
        plot_shared_timeline([td[1] for td in time_data], labels, filename + " Sorted by Day of Week")
    elif GroupBy == "day":
        plot_shared_timeline([td[2] for td in time_data], labels, filename + " Sorted by Day")
    elif GroupBy == "week":
        plot_shared_timeline([td[3] for td in time_data], labels, filename + " Sorted by Week")
    elif GroupBy == "month":
        plot_shared_timeline([td[4] for td in time_data], labels, filename + " Sorted by Month")
    else:
        print("Invalid GroupBy option. Choose from 'time_of_day', 'day_of_week', 'day', 'week', or 'month'.")

def GenerateTimeline(filename, GroupBy="week", SeperateBySender=False, sender="all"):
    if SeperateBySender:
        print(filename)
        timeFrames = GenerateTimeFrames(filename, sender)

        if isinstance(sender, str):
            if sender != "all":
                title = filename + " Sender: " + timeFrames[0]
                PlotType(title, [timeFrames[1]], GroupBy=GroupBy, labels=[timeFrames[0]])
            else:
                labels = [time_data[0] for time_data in timeFrames]
                time_data = [time_data[1] for time_data in timeFrames]
                PlotType(filename, time_data, GroupBy=GroupBy, labels=labels)

        elif isinstance(sender, (list, tuple, set)):
            labels = [time_data[0] for time_data in timeFrames]
            time_data = [time_data[1] for time_data in timeFrames]
            PlotType(filename, time_data, GroupBy=GroupBy, labels=labels)

    else:
        time_data = SortByTime(ExtractDates(filename))
        PlotType(filename, [time_data], GroupBy=GroupBy)


# Example usage:
