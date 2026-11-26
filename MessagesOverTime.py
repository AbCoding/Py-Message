import pandas as pd
import matplotlib.pyplot as plt
from pathlib import Path


GROUPS = {"time_of_day": 0, "day_of_week": 1, "day": 2, "week": 3, "month": 4}
DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def ExtractDates(filename):
    df = pd.read_csv(str(filename) + ".csv", keep_default_na=False)
    return pd.DataFrame({"datetime": pd.to_datetime(df['Timestamp']), "Sender": df['Sender']})


def SeparateBySender(df, sender):
    return df.loc[df['Sender'] == sender, ['datetime']].copy()


def GetUniqueSenders(df):
    return df['Sender'].unique()


def SortByTime(df):
    dates = df['datetime']
    return [
        dates.dt.hour.value_counts().reindex(range(24), fill_value=0),
        dates.dt.dayofweek.value_counts().reindex(range(7), fill_value=0).rename(index=dict(enumerate(DAY_NAMES))),
        dates.dt.date.value_counts().sort_index(),
        dates.dt.to_period('W').value_counts().sort_index(),
        dates.dt.to_period('M').value_counts().sort_index(),
    ]


def GenerateTimeFrames(filename, sender="all"):
    df = ExtractDates(filename)
    if isinstance(sender, str) and sender != "all":
        return sender, SortByTime(SeparateBySender(df, sender))
    senders = GetUniqueSenders(df) if sender == "all" else sender
    return [(value, SortByTime(SeparateBySender(df, value))) for value in senders]


def plot_shared_timeline(data_frames, labels=None, title="", *, output_file=None, show=True):
    # Align the union of dates before plotting so different senders' bars share
    # the same positions, even when one sender has gaps in their timeline.
    labels = labels or ["Messages"]
    data = pd.concat(data_frames, axis=1).fillna(0).sort_index()
    data.columns = labels
    figure, axis = plt.subplots(figsize=(12, 5))
    if not data.empty:
        data.plot.bar(ax=axis, width=0.8)
    axis.set(title=title, xlabel="Time", ylabel="Messages")
    # Large archives should still produce readable charts.
    if len(data) > 30:
        step = max(1, len(data) // 20)
        ticks = list(range(0, len(data), step))
        axis.set_xticks(ticks, [str(data.index[i]) for i in ticks], rotation=45, ha='right')
    else:
        plt.setp(axis.get_xticklabels(), rotation=45, ha='right')
    figure.tight_layout()
    if output_file:
        figure.savefig(output_file, dpi=150)
    if show:
        plt.show()
    plt.close(figure)
    return figure


def PlotType(filename, time_data, GroupBy, labels=None, *, output_file=None, show=True):
    groups = list(GROUPS) if GroupBy == "all" else [GroupBy]
    if any(group not in GROUPS for group in groups):
        raise ValueError(f"Unknown grouping: {GroupBy}")
    figures = []
    for group in groups:
        target = output_file
        if target and len(groups) > 1:
            target = Path(target).with_name(f"{Path(target).stem}_{group}{Path(target).suffix}")
        figures.append(plot_shared_timeline(
            [data[GROUPS[group]] for data in time_data], labels,
            f"{Path(filename).name} — messages by {group.replace('_', ' ')}",
            output_file=target, show=show,
        ))
    return figures


def GenerateTimeline(filename, GroupBy="week", SeperateBySender=False, sender="all", *, output_file=None, show=True):
    if SeperateBySender:
        frames = GenerateTimeFrames(filename, sender)
        if isinstance(sender, str) and sender != "all":
            frames = [frames]
        if not frames:
            return []
        labels = [label for label, _ in frames]
        data = [values for _, values in frames]
    else:
        data = [SortByTime(ExtractDates(filename))]
        labels = ["Messages"]
    return PlotType(filename, data, GroupBy, labels, output_file=output_file, show=show)
