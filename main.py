"""Run local message export and analysis: python main.py."""

import argparse
from datetime import date, datetime
import json
from pathlib import Path
import sys
import tempfile

import MessageExporter


PROJECT_ROOT = Path(__file__).resolve().parent


# Preserve the original notebook/script entry points.
def GenMessages(filename, GroupBy="week", SeperateBySender=False, sender="all"):
    import MessageFileToDatabase
    import MessagesOverTime
    MessageFileToDatabase.WriteToCSV(str(filename) + ".html", str(filename) + ".csv")
    return MessagesOverTime.GenerateTimeline(str(filename), GroupBy=GroupBy, SeperateBySender=SeperateBySender, sender=sender)


def MakeWordCloudFromScratch(filename, FilterDict=False, BlackListedWords=None):
    import MessageFileToDatabase
    import GenerateWordCloud
    MessageFileToDatabase.WriteToCSV(str(filename) + ".html", str(filename) + ".csv")
    return GenerateWordCloud.GenerateWordCloud(str(filename), FilterDict, BlackListedwords=BlackListedWords)


def iso_date(value):
    try:
        date.fromisoformat(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("Expected a date in YYYY-MM-DD format.") from error
    return value


def build_parser():
    parser = argparse.ArgumentParser(description="Export your Mac's messages and generate CSVs, timelines, and word clouds.")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--db-path", type=Path, help="Messages database (default: ~/Library/Messages/chat.db).")
    source.add_argument("--html-dir", type=Path, help="Analyze an existing HTML export instead of accessing Messages.")
    parser.add_argument("--output", type=Path, default=PROJECT_ROOT / "GeneratedFiles", help="Parent folder for a new run (default: GeneratedFiles beside main.py).")
    parser.add_argument("--conversation", help="Export conversations matching a contact name, phone number, or email.")
    parser.add_argument("--start-date", type=iso_date, help="Include messages on or after YYYY-MM-DD.")
    parser.add_argument("--end-date", type=iso_date, help="Include messages before YYYY-MM-DD (exclusive).")
    parser.add_argument("--group-by", choices=["time_of_day", "day_of_week", "day", "week", "month", "all"], default="week")
    parser.add_argument("--separate-by-sender", action="store_true", help="Compare senders on the timeline.")
    parser.add_argument("--filter-common-words", action="store_true", help="Exclude CommonEnglishWords.txt from word frequencies/clouds.")
    parser.add_argument("--prepare-exporter", action="store_true", help="Download/verify the project-managed exporter and exit without reading messages.")
    return parser


def run_pipeline(args):
    # Save figures without opening a blocking window for every conversation.
    import matplotlib
    matplotlib.use("Agg")
    import FrequencyTableGenerator
    import GenerateWordCloud
    import MessageFileToDatabase
    import MessagesOverTime

    if args.html_dir:
        source = args.html_dir.expanduser().resolve()
        if not source.is_dir():
            raise ValueError(f"HTML export directory does not exist: {source}")
        files = sorted(source.rglob("*.html"))
        if not files:
            raise ValueError(f"No HTML files found in {source}")
    else:
        source = MessageExporter.validate_database(args.db_path or MessageExporter.default_database())

    output = args.output.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    run_dir = Path(tempfile.mkdtemp(prefix=datetime.now().strftime("%Y-%m-%d_%H-%M-%S_"), dir=output))
    if not args.html_dir:
        files = MessageExporter.export_messages(
            source, run_dir / "exports", conversation=args.conversation,
            start_date=args.start_date, end_date=args.end_date,
        )
        source = run_dir / "exports"
    analysis_dir = run_dir / "analysis"
    analysis_dir.mkdir()
    results, errors = [], []
    for index, html in enumerate(files, start=1):
        # Preserve relative paths so equally named conversations cannot collide.
        relative = html.relative_to(source)
        base = analysis_dir / relative.with_suffix("")
        base.parent.mkdir(parents=True, exist_ok=True)
        print(f"Analyzing conversation {index}/{len(files)}...", flush=True)
        try:
            count = MessageFileToDatabase.WriteToCSV(html, str(base) + ".csv")
            if count == 0:
                continue
            frequencies = FrequencyTableGenerator.MakeFrequencyTable(str(base), FilterWords=args.filter_common_words)
            MessagesOverTime.GenerateTimeline(
                str(base), GroupBy=args.group_by, SeperateBySender=args.separate_by_sender,
                output_file=str(base) + "_timeline.png", show=False,
            )
            cloud = GenerateWordCloud.GenerateWordCloud(
                str(base), output_file=str(base) + "_wordcloud.png", show=False, frequency_table=frequencies,
            )
            results.append({"conversation": str(relative), "messages": count, "wordcloud": cloud is not None})
        except (ValueError, OSError) as error:
            errors.append({"conversation": str(relative), "error": str(error)})
            print(f"Could not analyze {relative}: {error}", file=sys.stderr)
    summary = {"conversations": results, "errors": errors, "total_messages": sum(item['messages'] for item in results)}
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(f"Analyzed {len(results)} conversations / {summary['total_messages']} messages.\nResults: {run_dir}")
    if errors:
        raise ValueError(f"{len(errors)} conversations could not be analyzed; details are in {run_dir / 'summary.json'}")
    if not results:
        raise ValueError("No messages found in the exported HTML. Check your filters or export format.")
    return run_dir


def main(argv=None):
    from pymessage.cli import main as application_main
    return application_main(argv)


if __name__ == "__main__":
    sys.exit(main())
