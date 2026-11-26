from pathlib import Path
import sqlite3

SCHEMA = '''
CREATE TABLE message (ROWID INTEGER PRIMARY KEY,guid TEXT,text TEXT,date INTEGER,is_from_me INTEGER,
 handle_id INTEGER,service TEXT,date_read INTEGER,date_delivered INTEGER,thread_originator_guid TEXT,
 date_edited INTEGER,attributedBody BLOB,message_summary_info BLOB,associated_message_guid TEXT,
 associated_message_type INTEGER DEFAULT 0,associated_message_emoji TEXT,item_type INTEGER DEFAULT 0);
CREATE TABLE chat (ROWID INTEGER PRIMARY KEY,guid TEXT,display_name TEXT,chat_identifier TEXT);
CREATE TABLE handle (ROWID INTEGER PRIMARY KEY,id TEXT,service TEXT);
CREATE TABLE chat_handle_join (chat_id INTEGER,handle_id INTEGER);
CREATE TABLE chat_message_join (chat_id INTEGER,message_id INTEGER,message_date INTEGER);
CREATE TABLE attachment (ROWID INTEGER PRIMARY KEY,guid TEXT,filename TEXT,mime_type TEXT,total_bytes INTEGER);
CREATE TABLE message_attachment_join (message_id INTEGER,attachment_id INTEGER);
CREATE TABLE custom_metadata (id INTEGER PRIMARY KEY,blob BLOB,value TEXT);
'''
BASE = 700_000_000_000_000_000


def create_database(path, count=100, chats=1):
    path = Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    connection = sqlite3.connect(path)
    connection.executescript(SCHEMA)
    connection.executemany('INSERT INTO chat VALUES(?,?,?,?)',((i,f'chat-{i}',f'Synthetic {i}',f'user{i}@example.invalid') for i in range(1,chats+1)))
    connection.executemany('INSERT INTO handle VALUES(?,?,?)',((i,f'user{i}@example.invalid','iMessage') for i in range(1,chats+1)))
    connection.executemany('INSERT INTO chat_handle_join VALUES(?,?)',((i,i) for i in range(1,chats+1)))
    connection.execute('INSERT INTO custom_metadata VALUES(1,?,?)',(b'\x00\xffpreserve me','unknown future field'))
    for start in range(1,count+1,2000):
        stop = min(start+2000,count+1)
        connection.executemany('INSERT INTO message(ROWID,guid,text,date,is_from_me,handle_id,service,date_read,date_delivered) VALUES(?,?,?,?,?,?,?,?,?)',
            ((i,f'guid-{i}','Hello café 你好 🙂 well-being don’t stop '+str(i%20),BASE+i*60*10**9,i%2,(i-1)%chats+1,'iMessage',BASE+i*60*10**9+10**9,BASE+i*60*10**9+500_000_000) for i in range(start,stop)))
        connection.executemany('INSERT INTO chat_message_join VALUES(?,?,?)',(((i-1)%chats+1,i,BASE+i*60*10**9) for i in range(start,stop)))
    connection.commit()
    connection.close()
    return path
