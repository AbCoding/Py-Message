"""Reproducible isolated-process full import, no-op, append, and old-update timings."""
import argparse
import json
from pathlib import Path
import platform
import resource
import sqlite3
import subprocess
import sys
import tempfile
import time

from benchmarks.synthetic import BASE, create_database


def total_bytes(path):
    return sum(item.stat().st_size for item in Path(path).rglob('*') if item.is_file())


def measure(source, output):
    start = time.perf_counter()
    command = [sys.executable,'-m','pymessage','--db-path',str(source),'--output',str(output),'--charts','none','--timezone','UTC']
    result = subprocess.run(command,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    if result.returncode:
        raise RuntimeError(result.stderr or result.stdout)
    summary = json.loads((output/'latest-run.json').read_text())
    return {'wall_seconds':round(time.perf_counter()-start,4),'stage_seconds':summary['timings_seconds'],
            'sync':summary['sync'],'cache':summary['cache'],'total_messages':summary['total_messages']}


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--sizes',type=int,nargs='+',default=[10000,100000,1000000])
    p.add_argument('--chats',type=int,default=1)
    p.add_argument('--output',type=Path,default=Path('benchmarks/results.json'))
    p.add_argument('--quick',action='store_true',help='First import/no-op only; skip append and historical-update measurements.')
    args = p.parse_args()
    result = {'python':platform.python_version(),'platform':platform.platform(),'chats':args.chats,'charts':'none','cases':[]}
    for size in args.sizes:
        with tempfile.TemporaryDirectory(prefix='pymessage-benchmark-') as temp:
            root = Path(temp).resolve()
            source = create_database(root/'source.db',size,args.chats)
            output = root/'results'
            case = {'messages':size,'source_bytes':source.stat().st_size,'first':measure(source,output)}
            case['unchanged'] = measure(source,output)
            if not args.quick:
                with sqlite3.connect(source) as db:
                    db.execute('INSERT INTO message(ROWID,guid,text,date,is_from_me,handle_id,service) VALUES(?,?,?,?,?,?,?)',
                               (size+1,f'guid-{size+1}','A newly appended message',BASE+(size+1)*60*10**9,1,1,'iMessage'))
                    db.execute('INSERT INTO chat_message_join VALUES(?,?,?)',(1,size+1,BASE+(size+1)*60*10**9))
                case['append'] = measure(source,output)
                with sqlite3.connect(source) as db:
                    db.execute('UPDATE message SET text=?,date_read=date_read+1000000000 WHERE ROWID=1',('An old message changed',))
                case['old_update'] = measure(source,output)
            case['output_bytes'] = total_bytes(output)
            # getrusage includes previous child maxima; recorded as a process-wide
            # high-water mark, not an isolated stage allocation measurement.
            rss = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
            case['child_peak_rss_mb'] = round(rss/(1024*1024 if sys.platform=='darwin' else 1024),2)
            result['cases'].append(case)
            args.output.parent.mkdir(parents=True,exist_ok=True)
            args.output.write_text(json.dumps(result,indent=2)+'\n')
            print(json.dumps(case),flush=True)


if __name__=='__main__':
    main()
