//! Emails saved as .eml (RFC 5322).

use std::path::Path;

use chrono::{FixedOffset, TimeZone};
use mail_parser::{Address, Message, MessageParser, MimeHeaders, PartType};

use super::email::{iso, render, Email, EmailDate};
use super::markdown::safe_name;
use super::{Attachment, ConvResult, Converted, ConversionError, Options};

const MAX_BYTES: u64 = 500 << 20;

/// "Name <address>", or whichever of the two there is.
pub fn addresses(a: Option<&Address>) -> Vec<String> {
    let Some(a) = a else { return Vec::new() };
    a.iter()
        .filter_map(|x| {
            let name = x.name().map(str::trim).unwrap_or("");
            let addr = x.address().map(str::trim).unwrap_or("");
            match (name.is_empty(), addr.is_empty()) {
                (false, false) => Some(format!("{name} <{addr}>")),
                (true, true) => None,
                _ => Some(if addr.is_empty() { name } else { addr }.to_string()),
            }
        })
        .collect()
}

/// A header as text, folded lines joined.
pub fn header_text(m: &Message, name: &str) -> String {
    let v = m.header(name).and_then(|h| h.as_text()).map(str::to_string).or_else(|| m.header_raw(name).map(str::to_string));
    v.map(|s| s.split_whitespace().collect::<Vec<_>>().join(" ")).unwrap_or_default()
}

pub fn date_of(m: &Message) -> Option<EmailDate> {
    let d = m.date()?;
    let secs = (d.tz_hour as i32 * 3600 + d.tz_minute as i32 * 60) * if d.tz_before_gmt { -1 } else { 1 };
    // "-0000" means the zone is unknown.
    let naive = secs == 0 && d.tz_before_gmt;
    let tz = FixedOffset::east_opt(secs)?;
    let at = tz.with_ymd_and_hms(d.year as i32, d.month as u32, d.day as u32, d.hour as u32, d.minute as u32, d.second as u32).single()?;
    Some(EmailDate { at, naive })
}

fn text_of(part: &mail_parser::MessagePart) -> Option<String> {
    match &part.body {
        PartType::Text(t) | PartType::Html(t) => Some(t.to_string()),
        PartType::Binary(b) | PartType::InlineBinary(b) => Some(String::from_utf8_lossy(b).into_owned()),
        _ => None,
    }
}

/// One message. Converted attachments are added to `attachments`; attached
/// emails are rendered inside this one.
pub fn parse(msg: &Message, attachments: &mut Vec<Attachment>, depth: usize) -> Email {
    let mut e = Email {
        subject: msg.subject().unwrap_or("").trim().to_string(),
        sender: addresses(msg.from()).into_iter().next().unwrap_or_default(),
        to: addresses(msg.to()),
        cc: addresses(msg.cc()),
        bcc: addresses(msg.bcc()),
        date: date_of(msg),
        message_id: msg.header_raw("Message-ID").map(|s| s.trim().to_string()).unwrap_or_default(),
        in_reply_to: msg.header_raw("In-Reply-To").map(|s| s.trim().to_string()).unwrap_or_default(),
        thread: header_text(msg, "Thread-Topic"),
        ..Default::default()
    };

    let part = |id: &u32| msg.parts.get(*id as usize);
    let plain = msg.text_body.first().and_then(part).filter(|p| matches!(p.body, PartType::Text(_)));
    let html = msg.html_body.first().and_then(part).filter(|p| matches!(p.body, PartType::Html(_)));
    match (plain, html) {
        (Some(p), h) => {
            let content = text_of(p).unwrap_or_default();
            // Some mailers send a plain part that only says "view in HTML".
            match h {
                Some(h) if content.trim().chars().count() < 20 => e.body_html = text_of(h),
                _ => e.body_text = Some(content),
            }
        }
        (None, Some(h)) => e.body_html = text_of(h),
        (None, None) => {}
    }

    if matches!(msg.parts.first().map(|p| &p.body), Some(PartType::Multipart(_))) {
        for p in msg.attachments.iter().filter_map(part) {
            let name = p.attachment_name();
            if let PartType::Message(inner) = &p.body {
                if depth < 3 {
                    e.embedded.push(parse(inner, attachments, depth + 1));
                    let fallback = format!("{}.eml", inner.subject().unwrap_or(""));
                    e.attachments.push(safe_name(name.unwrap_or(&fallback), "attached email.eml"));
                }
                continue;
            }
            let ctype = p.content_type().map(|c| c.ctype().to_ascii_lowercase()).unwrap_or_default();
            let inline = p.content_disposition().is_none_or(|d| d.is_inline());
            if p.content_id().is_some() && ctype == "image" && inline {
                continue; // logos and pictures in the body, not attachments
            }
            let fname = safe_name(name.unwrap_or("attachment"), "attachment");
            e.attach(fname, p.contents().to_vec(), attachments);
        }
    }
    e
}

pub fn convert(path: &Path, _opts: &Options) -> Converted {
    let size = std::fs::metadata(path).map(|m| m.len()).unwrap_or(0);
    if size > MAX_BYTES {
        return Err(ConversionError::new("This email is larger than 500 MB, which is too large to convert."));
    }
    let data = std::fs::read(path).map_err(|_| ConversionError::new("This email can't be read. Upload it again."))?;
    let not_email = || ConversionError::new("This file doesn't look like an email. Open it on your computer to check it.");
    let msg = MessageParser::default().parse(&data).ok_or_else(not_email)?;
    if msg.parts.first().is_none_or(|p| p.headers.is_empty()) {
        return Err(not_email());
    }
    let mut attachments = Vec::new();
    let e = parse(&msg, &mut attachments, 0);
    Ok(ConvResult {
        body: render(&e, 1),
        meta: e.meta(),
        doc_date: iso(e.date),
        title: Some(e.subject.clone()).filter(|s| !s.is_empty()),
        warnings: if e.has_body() { Vec::new() } else { vec!["This email has no text body.".into()] },
        attachments,
        ..Default::default()
    })
}
