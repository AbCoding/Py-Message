from datetime import datetime, timedelta
from bs4 import BeautifulSoup
import re
import csv

def ExtractAllMessages(file_path):
    with open(file_path, 'r', encoding='utf-8') as file:
        html_doc = file.read()

    # Parse the HTML document
    soup = BeautifulSoup(html_doc, 'html.parser')

    # Extract timestamps
    timestamps = soup.find_all('div', class_='message')
    for i,timestamp in enumerate(timestamps):
        timestamps[i] = timestamp.extract()
    return timestamps


def TimeStamp_to_DateTime(timestamp):
    date_format = "%b %d, %Y %I:%M:%S %p"

    date_object = datetime.strptime(timestamp, date_format)

    return date_object


def extract_timedelta_from_ReadTime(text):
    HourRegEx = r'(\d+)\shours'
    MinuteRegEx = r'(?:\d+\shours,\s)?(\d+)\sminutes'
    SecondRegEx = r'(?:\d+\sminutes,\s)?(\d+)\sseconds\)'

    hours = 0
    minutes = 0
    seconds = 0

    match = re.search(HourRegEx, text)
    if match is not None:
        hours = int(match.group(1))

    match = re.search(MinuteRegEx, text)
    if match is not None:
        minutes = int(match.group(1))

    match = re.search(SecondRegEx, text)
    if match is not None:
        seconds = int(match.group(1))

    return timedelta(hours=hours, minutes=minutes, seconds=seconds)


def MessageToDataEntry(message):
    #String

    MessageSender= str(message.find('span', class_='sender').getText())


    #Datetime Value
    MessageTimeString = str(message.find('span', class_='timestamp').getText())

    MessageTimeStamp = TimeStamp_to_DateTime(MessageTimeString.split('(')[0].strip())


    #Time value
    if len(MessageTimeString.split('('))>1:
        ReadTime=extract_timedelta_from_ReadTime(MessageTimeString.split('(')[1].strip())
    else:
        ReadTime=None


    #String

    MessageContent= message.find('span', class_='bubble')
    if MessageContent:
        MessageContent=str(MessageContent.getText())

    return {
        'Sender': MessageSender,
        'Timestamp': MessageTimeStamp,
        'ReadTime': ReadTime,
        'Content': MessageContent
    }


#Database Structure:

#Message Sender(str)| Message timeStamp(datetime)|How Long To Read(deltatime)| Message Content(str)

def WriteToCSV(fileName, output_file):
    messages = ExtractAllMessages(fileName)
    with open(output_file, 'w', newline='', encoding='utf-8') as file:
        writer = csv.DictWriter(file, fieldnames=['Sender', 'Timestamp', 'ReadTime', 'Content'])
        writer.writeheader()
        for message in messages:
            writer.writerow(MessageToDataEntry(message))



