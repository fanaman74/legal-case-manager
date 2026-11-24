//! Shared by the .eml, .msg and PST converters: one email as Markdown with
//! a header block, the new message, and earlier quoted messages marked off.

use std::sync::LazyLock;

use chrono::{DateTime, FixedOffset};
use regex::Regex;
use serde_json::{json, Map, Value};

use super::html;
use super::markdown::{escape, tidy};

/// File types the converters can read; other attachments stay inside the email.
pub const CONVERTIBLE: [&str; 10] = [".docx", ".pdf", ".msg", ".eml", ".txt", ".png", ".jpg", ".jpeg", ".tif", ".tiff"];

static REPLY_STARTS: LazyLock<Vec<Regex>> = LazyLock::new(|| {
    [
        r"(?i)^-{2,}\s*Original Message\s*-{2,}\s*$",
        r"(?i)^-{2,}\s*Forwarded message\s*-{2,}\s*$",
        r"(?i)^Begin forwarded message:\s*$",
        r"^_{10,}\s*$",
    ]
    .iter()
    .map(|p| Regex::new(p).unwrap())
    .collect()
});
static ON_WROTE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"(?i)^(On|Le|Am|El|Op)\b.{4,200}(wrote|a écrit|schrieb|escribió|schreef)\s*:\s*$").unwrap());
static ON_START: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^(On|Le|Am)\b").unwrap());
static HEADER_LINE: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"(?i)^\**(From|Von|De|Van)\s*:\**\s").unwrap());
static HEADER_FOLLOW: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"(?i)^\**(Sent|Date|Gesendet|Envoyé|Enviado|Verzonden|To|Subject|Cc)\s*:").unwrap());
static SUBJECT_PREFIX: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"(?i)^\s*((re|fw|fwd|aw|wg|tr|sv|vs|rv)\s*(\[\d+\])?\s*:\s*)+").unwrap());

/// A date as the email gave it. `naive` is a date with no time zone ("-0000").
#[derive(Clone, Copy, Debug)]
pub struct EmailDate {
    pub at: DateTime<FixedOffset>,
    pub naive: bool,
}

#[derive(Default, Debug)]
pub struct Email {
    pub subject: String,
    pub sender: String,
    pub to: Vec<String>,
    pub cc: Vec<String>,
    pub bcc: Vec<String>,
    pub date: Option<EmailDate>,
    pub message_id: String,
    #[allow(dead_code)] // kept for mailboxes (PST), as in the Python version
    pub in_reply_to: String,
    pub thread: String,
    pub body_text: Option<String>,
    pub body_html: Option<String>,
    /// Names, for the header block.
    pub attachments: Vec<String>,
    /// Attachments not converted, with the reason.
    pub skipped: Vec<String>,
    /// Emails attached inside this one.
    pub embedded: Vec<Email>,
}

impl Email {
    pub fn meta(&self) -> Map<String, Value> {
        let mut m = Map::new();
        m.insert("from".into(), json!(self.sender));
        m.insert("to".into(), json!(self.to));
        m.insert("cc".into(), json!(self.cc));
        m.insert("date".into(), json!(iso(self.date)));
        m.insert("subject".into(), json!(self.subject));
        m.insert("message_id".into(), json!(self.message_id));
        m.insert("thread".into(), json!(if self.thread.is_empty() { thread_of(&self.subject) } else { self.thread.clone() }));
        m
    }

    pub fn has_body(&self) -> bool {
        self.body_text.is_some() || self.body_html.is_some()
    }

    /// Note an attachment: converted ones go to `out`, others are listed as skipped.
    pub fn attach(&mut self, name: String, data: Vec<u8>, out: &mut Vec<super::Attachment>) {
        let ext = name.rsplit_once('.').map(|(_, e)| format!(".{}", e.to_lowercase())).unwrap_or_default();
        self.attachments.push(name.clone());
        if CONVERTIBLE.contains(&ext.as_str()) {
            out.push(super::Attachment { name, data });
        } else {
            self.skipped.push(format!(
                "{name} ({} files aren't converted; it stays inside the original email)",
                if ext.is_empty() { "no extension" } else { &ext }
            ));
        }
    }
}

/// 2024-04-16T09:30:00+01:00 (a date with no zone is taken as UTC).
pub fn iso(d: Option<EmailDate>) -> Option<String> {
    d.map(|d| d.at.format("%Y-%m-%dT%H:%M:%S%:z").to_string())
}

pub fn thread_of(subject: &str) -> String {
    SUBJECT_PREFIX.replace(subject, "").trim().to_string()
}

fn lines_of(text: &str) -> Vec<String> {
    text.replace("\r\n", "\n").replace('\r', "\n").split('\n').map(str::to_string).collect()
}

/// (new message, earlier quoted messages). Recognises the usual reply and
/// forward separators from Outlook, Gmail and Apple Mail, and '>' quoting.
pub fn split_quoted(text: &str) -> (String, String) {
    let lines = lines_of(text);
    let cut = |i: usize| (lines[..i].join("\n"), lines[i..].join("\n"));
    for (i, line) in lines.iter().enumerate() {
        let s = line.trim();
        if REPLY_STARTS.iter().any(|p| p.is_match(s)) || ON_WROTE.is_match(s) {
            return cut(i);
        }
        // "On Tue, 5 Mar 2024 at 14:22, Jane Doe <jane@x.com>" + "wrote:" on the next line
        if i + 1 < lines.len() && ON_START.is_match(s) {
            let next = lines[i + 1].trim().to_lowercase();
            if ["wrote:", "a écrit", "schrieb"].iter().any(|w| next.starts_with(w)) {
                return cut(i);
            }
        }
        if HEADER_LINE.is_match(s) && i > 0 {
            let follow = lines[i + 1..lines.len().min(i + 5)].iter().filter(|l| HEADER_FOLLOW.is_match(l.trim())).count();
            if follow >= 2 {
                return cut(i);
            }
        }
        if s.starts_with('>') && i > 0 {
            let rest: Vec<&String> = lines[i..].iter().filter(|l| !l.trim().is_empty()).collect();
            let quoted = rest.iter().filter(|l| l.trim_start().starts_with('>')).count();
            if !rest.is_empty() && quoted as f64 >= 0.8 * rest.len() as f64 {
                return cut(i);
            }
        }
    }
    (text.to_string(), String::new())
}

fn quote(text: &str) -> String {
    text.trim_matches('\n').split('\n').map(|l| if l.trim().is_empty() { ">".to_string() } else { format!("> {l}") }).collect::<Vec<_>>().join("\n")
}

/// Plain text email body: keep line breaks (they matter in signatures and
/// addresses) and escape Markdown syntax.
fn text_to_md(text: &str) -> String {
    let lines: Vec<String> = lines_of(text).iter().map(|l| escape(l.trim_end())).collect();
    let mut out = Vec::with_capacity(lines.len());
    for (i, l) in lines.iter().enumerate() {
        let next_has = lines.get(i + 1).is_some_and(|n| !n.is_empty());
        out.push(if !l.is_empty() && next_has { format!("{l}  ") } else { l.clone() });
    }
    out.join("\n")
}

/// (new message, quoted earlier messages) as Markdown.
pub fn body_markdown(e: &Email) -> (String, String) {
    if let Some(t) = e.body_text.as_deref().filter(|t| !t.trim().is_empty()) {
        let (new, quoted) = split_quoted(t);
        let q = if quoted.trim().is_empty() { String::new() } else { quote(&text_to_md(&quoted)) };
        return (text_to_md(&new), q);
    }
    if let Some(h) = &e.body_html {
        let md = html::to_markdown(h);
        let (new, quoted) = split_quoted(&md);
        let q = if quoted.trim().is_empty() { String::new() } else { quote(&quoted) };
        return (new, q);
    }
    (String::new(), String::new())
}

fn names(addrs: &[String]) -> String {
    addrs.iter().filter(|a| !a.is_empty()).map(|a| escape(a)).collect::<Vec<_>>().join("; ")
}

/// "Tue, 16 Apr 2024 09:30 UTC+01:00", like Python's strftime with %Z.
fn shown_date(d: &EmailDate) -> String {
    let base = d.at.format("%a, %d %b %Y %H:%M").to_string();
    if d.naive {
        return base;
    }
    let secs = d.at.offset().local_minus_utc();
    if secs == 0 {
        return format!("{base} UTC");
    }
    let sign = if secs < 0 { '-' } else { '+' };
    let a = secs.abs();
    format!("{base} UTC{sign}{:02}:{:02}", a / 3600, a % 3600 / 60)
}

pub fn render(e: &Email, level: usize) -> String {
    let h = "#".repeat(level);
    let subject = escape(&e.subject);
    let mut parts = vec![format!("{h} {}", if subject.is_empty() { "(no subject)" } else { &subject }), String::new()];
    let rows = [
        ("From", escape(&e.sender)),
        ("To", names(&e.to)),
        ("Cc", names(&e.cc)),
        ("Bcc", names(&e.bcc)),
        ("Date", e.date.as_ref().map(shown_date).unwrap_or_default()),
        ("Subject", subject.clone()),
        ("Attachments", e.attachments.iter().map(|a| escape(a)).collect::<Vec<_>>().join("; ")),
    ];
    parts.push(rows.iter().filter(|(_, v)| !v.is_empty()).map(|(k, v)| format!("**{k}:** {v}")).collect::<Vec<_>>().join("  \n"));
    parts.extend(["".into(), "---".into(), "".into()]);
    let (new, quoted) = body_markdown(e);
    let new = new.trim();
    parts.push(if new.is_empty() { "*(This email has no text.)*".into() } else { new.to_string() });
    if !quoted.is_empty() {
        parts.extend(["".into(), format!("{h}# Earlier messages in this thread (quoted)"), "".into(), quoted]);
    }
    if !e.skipped.is_empty() {
        parts.extend(["".into(), format!("{h}# Attachments not converted"), "".into()]);
        parts.extend(e.skipped.iter().map(|s| format!("- {}", escape(s))));
    }
    for sub in &e.embedded {
        parts.extend(["".into(), format!("{h}# Attached email"), "".into(), render(sub, (level + 2).min(6))]);
    }
    tidy(&parts.join("\n"))
}

#[cfg(test)]
mod tests {
    use super::split_quoted;

    #[test]
    fn reply_separators() {
        for sep in ["-----Original Message-----", "On Tue, 5 Mar 2024 at 14:22, Jane Doe <j@x.com> wrote:", "________________________________"] {
            let (new, quoted) = split_quoted(&format!("Thanks.\n\n{sep}\nolder text"));
            assert_eq!(new.trim(), "Thanks.");
            assert!(quoted.contains("older text"));
        }
        let (new, quoted) = split_quoted("Agreed.\n\nFrom: Bob\nSent: Monday\nTo: Jane\nSubject: x\n\nold");
        assert_eq!(new.trim(), "Agreed.");
        assert!(quoted.starts_with("From: Bob"));
        let t = "No quotes here.\nFrom: the start.";
        assert_eq!(split_quoted(t), (t.to_string(), String::new()));
    }
}
