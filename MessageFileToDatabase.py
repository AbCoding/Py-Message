"""Convert imessage-exporter HTML (legacy and current) to analysis CSVs."""

from datetime import datetime, timedelta
import csv
import re

from bs4 import BeautifulSoup


def _own_elements(message, selector):
    # Never attribute a nested reply's timestamp, sender or text to its parent.
    return [
        element for element in message.select(selector)
        if element.find_parent("div", class_="message") is message
    ]


def ExtractAllMessages(file_path):
    with open(file_path, "r", encoding="utf-8") as source:
        soup = BeautifulSoup(source, "html.parser")
    messages = []
    seen = set()
    for message in soup.select("div.message"):
        # Current exports expose the GUID in sms:// links for both inline
        # replies and their duplicate renderings in the main timeline.
        timestamps = _own_elements(message, "span.timestamp")
        link = timestamps[0].find("a", href=re.compile(r"message-guid=")) if timestamps else None
        identity = link.get("href") if link else message.get("id")
        if identity:
            identity = identity.removeprefix("r-")
            if identity in seen:
                continue
            seen.add(identity)
        messages.append(message)
    return messages


def TimeStamp_to_DateTime(timestamp):
    normalized = " ".join(timestamp.split())
    return datetime.strptime(normalized, "%b %d, %Y %I:%M:%S %p")


def extract_timedelta_from_ReadTime(text):
    units = {"day": 86400, "hour": 3600, "minute": 60, "second": 1}
    seconds = sum(
        int(amount) * units[unit]
        for amount, unit in re.findall(r"(\d+)\s+(day|hour|minute|second)s?\b", text)
    )
    return timedelta(seconds=seconds)


def MessageToDataEntry(message):
    senders = _own_elements(message, "span.sender")
    timestamps = _own_elements(message, "span.timestamp")
    if not senders or not timestamps:
        raise ValueError("Message is missing its sender or timestamp; unsupported HTML export.")
    timestamp_text = timestamps[0].get_text(" ", strip=True)
    date_text = timestamp_text.split("(", 1)[0].strip()
    receipt = re.search(r"\(Read .*? after (.*?)\)", timestamp_text)
    parts = []
    for bubble in _own_elements(message, "span.bubble, span.emoji, div.edited"):
        if bubble.find_parent(class_=["tapbacks", "translated", "edited"]):
            continue
        if "edited" in bubble.get("class", []):
            cells = bubble.select("tfoot tr:last-child td:last-child") or bubble.select("tr:last-child td:last-child")
            text = cells[-1].get_text(" ", strip=True) if cells else ""
        else:
            for line_break in bubble.find_all("br"):
                line_break.replace_with("\n")
            text = bubble.get_text("", strip=False).strip()
        if text:
            parts.append(text)
    return {
        "Sender": senders[0].get_text(strip=True),
        "Timestamp": TimeStamp_to_DateTime(date_text),
        "ReadTime": extract_timedelta_from_ReadTime(receipt.group(1)) if receipt else None,
        "Content": "\n".join(parts) or None,
    }


def WriteToCSV(fileName, output_file):
    messages = ExtractAllMessages(fileName)
    with open(output_file, "w", newline="", encoding="utf-8") as destination:
        writer = csv.DictWriter(destination, fieldnames=["Sender", "Timestamp", "ReadTime", "Content"])
        writer.writeheader()
        for index, message in enumerate(messages, start=1):
            try:
                writer.writerow(MessageToDataEntry(message))
            except ValueError as error:
                raise ValueError(f"{fileName}, message {index}: {error}") from error
    return len(messages)
