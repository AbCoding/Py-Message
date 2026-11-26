//! A narrow, versioned JSONL bridge to the upstream iMessage body decoder.
use imessage_database::{
    tables::{messages::{Message, models::BubbleComponent}, table::get_connection},
    rusqlite::params_from_iter,
};
use serde_json::{Value, json};
use std::{env, fs::File, io::{self, BufRead, BufReader, BufWriter, Write}, path::Path};

fn parts(components: &[BubbleComponent], text: &str) -> Vec<Value> {
    components.iter().map(|part| match part {
        BubbleComponent::Run(ranges) => json!({"kind":"run", "ranges":ranges.iter().map(|r| json!({
            "start":r.start, "end":r.end,
            "text":if r.attachment.is_none() {text.get(r.start..r.end)} else {None},
            "effects":r.effects.iter().map(|e| format!("{e:?}")).collect::<Vec<_>>(),
            "attachment":r.attachment.as_ref().map(|a| format!("{a:?}")), "emoji_image":r.emoji_image
        })).collect::<Vec<_>>() }),
        BubbleComponent::App => json!({"kind":"app"}),
        BubbleComponent::Retracted => json!({"kind":"retracted"}),
    }).collect()
}

fn run() -> Result<(), Box<dyn std::error::Error>> {
    let args: Vec<String> = env::args().collect();
    if args.get(1).map(String::as_str) == Some("--version") {
        println!("pymessage-decoder 0.2.0 protocol=1 upstream=5.0.1"); return Ok(());
    }
    if args.len()!=3 { return Err("usage: pymessage-decoder SNAPSHOT.db ROWIDS.txt".into()); }
    let db = get_connection(Path::new(&args[1]))?;
    let columns: Vec<String> = db.prepare("PRAGMA table_info(message)")?.query_map([], |r| r.get(1))?.collect::<Result<_,_>>()?;
    let projection = columns.iter().filter(|c| !c.eq_ignore_ascii_case("rowid")).map(|c| {
        let quoted = format!("m.\"{}\"", c.replace('"', "\"\""));
        if c=="attributedBody" || c=="message_summary_info" {format!("CAST({quoted} AS BLOB) AS \"{c}\"")} else {quoted}
    }).collect::<Vec<_>>().join(",");
    let mut stdout = BufWriter::new(io::stdout().lock());
    let mut lines = BufReader::new(File::open(&args[2])?).lines();
    loop {
        let mut ids=Vec::with_capacity(400);
        for line in lines.by_ref().take(400) { ids.push(line?.parse::<i64>()?); }
        if ids.is_empty() { break; }
        ids.sort_unstable();
        ids.dedup();
        let placeholders=vec!["?";ids.len()].join(",");
        let mut defaults=String::new();
        for (field, expr) in [("guid","CAST(m.ROWID AS TEXT)"),("date","0"),("is_from_me","0")] {
            if !columns.iter().any(|c| c.eq_ignore_ascii_case(field)) { defaults.push_str(&format!(",{expr} AS {field}")); }
        }
        let sql=format!("SELECT m.ROWID AS rowid,{projection}{defaults},0 AS num_attachments,0 AS num_replies FROM message m WHERE m.ROWID IN ({placeholders}) ORDER BY m.ROWID");
        let mut statement=db.prepare(&sql)?;
        // Query each bounded batch through the upstream schema-aware decoder.
        // Layout failures stop the bridge; Python retains all source records.
        for (offset,result) in Message::rows(&mut statement,params_from_iter(ids.iter()))?.enumerate() {
            let m=match result {
                Ok(message) => message,
                Err(error) => {
                    serde_json::to_writer(&mut stdout,&json!({"protocol":1,"rowid":ids[offset],"error":error.to_string()}))?;
                    stdout.write_all(b"\n")?;
                    continue;
                }
            };
            let edits=m.edited_parts.as_ref().map(|e| e.parts.iter().enumerate().map(|(index,p)|json!({
                "part":index,"status":format!("{:?}",p.status),"events":p.edit_history.iter().map(|h|json!({
                    "date_raw":h.date,"text":h.text,"guid":h.guid,"parts":parts(&h.components,&h.text)
                })).collect::<Vec<_>>()
            })).collect::<Vec<_>>());
            let value=json!({"protocol":1,"rowid":m.rowid,"guid":m.guid,"text":m.text,
                "parts":parts(&m.components,m.text.as_deref().unwrap_or("")),"edits":edits});
            serde_json::to_writer(&mut stdout,&value)?; stdout.write_all(b"\n")?;
        }
        stdout.flush()?;
    }
    Ok(())
}
fn main(){ if let Err(e)=run(){eprintln!("decoder error: {e}");std::process::exit(1);} }
