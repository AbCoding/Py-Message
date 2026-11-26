"""Atomic exports, cached analysis, and a local report index."""
from concurrent.futures import ProcessPoolExecutor, wait, FIRST_COMPLETED
import csv
from dataclasses import asdict
from datetime import datetime, timezone
from html import escape
import json
from pathlib import Path

from .analysis import analyze, conversations, fingerprint, iter_messages
from .util import atomic_output, atomic_write, digest, dumps, slug


def timestamp_iso(ns):
    if ns is None:
        return None
    seconds, nanos = divmod(ns,10**9)
    return datetime.fromtimestamp(seconds,timezone.utc).strftime('%Y-%m-%dT%H:%M:%S') + f'.{nanos:09d}Z'


def export_rows(archive, chat, config):
    from .source import associated_target
    for row in iter_messages(archive,chat['id'],config):
        raw = archive.db.execute("SELECT payload FROM raw_records WHERE source=? AND table_name='message' AND record_key=?",(row['source'],str(row['source_rowid']))).fetchone()
        attachments = [dict(r) for r in archive.db.execute('''SELECT a.* FROM attachments a JOIN message_attachments ma ON ma.attachment_id=a.id
            WHERE ma.message_id=? ORDER BY a.id''',(row['id'],))]
        target,part = associated_target(row['associated_guid'])
        yield {'ID':row['id'],'GUID':row['guid'],'Source':row['source'],'Sender':row['display_sender'],
               'LogicalID':row['source']+':guid:'+row['guid'] if row['guid'] else row['id'],
               'Timestamp':timestamp_iso(row['timestamp_ns']),'Content':row['text'],'IsFromMe':row['is_from_me'],
               'Kind':row['kind'],'Service':row['service'],'ThreadGUID':row['thread_guid'],
               'AssociatedGUID':row['associated_guid'],'TargetMessageGUID':target,'TargetPart':part,'SourcePresent':bool(row['source_present']),
               'DecodeStatus':row['decode_status'],'Attachments':attachments,
               'Raw':json.loads(raw[0]) if raw else {},'Decoded':json.loads(row['decoded'])}


def write_export(archive, chat, config, target, format='jsonl'):
    target = Path(target)
    with atomic_output(target) as temporary:
        with temporary.open('w',encoding='utf-8',newline='') as stream:
            if format=='jsonl':
                for row in export_rows(archive,chat,config):
                    stream.write(dumps(row)+'\n')
            else:
                writer = None
                for row in export_rows(archive,chat,config):
                    if writer is None:
                        writer = csv.DictWriter(stream,fieldnames=list(row))
                        writer.writeheader()
                    writer.writerow({key:dumps(value) if isinstance(value,(dict,list)) else value for key,value in row.items()})


def render_charts(job):
    # Every process owns its own Matplotlib state. Limit vocabulary and labels.
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from wordcloud import WordCloud
    title, metrics, directory, max_words = job
    directory = Path(directory)
    artifacts = []
    for group, buckets in metrics['timelines'].items():
        if not buckets:
            continue
        labels = sorted({sender for values in buckets.values() for sender in values})
        keys = list(buckets)
        figure, axis = plt.subplots(figsize=(12,5))
        try:
            width = 0.8/max(1,len(labels))
            for index,label in enumerate(labels):
                axis.bar([value+index*width for value in range(len(keys))],[buckets[key].get(label,0) for key in keys],width,label=label)
            step = max(1,(len(keys)+19)//20)
            ticks = list(range(0,len(keys),step))
            axis.set_xticks(ticks,[keys[i] for i in ticks],rotation=45,ha='right')
            axis.set(title=title+' — '+group.replace('_',' '),xlabel='Time',ylabel='Messages')
            axis.legend()
            figure.tight_layout()
            path = directory/('timeline_'+group+'.png')
            with atomic_output(path) as temporary:
                figure.savefig(temporary,dpi=130)
            artifacts.append(path.name)
        finally:
            plt.close(figure)
    frequencies = dict(list(metrics['word_frequencies'].items())[:max_words])
    if frequencies:
        font = next((str(path) for path in (Path('/System/Library/Fonts/PingFang.ttc'),Path('/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc')) if path.is_file()),None)
        cloud = WordCloud(width=1000,height=500,background_color='white',max_words=max_words,random_state=42,font_path=font).generate_from_frequencies(frequencies)
        path = directory/'wordcloud.png'
        with atomic_output(path) as temporary:
            cloud.to_file(str(temporary))
        artifacts.append(path.name)
    return artifacts


def render_bounded(jobs, workers):
    if workers <= 1:
        for key,job in jobs:
            yield key,render_charts(job)
        return
    # At most two tasks per worker are queued. No giant future/dataframe list.
    with ProcessPoolExecutor(max_workers=min(workers,4)) as executor:
        pending = {}
        iterator = iter(jobs)
        exhausted = False
        while pending or not exhausted:
            while not exhausted and len(pending)<2*min(workers,4):
                try:
                    key,job = next(iterator)
                except StopIteration:
                    exhausted = True
                    break
                pending[executor.submit(render_charts,job)] = key
            if pending:
                done,_ = wait(pending,return_when=FIRST_COMPLETED)
                for future in done:
                    yield pending.pop(future),future.result()


def generate_reports(archive, config, output, timings, *, workers=1, rebuild=False):
    output = Path(output)
    config_id = config.key()[:16]
    root = output/'reports'/config_id
    root.mkdir(parents=True,exist_ok=True)
    entries = []
    jobs = []
    counts = {'analyzed':0,'analysis_cached':0,'rendered':0,'render_cached':0}
    with timings.stage('analysis_and_exports'):
        context = digest([list(row) for row in archive.db.execute('SELECT id,snapshot_hash,decoder,last_success FROM sources ORDER BY id')])
        view = archive.db.execute('SELECT * FROM view_cache WHERE key=?',(config.key(),)).fetchone()
        if view and view['context_stamp']==context and not rebuild:
            selection = json.loads(view['selection'])
        else:
            selection = list(conversations(archive,config))
            archive.db.execute('INSERT OR REPLACE INTO view_cache VALUES(?,?,?)',(config.key(),context,dumps(selection)))
        for index,chat in enumerate(selection):
            key = config.key()+':'+chat['id']
            previous = archive.db.execute('SELECT * FROM reports WHERE cache_key=?',(key,)).fetchone()
            context = archive.db.execute('SELECT snapshot_hash,decoder,last_success FROM sources WHERE id=?',(chat['source'],)).fetchone()
            context_stamp = digest([list(context) if context else [],chat['metadata']])
            stamp = previous['fingerprint'] if previous and previous['context_stamp']==context_stamp and not rebuild else fingerprint(archive,chat,config)
            cache = json.loads(previous['summary']) if previous else None
            target = root/slug(chat['name'],chat['id'])/stamp[:16]
            cached = bool(previous and previous['fingerprint']==stamp and cache and not rebuild and Path(previous['path'])==target)
            if cached and all((target/name).is_file() and (target/name).stat().st_size==size for name,size in cache.get('files',{}).items()):
                metrics = cache['metrics']
                counts['analysis_cached'] += 1
            else:
                cached = False
                target.mkdir(parents=True,exist_ok=True)
                metrics = analyze(archive,chat,config)
                atomic_write(target/'statistics.json',dumps(metrics)+'\n')
                if config.export_csv:
                    write_export(archive,chat,config,target/'messages.csv','csv')
                counts['analyzed'] += 1
            # Event-only conversations remain visible with zero message count.
            should_render = config.charts=='all' or (config.charts=='top' and index<config.top)
            chart_files = cache.get('charts',[]) if cached else []
            entry = {'chat_id':chat['id'],'name':chat['name'],'fingerprint':stamp,'metrics':metrics,
                     'directory':str(target),'charts':chart_files,'cache_key':key,'context_stamp':context_stamp,
                     'render_complete':bool(cached and cache.get('render_complete'))}
            entries.append(entry)
            if should_render:
                if cached and entry['render_complete'] and all((target/name).exists() for name in chart_files):
                    counts['render_cached'] += 1
                else:
                    jobs.append((len(entries)-1,(chat['name'],metrics,str(target),config.max_words)))
    with timings.stage('render'):
        for index,artifacts in render_bounded(jobs,workers):
            entries[index]['charts'] = artifacts
            entries[index]['render_complete'] = True
            counts['rendered'] += 1
    with timings.stage('report_index'):
        for entry in entries:
            directory = Path(entry['directory'])
            names = ['statistics.json',*(['messages.csv'] if config.export_csv else []),*entry['charts']]
            cache = {'metrics':entry['metrics'],'charts':entry['charts'],'render_complete':entry['render_complete'],
                     'files':{name:(directory/name).stat().st_size for name in names}}
            archive.db.execute('INSERT OR REPLACE INTO reports VALUES(?,?,?,?,?)',
                               (entry['cache_key'],entry['fingerprint'],entry['context_stamp'],str(directory),dumps(cache)))
        archive.db.commit()
        coverage = {'unresolved_errors':archive.db.execute('SELECT COUNT(*) FROM import_errors WHERE resolved=0').fetchone()[0],
                    'unsupported_bodies':archive.db.execute("SELECT COUNT(*) FROM messages WHERE decode_status='unsupported'").fetchone()[0]}
        summary = {'config':asdict(config),'coverage':coverage,'conversations':[{key:value for key,value in entry.items() if key not in ('cache_key',)} for entry in entries],
                   'total_messages':sum(e['metrics']['message_count'] for e in entries),'cache':counts}
        atomic_write(root/'summary.json',dumps(summary)+'\n')
        atomic_write(root/'index.html',report_html(entries,root,config,coverage))
        atomic_write(output/'index.html',f'<!doctype html><meta charset="utf-8"><title>Py-Message</title><a href="reports/{config_id}/index.html">Open the latest Py-Message report</a>')
    return summary,root/'index.html'


def report_html(entries, root, config, coverage=None):
    rows = []
    for entry in entries:
        metrics = entry['metrics']
        relative = Path(entry['directory']).relative_to(root).as_posix()
        # All source-provided labels are HTML-escaped; no remote scripts/assets.
        links = [f'<a href="{escape(relative,quote=True)}/statistics.json">Statistics JSON</a>']
        if config.export_csv:
            links.insert(0,f'<a href="{escape(relative,quote=True)}/messages.csv">Messages CSV</a>')
        links += [f'<a href="{escape(relative,quote=True)}/{escape(name,quote=True)}">{escape(name)}</a>' for name in entry['charts']]
        warnings = []
        for label,key in [('undecoded bodies','unsupported'),('invalid timestamps','invalid_timestamps'),('missing attachments','missing_attachments'),('duplicate rows excluded','duplicates')]:
            if metrics[key]:
                warnings.append(f'{metrics[key]} {label}')
        rows.append(f'<section><h2>{escape(entry["name"])}</h2><p>{metrics["message_count"]:,} messages · {metrics["word_count"]:,} tokens · {metrics["attachments"]:,} attachments · {metrics["reaction_events"]:,} reaction events</p><p>{" · ".join(links)}</p><p class="warning">{escape("; ".join(warnings))}</p></section>')
    warning = '<p class="warning"><strong>Partial coverage.</strong> Some records could not be fully interpreted. Original source data is retained. See <a href="../../latest-run.json">run diagnostics</a>.</p>' if coverage and any(coverage.values()) else ''
    return '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Py-Message report</title><style>body{font:16px system-ui;max-width:1000px;margin:40px auto;padding:0 20px;background:#fafafa;color:#17202a}section{padding:16px 24px;background:white;border:1px solid #ddd;border-radius:12px;margin:16px 0}a{color:#145ac6}.warning{color:#8c4200}h1,h2{line-height:1.2}</style><h1>Py-Message</h1><p>'+escape(f'Local archive report · timezone {config.timezone} · token rules unicode-1; CJK counted by character. Read/delivery receipts are separate from conversational response times.')+'</p>'+warning+''.join(rows or ['<p>No conversations match this view.</p>'])+'</html>'
