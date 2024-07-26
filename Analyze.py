import plotly.graph_objects as go
from collections import defaultdict,Counter
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
import re

# Path to your HTML file
file_path = 'george.html'

# Read the HTML file
with open(file_path, 'r', encoding='utf-8') as file:
    html_doc = file.read()

# Parse the HTML document
soup = BeautifulSoup(html_doc, 'html.parser')

# Extract timestamps
timestamps = soup.find_all('span', class_='timestamp')
date_pattern = r"(\w+ \d{1,2}, \d{4})"
dates = []

# Extract dates
for timestamp in timestamps:
    t = str(timestamp)
    match = re.search(date_pattern, t)
    if match:
        dates.append(match.group(1))

# Count occurrences of each date
date_counts = Counter(dates)
data = [[date, count] for date, count in date_counts.items()]

# Extract dates and counts
unique_dates = [datetime.strptime(entry[0], '%b %d, %Y') for entry in data]
counts = [entry[1] for entry in data]

# Helper function to get the start of the week for a given date
def get_week_start(date):
    return date - timedelta(days=date.weekday())

# Helper function to get the start of the month for a given date
def get_month_start(date):
    return date.replace(day=1)

# Aggregate counts by week
def aggregate_by_week(dates, counts):
    weekly_counts = defaultdict(int)
    for date, count in zip(dates, counts):
        week_start = get_week_start(date)
        weekly_counts[week_start] += count
    return weekly_counts

# Aggregate counts by month
def aggregate_by_month(dates, counts):
    monthly_counts = defaultdict(int)
    for date, count in zip(dates, counts):
        month_start = get_month_start(date)
        monthly_counts[month_start] += count
    return monthly_counts
def aggregate_by_day(dates, counts):
    daily_counts = defaultdict(int)
    for date, count in zip(dates, counts):
        daily_counts[date] += count
    return daily_counts
# Generate interactive bar plot for weekly data


# Generate interactive bar plot for daily data
def generate_by_day(dates, counts):
    daily_counts = aggregate_by_day(dates, counts)

    # Prepare data for plotting
    days = list(daily_counts.keys())
    counts = list(daily_counts.values())

    # Sort days and counts
    sorted_days_counts = sorted(zip(days, counts))
    days, counts = zip(*sorted_days_counts)

    # Format days for better x-axis labeling
    formatted_days = [day.strftime('%b %d, %Y') for day in days]

    fig = go.Figure(data=[go.Bar(x=formatted_days, y=counts, marker_color='blue')])

    # Update layout for better readability
    fig.update_layout(
        title='Number of Texts Sent Per Day',
        xaxis_title='Date',
        yaxis_title='Number of Texts',
        xaxis_tickangle=-45
    )

    # Show the interactive plot
    fig.show()

def generate_by_week(dates, counts):
    weekly_counts = aggregate_by_week(dates, counts)

    # Prepare data for plotting
    weeks = list(weekly_counts.keys())
    counts = list(weekly_counts.values())

    # Sort weeks and counts
    sorted_weeks_counts = sorted(zip(weeks, counts))
    weeks, counts = zip(*sorted_weeks_counts)

    # Format weeks for better x-axis labeling
    formatted_weeks = [week.strftime('%b %d, %Y') for week in weeks]

    fig = go.Figure(data=[go.Bar(x=formatted_weeks, y=counts, marker_color='blue')])

    # Update layout for better readability
    fig.update_layout(
        title='Number of Texts Sent Per Week',
        xaxis_title='Week Starting',
        yaxis_title='Number of Texts',
        xaxis_tickangle=-45
    )

    # Show the interactive plot
    fig.show()

# Generate interactive bar plot for monthly data
def generate_by_month(dates, counts):
    monthly_counts = aggregate_by_month(dates, counts)

    # Prepare data for plotting
    months = list(monthly_counts.keys())
    counts = list(monthly_counts.values())

    # Sort months and counts
    sorted_months_counts = sorted(zip(months, counts))
    months, counts = zip(*sorted_months_counts)

    # Format months for better x-axis labeling
    formatted_months = [month.strftime('%b %Y') for month in months]

    fig = go.Figure(data=[go.Bar(x=formatted_months, y=counts, marker_color='blue')])

    # Update layout for better readability
    fig.update_layout(
        title='Number of Texts Sent Per Month',
        xaxis_title='Month Starting',
        yaxis_title='Number of Texts',
        xaxis_tickangle=-45
    )

    # Show the interactive plot
    fig.show()

# Call function to generate monthly plot
generate_by_week(unique_dates, counts)
