//! Outlook messages saved as .msg.
//!
//! A .msg file is an OLE compound file holding MAPI properties
//! ([MS-OXMSG]). This reads the properties that matter (sender, recipients,
//! dates, subject, body, attachments). Bodies stored only as compressed RTF
//! are decompressed and unwrapped (see rtf.rs).

use std::collections::{BTreeSet, HashMap};
use std::fs::File;
use std::io::Read;
use std::path::{Path, PathBuf};

use cfb::CompoundFile;
use chrono::{TimeZone, Utc};
use encoding_rs::Encoding;
use mail_parser::MessageParser;

use super::email::{iso, render, Email, EmailDate};
use super::eml::addresses;
use super::markdown::safe_name;
use super::rtf;
use super::{Attachment, ConvResult, Converted, ConversionError, Options};

const PT_UNICODE: u16 = 0x001F;
const PT_STRING8: u16 = 0x001E;
const PT_BINARY: u16 = 0x0102;
const PT_LONG: u16 = 0x0003;
const PT_SYSTIME: u16 = 0x0040;
const PT_BOOLEAN: u16 = 0x000B;

const SUBJECT: u16 = 0x0037;
const SENDER_NAME: u16 = 0x0C1A;
const SENDER_EMAIL: u16 = 0x0C1F;
const SENDER_SMTP: u16 = 0x5D01;
const DISPLAY_TO: u16 = 0x0E04;
const DISPLAY_CC: u16 = 0x0E03;
const BODY: u16 = 0x1000;
const HTML: u16 = 0x1013;
const RTF_COMPRESSED: u16 = 0x1009;
const MESSAGE_ID: u16 = 0x1035;
const IN_REPLY_TO: u16 = 0x1042;
const HEADERS: u16 = 0x007D;
const TOPIC: u16 = 0x0070;
const SUBMIT_TIME: u16 = 0x0039;
const DELIVERY_TIME: u16 = 0x0E06;
const CODEPAGE: u16 = 0x3FFD;
const INTERNET_CPID: u16 = 0x3FDE;
const RECIP_NAME: u16 = 0x3001;
const RECIP_EMAIL: u16 = 0x3003;
const RECIP_SMTP: u16 = 0x39FE;
const RECIP_TYPE: u16 = 0x0C15;
const ATTACH_LONG_NAME: u16 = 0x3707;
const ATTACH_NAME: u16 = 0x3704;
const ATTACH_DISPLAY: u16 = 0x3001;
const ATTACH_DATA: u16 = 0x3701;
const ATTACH_METHOD: u16 = 0x3705;
const ATTACH_CID: u16 = 0x3712;
const ATTACH_HIDDEN: u16 = 0x7FFE;
const ATTACH_EMBEDDED: i64 = 5;
const EMBEDDED_STORAGE: &str = "__substg1.0_3701000D";

type Ole = CompoundFile<File>;

fn filetime(value: u64) -> Option<EmailDate> {
    if value == 0 {
        return None;
    }
    let secs = (value / 10_000_000) as i64 - 11_644_473_600;
    let nanos = ((value % 10_000_000) * 100) as u32;
    let at = Utc.timestamp_opt(secs, nanos).single()?.fixed_offset();
    Some(EmailDate { at, naive: false })
}

/// The properties of one message, recipient or attachment: a storage path
/// inside the compound file.
struct Store {
    prefix: PathBuf,
    codepage: &'static Encoding,
    fixed: HashMap<u16, (u16, [u8; 8])>,
}

fn stream(ole: &mut Ole, path: &Path) -> Option<Vec<u8>> {
    if !ole.is_stream(path) {
        return None;
    }
    let mut s = ole.open_stream(path).ok()?;
    let mut buf = Vec::new();
    s.read_to_end(&mut buf).ok()?;
    Some(buf)
}

impl Store {
    fn new(ole: &mut Ole, prefix: PathBuf, header: usize) -> Store {
        // Fixed-size properties (numbers, dates, flags) from the properties
        // stream: 16 bytes each after a header.
        let data = stream(ole, &prefix.join("__properties_version1.0")).unwrap_or_default();
        let mut fixed = HashMap::new();
        let mut off = header;
        while off + 16 <= data.len() {
            let tag = u32::from_le_bytes(data[off..off + 4].try_into().unwrap());
            let mut v = [0u8; 8];
            v.copy_from_slice(&data[off + 8..off + 16]);
            fixed.insert((tag >> 16) as u16, ((tag & 0xFFFF) as u16, v));
            off += 16;
        }
        Store { prefix, codepage: encoding_rs::WINDOWS_1252, fixed }
    }

    fn raw(&self, ole: &mut Ole, prop: u16, kind: u16) -> Option<Vec<u8>> {
        stream(ole, &self.prefix.join(format!("__substg1.0_{prop:04X}{kind:04X}")))
    }

    fn string(&self, ole: &mut Ole, prop: u16) -> String {
        if let Some(raw) = self.raw(ole, prop, PT_UNICODE) {
            let units: Vec<u16> = raw.chunks_exact(2).map(|c| u16::from_le_bytes([c[0], c[1]])).collect();
            return String::from_utf16_lossy(&units).trim_end_matches('\0').to_string();
        }
        if let Some(raw) = self.raw(ole, prop, PT_STRING8) {
            return rtf::decode_bytes(&raw, self.codepage).trim_end_matches('\0').to_string();
        }
        String::new()
    }

    fn binary(&self, ole: &mut Ole, prop: u16) -> Option<Vec<u8>> {
        self.raw(ole, prop, PT_BINARY)
    }

    fn number(&self, prop: u16) -> Option<i64> {
        let (kind, v) = self.fixed.get(&prop)?;
        match *kind {
            PT_LONG | PT_BOOLEAN => Some(i32::from_le_bytes(v[..4].try_into().unwrap()) as i64),
            PT_SYSTIME => Some(u64::from_le_bytes(*v) as i64),
            _ => None,
        }
    }

    fn date(&self, prop: u16) -> Option<EmailDate> {
        match self.fixed.get(&prop) {
            Some((PT_SYSTIME, v)) => filetime(u64::from_le_bytes(*v)),
            _ => None,
        }
    }

    /// Storages for recipients (__recip_) or attachments (__attach_).
    fn children(&self, ole: &Ole, kind: &str) -> Vec<PathBuf> {
        let Ok(entries) = ole.read_storage(&self.prefix) else { return Vec::new() };
        let names: BTreeSet<String> = entries.filter(|e| e.is_storage() && e.name().starts_with(kind)).map(|e| e.name().to_string()).collect();
        names.into_iter().map(|n| self.prefix.join(n)).collect()
    }
}

fn codepage(store: &Store) -> &'static Encoding {
    for prop in [INTERNET_CPID, CODEPAGE] {
        if let Some(cp) = store.number(prop).filter(|c| *c > 0) {
            return rtf::codepage(cp as u32);
        }
    }
    encoding_rs::WINDOWS_1252
}

fn addr(name: &str, address: &str) -> String {
    if !name.is_empty() && !address.is_empty() && name != address {
        format!("{name} <{address}>")
    } else if address.is_empty() {
        name.to_string()
    } else {
        address.to_string()
    }
}

fn split_display(s: &str) -> Vec<String> {
    s.split(';').map(str::trim).filter(|s| !s.is_empty()).map(str::to_string).collect()
}

fn read(ole: &mut Ole, prefix: PathBuf, header: usize, attachments: &mut Vec<Attachment>, depth: usize) -> Email {
    let mut store = Store::new(ole, prefix, header);
    store.codepage = codepage(&store);
    let mut e = Email {
        subject: store.string(ole, SUBJECT).trim().to_string(),
        date: store.date(SUBMIT_TIME).or_else(|| store.date(DELIVERY_TIME)),
        message_id: store.string(ole, MESSAGE_ID).trim().to_string(),
        in_reply_to: store.string(ole, IN_REPLY_TO).trim().to_string(),
        thread: store.string(ole, TOPIC).trim().to_string(),
        ..Default::default()
    };

    let mut sender_addr = store.string(ole, SENDER_SMTP);
    if sender_addr.is_empty() {
        sender_addr = store.string(ole, SENDER_EMAIL);
    }
    if sender_addr.to_lowercase().starts_with("/o=") {
        sender_addr.clear(); // an Exchange internal address, not useful to a reader
    }
    let headers = store.string(ole, HEADERS);
    let parsed = (!headers.is_empty()).then(|| MessageParser::default().parse_headers(headers.as_bytes())).flatten();
    let header_from = parsed.as_ref().map(|p| addresses(p.from())).and_then(|a| a.into_iter().next());
    e.sender = match header_from {
        Some(f) if sender_addr.is_empty() => f,
        _ => addr(&store.string(ole, SENDER_NAME), &sender_addr),
    };
    if e.message_id.is_empty() {
        if let Some(p) = &parsed {
            e.message_id = p.header_raw("Message-ID").map(|s| s.trim().to_string()).unwrap_or_default();
        }
    }

    for rpath in store.children(ole, "__recip_version1.0_") {
        let mut r = Store::new(ole, rpath, 8);
        r.codepage = store.codepage;
        let mut a = r.string(ole, RECIP_SMTP);
        if a.is_empty() {
            a = r.string(ole, RECIP_EMAIL);
        }
        if a.to_lowercase().starts_with("/o=") {
            a.clear();
        }
        let value = addr(&r.string(ole, RECIP_NAME), &a);
        if value.is_empty() {
            continue;
        }
        match r.number(RECIP_TYPE).filter(|k| *k != 0).unwrap_or(1) {
            1 => e.to.push(value),
            2 => e.cc.push(value),
            _ => e.bcc.push(value),
        }
    }
    if e.to.is_empty() && e.cc.is_empty() {
        // Messages without recipient storages still have the display lines.
        e.to = split_display(&store.string(ole, DISPLAY_TO));
        e.cc = split_display(&store.string(ole, DISPLAY_CC));
        if e.to.is_empty() {
            if let Some(p) = &parsed {
                e.to = addresses(p.to());
            }
        }
    }

    let body = store.string(ole, BODY);
    let html = store.binary(ole, HTML).filter(|h| !h.is_empty()).or_else(|| Some(store.string(ole, HTML).into_bytes()).filter(|h| !h.is_empty()));
    if !body.trim().is_empty() {
        e.body_text = Some(body);
    } else if let Some(h) = html {
        e.body_html = Some(if h.starts_with(b"\xef\xbb\xbf") { String::from_utf8_lossy(&h[3..]).into_owned() } else { rtf::decode_bytes(&h, store.codepage) });
    } else if let Some(compressed) = store.binary(ole, RTF_COMPRESSED) {
        // Damaged RTF: carry on without a body.
        if let Some(raw) = rtf::decompress(&compressed) {
            let raw: String = raw.iter().map(|&b| b as char).collect(); // latin-1
            match rtf::decode(&raw) {
                ("html", c) => e.body_html = Some(c),
                (_, c) => e.body_text = Some(c),
            }
        }
    }

    for apath in store.children(ole, "__attach_version1.0_") {
        let mut a = Store::new(ole, apath.clone(), 8);
        a.codepage = store.codepage;
        let mut raw_name = a.string(ole, ATTACH_LONG_NAME);
        if raw_name.is_empty() {
            raw_name = a.string(ole, ATTACH_NAME);
        }
        if raw_name.is_empty() {
            raw_name = a.string(ole, ATTACH_DISPLAY);
        }
        let name = safe_name(if raw_name.is_empty() { "attachment" } else { &raw_name }, "attachment");
        let inner_path = apath.join(EMBEDDED_STORAGE);
        if a.number(ATTACH_METHOD) == Some(ATTACH_EMBEDDED) || ole.exists(&inner_path) {
            if depth < 3 && ole.is_storage(&inner_path) {
                let inner = read(ole, inner_path, 24, attachments, depth + 1);
                let shown = if name != "attachment" {
                    name
                } else {
                    format!("{}.msg", if inner.subject.is_empty() { "attached email" } else { &inner.subject })
                };
                e.attachments.push(shown);
                e.embedded.push(inner);
            }
            continue;
        }
        let Some(data) = a.binary(ole, ATTACH_DATA) else { continue };
        let lower = name.to_lowercase();
        let picture = [".png", ".jpg", ".jpeg", ".gif", ".bmp"].iter().any(|x| lower.ends_with(x));
        if !a.string(ole, ATTACH_CID).is_empty() && picture && a.number(ATTACH_HIDDEN).unwrap_or(0) != 0 {
            continue; // a picture in the body (a logo), not an attachment
        }
        e.attach(name, data, attachments);
    }
    e
}

pub fn convert(path: &Path, _opts: &Options) -> Converted {
    let is_ole = {
        let mut head = [0u8; 8];
        File::open(path).and_then(|mut f| f.read_exact(&mut head)).is_ok() && head == [0xD0, 0xCF, 0x11, 0xE0, 0xA1, 0xB1, 0x1A, 0xE1]
    };
    if !is_ole {
        return Err(ConversionError::new("This file isn't an Outlook message, even though it ends in .msg. Open it on your computer to check it."));
    }
    let damaged = || ConversionError::new("This Outlook message is damaged and can't be read. Open it in Outlook and save it again.");
    let mut ole = cfb::open(path).map_err(|_| damaged())?;
    let mut attachments = Vec::new();
    let e = read(&mut ole, PathBuf::from("/"), 32, &mut attachments, 0);
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
