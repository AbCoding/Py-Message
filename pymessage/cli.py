"""One-command workflow plus independent offline sync/analyze/report/export tools."""
import argparse
from dataclasses import asdict
from datetime import date, datetime, timezone
import json
import math
import os
from pathlib import Path
import platform
import sqlite3
import sys
import uuid
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import __version__
from .analysis import Config, conversations
from .archive import Archive, SCHEMA_VERSION
from .decoder import native_binary
from .reports import generate_reports, write_export
from .source import sync_database
from .util import Timings, atomic_write, dumps, exclusive_lock, file_digest, slug


def local_timezone():
    zone = os.environ.get('TZ')
    if not zone:
        path = str(Path('/etc/localtime').resolve())
        if '/zoneinfo/' in path:
            zone = path.split('/zoneinfo/',1)[1]
    try:
        ZoneInfo(zone or 'UTC')
        return zone or 'UTC'
    except ZoneInfoNotFoundError:
        return 'UTC'


def parse_date(value):
    try:
        if date.fromisoformat(value).isoformat() != value:
            raise ValueError(value)
        return value
    except ValueError as error:
        raise argparse.ArgumentTypeError('Use YYYY-MM-DD.') from error


def parser():
    p = argparse.ArgumentParser(description='Archive and analyze local iMessages. Use app for the interactive UI; run for sync + analysis + report.')
    p.add_argument('command',nargs='?',choices=['run','sync','analyze','report','export','doctor','app'],default='run')
    p.add_argument('--port',type=int,default=8765,help='Local app port; use 0 for an available port.')
    p.add_argument('--no-browser',action='store_true',help='Print the app URL without opening a browser.')
    p.add_argument('--demo',action='store_true',help='With app: use fictional conversations in an isolated demo archive.')
    source = p.add_mutually_exclusive_group()
    source.add_argument('--db-path',type=Path,help='Default: ~/Library/Messages/chat.db')
    source.add_argument('--html-dir',type=Path,help='Import an existing HTML directory (fields unavailable in HTML remain unknown).')
    p.add_argument('--output',type=Path,default=Path.cwd()/'GeneratedFiles',help='Report/run directory. Default: ./GeneratedFiles')
    p.add_argument('--archive',type=Path,help='Persistent archive path. Default: OUTPUT/.state/archive.sqlite')
    p.add_argument('--source-id',help='Stable source identity override for a database that moves between paths.')
    p.add_argument('--timezone',default=local_timezone(),help='IANA timezone for reports and date filters; default: system zone, or UTC.')
    p.add_argument('--conversation',help='Conversation name or participant address substring; comma separates alternatives.')
    p.add_argument('--start-date',type=parse_date)
    p.add_argument('--end-date',type=parse_date,help='Exclusive upper date boundary.')
    p.add_argument('--group-by',choices=['time_of_day','day_of_week','day','week','month','all'],default='week')
    p.add_argument('--separate-by-sender',action='store_true')
    p.add_argument('--filter-common-words',action='store_true')
    p.add_argument('--include-absent',action='store_true',help='Include historically observed records no longer present at the source.')
    p.add_argument('--archive-media',action='store_true',help='Copy available original media into a deduplicated local archive.')
    p.add_argument('--attachment-root',type=Path,help='Override the source Attachments directory for a copied database.')
    p.add_argument('--retain-snapshots',action='store_true')
    p.add_argument('--rebuild',action='store_true',help='Reconcile/recompute all rows; keep retained history and reuse safe body decodes.')
    p.add_argument('--strict',action='store_true',help='Fail on unsupported records instead of publishing partial coverage.')
    p.add_argument('--charts',choices=['top','all','none'],default='top')
    p.add_argument('--top',type=int,default=20,help='Number of largest conversations to chart (default: 20).')
    p.add_argument('--max-words',type=int,default=300)
    p.add_argument('--workers',type=int,choices=range(1,5),default=1,help='Bounded chart-rendering processes, 1–4.')
    p.add_argument('--response-gap-hours',type=float,default=24)
    p.add_argument('--format',choices=['jsonl','csv'],default='jsonl',help='Format for the export command.')
    p.add_argument('--export-csv',action='store_true',help='Also materialize full message CSVs during report generation (can be large).')
    p.add_argument('--prepare-exporter',action='store_true',help='Verify the bundled decoder without opening messages.')
    p.add_argument('--legacy-html',action='store_true',help='Additionally export upstream HTML from the acquired snapshot.')
    p.add_argument('--version',action='version',version=__version__)
    p.add_argument('--verify-media',action='store_true',help='With doctor: verify copied media against its recorded SHA-256.')
    return p


def doctor(args, archive_path):
    checks = {'version':__version__,'python':platform.python_version(),'platform':platform.platform(),
              'timezone':args.timezone,'archive':str(archive_path),'archive_exists':archive_path.exists(),
              'native_decoder':str(native_binary()) if native_binary() else 'unavailable; plain text/HTML imports supported',
              'source_exists':(args.db_path or Path.home()/'Library/Messages/chat.db').expanduser().is_file()}
    if archive_path.exists():
        connection = sqlite3.connect(archive_path.as_uri()+'?mode=ro',uri=True)
        try:
            checks['archive_integrity'] = connection.execute('PRAGMA quick_check').fetchone()[0]
            checks['archive_schema'] = connection.execute('PRAGMA user_version').fetchone()[0]
            checks['incomplete_runs'] = [dict(zip(('id','status'),row)) for row in connection.execute("SELECT id,status FROM runs WHERE status!='complete' ORDER BY started DESC LIMIT 10")]
            if args.verify_media:
                checks['media_checked'] = 0
                checks['media_errors'] = []
                for path,checksum in connection.execute('SELECT DISTINCT archived_path,sha256 FROM attachments WHERE archived_path IS NOT NULL'):
                    checks['media_checked'] += 1
                    if not Path(path).is_file() or file_digest(path)!=checksum:
                        checks['media_errors'].append(path)
        finally:
            connection.close()
    print(json.dumps(checks,indent=2))
    return 0 if checks.get('archive_integrity','ok')=='ok' and not checks.get('media_errors') else 1


def execute(args):
    output = args.output.expanduser().resolve()
    archive_path = (args.archive or output/'.state'/'archive.sqlite').expanduser().resolve()
    state = archive_path.parent
    source_path = (args.db_path or Path.home()/'Library/Messages/chat.db').expanduser().resolve()
    if args.command in ('run','sync') and not args.html_dir and (archive_path==source_path or (archive_path.exists() and source_path.exists() and archive_path.samefile(source_path))):
        raise RuntimeError('--archive must be different from the source database. Source data was not modified.')
    if args.prepare_exporter:
        binary = native_binary()
        if binary:
            print(f'Bundled decoder verified: {binary}')
        else:
            raise RuntimeError('No bundled native decoder for this platform. Plain text and HTML imports remain available.')
        return 0
    if args.command=='doctor':
        return doctor(args,archive_path)
    if args.command in ('analyze','report','export') and not archive_path.exists():
        raise RuntimeError('Archive does not exist. Run sync or the default command first.')
    config = Config(timezone=args.timezone,conversation=args.conversation,start_date=args.start_date,end_date=args.end_date,
                    group_by=args.group_by,separate_by_sender=args.separate_by_sender,filter_common_words=args.filter_common_words,
                    include_absent=args.include_absent,charts=args.charts,top=args.top,max_words=args.max_words,response_gap_hours=args.response_gap_hours,export_csv=args.export_csv)
    run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:8]
    started = datetime.now(timezone.utc).isoformat()
    timings = Timings()
    summary = {'run_id':run_id,'version':__version__,'archive_schema':SCHEMA_VERSION,'errors':[],
               'config':asdict(config),'command':args.command,'status':'running','started':started,
               'archive':str(archive_path),'source_requested':{'db_path':str(source_path) if not args.html_dir else None,
               'html_dir':str(args.html_dir) if args.html_dir else None,'identity':args.source_id},
               'runtime':{'python':platform.python_version(),'platform':platform.platform()}}
    output.mkdir(parents=True,exist_ok=True)
    state.mkdir(parents=True,exist_ok=True)
    (state/'matplotlib').mkdir(exist_ok=True)
    (state/'cache'/'fontconfig').mkdir(parents=True,exist_ok=True)
    # Keep caches writable for a CLI launched from a restricted IDE/sandbox.
    os.environ.setdefault('MPLCONFIGDIR',str(state/'matplotlib'))
    os.environ.setdefault('XDG_CACHE_HOME',str(state/'cache'))
    with exclusive_lock(state/'archive.lock'), Archive(archive_path) as archive:
        archive.db.execute('INSERT INTO runs(id,started,status,config) VALUES(?,?,?,?)',(run_id,started,'running',dumps(asdict(config))))
        archive.db.commit()
        try:
            if args.command in ('run','sync'):
                print('Acquiring source and reconciling archive...',flush=True)
                if args.html_dir:
                    from .html_source import sync_html
                    result = sync_html(archive,args.html_dir,run_id,timings,zone=config.timezone,strict=args.strict,
                                       rebuild=args.rebuild,exclude=output if output.is_relative_to(args.html_dir.expanduser().resolve()) else None)
                else:
                    result = sync_database(archive,args.db_path or Path.home()/'Library/Messages/chat.db',state,run_id,timings,
                                           strict=args.strict,rebuild=args.rebuild,archive_media=args.archive_media,
                                           attachment_root=args.attachment_root,retain_snapshots=args.retain_snapshots,source_name=args.source_id)
                summary['sync'] = result
                print(f"Sync: {result['added']} added, {result['changed']} changed, {result['unchanged']} unchanged.",flush=True)
                if args.legacy_html:
                    from MessageExporter import export_messages
                    source_row = archive.db.execute('SELECT snapshot FROM sources WHERE id=?',(result['source_id'],)).fetchone()
                    if source_row and source_row[0]:
                        with timings.stage('legacy_html'):
                            export_messages(source_row[0],output/'runs'/run_id/'exports',cache_dir=state/'tools')
                    else:
                        raise RuntimeError('--legacy-html requires a database source.')
            if args.command in ('run','analyze','report'):
                print('Preparing analysis and local report...',flush=True)
                report, index = generate_reports(archive,config,output,timings,workers=args.workers,rebuild=args.rebuild)
                summary.update({'total_messages':report['total_messages'],'conversations':len(report['conversations']),
                                'report':str(index),'cache':report['cache']})
                print(f"Report: {index}\nMessages: {report['total_messages']:,}; analysis reused for {report['cache']['analysis_cached']} conversations.",flush=True)
            if args.command=='export':
                export_dir = output/'exports'/run_id
                with timings.stage('export'):
                    count = 0
                    for chat in conversations(archive,config):
                        write_export(archive,chat,config,export_dir/(slug(chat['name'],chat['id'])+'.'+args.format),args.format)
                        count += 1
                summary['export'] = {'directory':str(export_dir),'conversations':count,'format':args.format}
                print(f'Exported {count} conversations to {export_dir}',flush=True)
            # Historical failures are available in the archive; this manifest
            # describes errors and retained unsupported bodies for this run.
            summary['error_count'] = archive.db.execute('SELECT COUNT(*) FROM import_errors WHERE run_id=?',(run_id,)).fetchone()[0]
            summary['errors'] = [dict(row) for row in archive.db.execute('SELECT stage,record_key,error FROM import_errors WHERE run_id=? LIMIT 100',(run_id,))]
            summary['errors_truncated'] = summary['error_count'] > len(summary['errors'])
            summary['coverage'] = {
                'unsupported_bodies':archive.db.execute("SELECT COUNT(*) FROM messages WHERE decode_status='unsupported'").fetchone()[0],
                'invalid_timestamps':archive.db.execute('SELECT COUNT(*) FROM messages WHERE timestamp_ns IS NULL').fetchone()[0],
                'unresolved_import_errors':archive.db.execute('SELECT COUNT(*) FROM import_errors WHERE resolved=0').fetchone()[0]}
            summary['sources'] = [dict(row) for row in archive.db.execute('SELECT id,path,kind,snapshot_hash,decoder,last_success,coverage FROM sources')]
            summary['status'] = 'partial' if summary['errors'] or any(summary['coverage'].values()) else 'complete'
            status = 2 if summary['status']=='partial' else 0
        except BaseException as error:
            archive.db.rollback()
            summary['status'] = 'interrupted' if isinstance(error,KeyboardInterrupt) else 'failed'
            summary['errors'].append({'stage':'workflow','error':str(error) or type(error).__name__})
            raise
        finally:
            summary['timings_seconds'] = timings.values
            summary['finished'] = datetime.now(timezone.utc).isoformat()
            archive.manifest(run_id,summary,summary['status'],summary['finished'])
            atomic_write(output/'runs'/run_id/'summary.json',dumps(summary)+'\n')
            atomic_write(output/'latest-run.json',dumps(summary)+'\n')
    if status==2:
        print('Partial coverage: original records were retained; see latest-run.json and doctor for details.',file=sys.stderr)
    return status


def main(argv=None):
    p = parser()
    args = p.parse_args(argv)
    try:
        ZoneInfo(args.timezone)
    except ZoneInfoNotFoundError:
        p.error('Unknown IANA timezone: '+args.timezone)
    if args.start_date and args.end_date and args.start_date>=args.end_date:
        p.error('--end-date must be after --start-date.')
    if args.top<1 or args.max_words<1 or not math.isfinite(args.response_gap_hours) or args.response_gap_hours<=0:
        p.error('--top, --max-words, and --response-gap-hours must be positive.')
    if args.html_dir and args.legacy_html:
        p.error('--legacy-html requires a database source.')
    if not 0 <= args.port <= 65535:
        p.error('--port must be between 0 and 65535.')
    if args.demo and args.command != 'app':
        p.error('--demo requires the app command.')
    try:
        if args.command == 'app':
            from .app import serve
            return serve(args)
        return execute(args)
    except KeyboardInterrupt:
        print('Interrupted. Rerun the same command to resume from retained source/decode checkpoints.',file=sys.stderr)
        return 130
    except (RuntimeError,OSError,sqlite3.Error,ValueError) as error:
        print(f'Error: {error}',file=sys.stderr)
        return 1


if __name__=='__main__':
    sys.exit(main())
