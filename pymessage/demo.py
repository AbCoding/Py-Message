"""Explicitly fictional, isolated data for exploring the app."""
from copy import copy
from datetime import datetime, timedelta, timezone
import sqlite3


def prepare(original):
    args = copy(original)
    args.output = original.output.expanduser().resolve()/'demo'
    args.archive = args.output/'.state/archive.sqlite'
    args.db_path = args.output/'demo-source.db'
    args.html_dir = None
    args.source_id = 'pymessage-fictional-demo'
    args.output.mkdir(parents=True,exist_ok=True)
    if args.db_path.exists():
        return args
    with sqlite3.connect(args.db_path) as db:
        db.executescript('''
            CREATE TABLE message (ROWID INTEGER PRIMARY KEY,guid TEXT,text TEXT,date INTEGER,is_from_me INTEGER,handle_id INTEGER,service TEXT);
            CREATE TABLE chat (ROWID INTEGER PRIMARY KEY,guid TEXT,display_name TEXT);
            CREATE TABLE handle (ROWID INTEGER PRIMARY KEY,id TEXT);
            CREATE TABLE chat_handle_join (chat_id INTEGER,handle_id INTEGER);
            CREATE TABLE chat_message_join (chat_id INTEGER,message_id INTEGER);
        ''')
        chats = [
            ('Alex Morgan',['alex@example.invalid'],['Coffee and a long walk this weekend?','Absolutely. Same little place on the corner?','Yes! Their coffee is so good.','Perfect, I’ll meet you at 10 ☕','Sending you the playlist for the walk','This is exactly the vibe 🌿','Saturday can’t come soon enough','A slow morning, coffee, and no plans. Ideal.']),
            ('The weekend crew',['maya@example.invalid','jordan@example.invalid','sam@example.invalid'],['Who’s up for a hike this weekend? 🏔','Count me in!','I’ll bring snacks for everyone','Weather looks perfect for Saturday','Let’s take the trail by the lake','I can drive! Meet at nine?','Nine works. Coffee on the way?','Always yes to coffee']),
            ('Jamie Chen',['jamie@example.invalid'],['Have you tried that new ramen place?','Not yet, but it’s on my list','Dinner on Thursday?','Yes please! 🍜','I booked a table for seven','Amazing. See you there','That broth was unbelievable','We’re definitely going back']),
            ('Mom',['mom@example.invalid'],['How’s your week going?','Really good! Working on a little project','Can’t wait to hear all about it ❤️','I’ll call you this evening','Made your favorite soup today','Save me some for Sunday!','Of course. See you soon','Love you ❤️']),
            ('Design club',['lee@example.invalid','robin@example.invalid'],['A few ideas for the next project','Love the blue in this one','What if we simplify the layout?','Yes, a little more breathing room','The prototype feels so much better now','Let’s try it with real content','Good call. I’ll put together a demo','Excited to see where this goes ✨']),
        ]
        handle_id = 0
        message_id = 0
        now = datetime.now(timezone.utc)
        for chat_id,(name,addresses,lines) in enumerate(chats,1):
            db.execute('INSERT INTO chat VALUES(?,?,?)',(chat_id,f'demo-chat-{chat_id}',name))
            handles = []
            for address in addresses:
                handle_id += 1
                handles.append(handle_id)
                db.execute('INSERT INTO handle VALUES(?,?)',(handle_id,address))
                db.execute('INSERT INTO chat_handle_join VALUES(?,?)',(chat_id,handle_id))
            for turn in range(160):
                message_id += 1
                stamp = now-timedelta(days=(159-turn)//8*2,hours=chat_id,minutes=(7-turn%8)*3)
                apple_ns = int((stamp.timestamp()-978307200)*10**9)
                db.execute('INSERT INTO message VALUES(?,?,?,?,?,?,?)',
                           (message_id,f'demo-{message_id}',lines[turn%len(lines)],apple_ns,turn%2,handles[(turn//2)%len(handles)],'iMessage'))
                db.execute('INSERT INTO chat_message_join VALUES(?,?)',(chat_id,message_id))
    return args
