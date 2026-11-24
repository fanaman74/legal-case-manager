//! Just enough RTF to read Outlook message bodies.
//!
//! Outlook often stores a message body only as compressed RTF. That RTF is
//! either plain RTF or HTML wrapped in RTF ("\fromhtml1"). This turns the
//! first into text and unwraps the second back into HTML, following
//! Microsoft's de-encapsulation rules ([MS-OXRTFEX]) in simplified form.

use std::sync::LazyLock;

use encoding_rs::Encoding;
use regex::Regex;

const SKIP_DESTINATIONS: [&str; 34] = [
    "fonttbl", "colortbl", "stylesheet", "info", "pict", "object", "themedata", "colorschememapping", "latentstyles", "datastore",
    "xmlnstbl", "listtable", "listoverridetable", "rsidtbl", "generator", "mmathPr", "pgdsctbl", "header", "footer", "headerl",
    "headerr", "footerl", "footerr", "fldinst", "filetbl", "revtbl", "pnseclvl", "listtext", "pntext", "bkmkstart", "bkmkend",
    "nonshppict", "shp", "xe",
];

static TOKEN: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?s)\\([a-zA-Z]{1,32})(-?\d{1,10})? ?|\\'([0-9a-fA-F]{2})|\\(.)|([{}])|([^\\{}\r\n]+)|[\r\n]+").unwrap()
});
static ANSICPG: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"\\ansicpg(\d+)").unwrap());
static BLANKS: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"\n{3,}").unwrap());

/// A Windows code page number as a text encoding (cp1252 when unknown).
pub fn codepage(n: u32) -> &'static Encoding {
    let label = match n {
        65001 => "utf-8".to_string(),
        1200 => "utf-16le".to_string(),
        20127 | 28591 => "windows-1252".to_string(),
        932 => "shift_jis".to_string(),
        936 => "gbk".to_string(),
        949 => "euc-kr".to_string(),
        950 => "big5".to_string(),
        874 => "windows-874".to_string(),
        866 => "ibm866".to_string(),
        n if (28592..=28606).contains(&n) => format!("iso-8859-{}", n - 28590),
        n => format!("windows-{n}"),
    };
    Encoding::for_label(label.as_bytes()).unwrap_or(encoding_rs::WINDOWS_1252)
}

pub fn decode_bytes(data: &[u8], enc: &'static Encoding) -> String {
    enc.decode_without_bom_handling(data).0.into_owned()
}

#[derive(Clone, Copy)]
struct Group {
    skip: bool,
    htmlrtf: bool,
    uc: usize,
}

/// ("html", html) for HTML wrapped in RTF, else ("text", text).
pub fn decode(rtf: &str) -> (&'static str, String) {
    let head: String = rtf.chars().take(2000).collect();
    let from_html = head.chars().take(1000).collect::<String>().contains("\\fromhtml");
    let enc = ANSICPG.captures(&head).and_then(|c| c[1].parse().ok()).map(codepage).unwrap_or(encoding_rs::WINDOWS_1252);

    let mut out = String::new();
    let mut pending: Vec<u8> = Vec::new();
    let mut stack = vec![Group { skip: false, htmlrtf: false, uc: 1 }];
    let mut star = false;
    let mut skip_chars = 0usize;
    let flush = |pending: &mut Vec<u8>, out: &mut String| {
        if !pending.is_empty() {
            out.push_str(&decode_bytes(pending, enc));
            pending.clear();
        }
    };

    for m in TOKEN.captures_iter(rtf) {
        let (word, param, hexbyte, sym, brace, text) = (m.get(1), m.get(2), m.get(3), m.get(4), m.get(5), m.get(6));
        let state = *stack.last().unwrap();
        let hidden = state.skip || (from_html && state.htmlrtf);
        if let Some(b) = brace {
            if b.as_str() == "{" {
                stack.push(state);
            } else {
                flush(&mut pending, &mut out);
                if stack.len() > 1 {
                    stack.pop();
                }
            }
            star = false;
            continue;
        }
        let mut text = text.map(|t| t.as_str());
        if skip_chars > 0 && (text.is_some() || hexbyte.is_some()) {
            // Characters standing in for a \uN character are dropped.
            if let Some(t) = text {
                let n = skip_chars.min(t.chars().count());
                skip_chars -= n;
                let idx = t.char_indices().nth(n).map(|(i, _)| i).unwrap_or(t.len());
                if idx == t.len() {
                    continue;
                }
                text = Some(&t[idx..]);
            } else {
                skip_chars -= 1;
                continue;
            }
        }
        let state = stack.last_mut().unwrap();
        if let Some(w) = word {
            let w = w.as_str();
            let param = param.map(|p| p.as_str());
            if star && w != "htmltag" {
                state.skip = true;
            }
            star = false;
            if SKIP_DESTINATIONS.contains(&w) {
                state.skip = true;
            } else if w == "htmltag" {
                state.skip = false;
                state.htmlrtf = false;
            } else if w == "htmlrtf" {
                state.htmlrtf = param != Some("0");
            } else if hidden {
                continue;
            } else {
                let push = |s: &str, pending: &mut Vec<u8>, out: &mut String| {
                    flush(pending, out);
                    out.push_str(s);
                };
                match w {
                    "par" | "line" => push("\n", &mut pending, &mut out),
                    "tab" => push("\t", &mut pending, &mut out),
                    "u" => {
                        if let Some(n) = param.and_then(|p| p.parse::<i64>().ok()) {
                            let n = if n < 0 { n + 65536 } else { n };
                            let c = char::from_u32(n as u32).unwrap_or('\u{fffd}');
                            push(&c.to_string(), &mut pending, &mut out);
                            skip_chars = state.uc;
                        }
                    }
                    "uc" => {
                        if let Some(n) = param.and_then(|p| p.parse::<usize>().ok()) {
                            state.uc = n;
                        }
                    }
                    "emdash" => push("—", &mut pending, &mut out),
                    "endash" => push("–", &mut pending, &mut out),
                    "lquote" => push("‘", &mut pending, &mut out),
                    "rquote" => push("’", &mut pending, &mut out),
                    "ldblquote" => push("“", &mut pending, &mut out),
                    "rdblquote" => push("”", &mut pending, &mut out),
                    "bullet" => push("•", &mut pending, &mut out),
                    _ => {}
                }
            }
            continue;
        }
        if let Some(s) = sym {
            let s = s.as_str();
            if s == "*" {
                star = true;
            } else if hidden {
                continue;
            } else if matches!(s, "\\" | "{" | "}") {
                flush(&mut pending, &mut out);
                out.push_str(s);
            } else if s == "~" {
                flush(&mut pending, &mut out);
                out.push('\u{a0}');
            } else if s == "\r" || s == "\n" {
                flush(&mut pending, &mut out);
                out.push('\n');
            }
            continue;
        }
        if hidden {
            continue;
        }
        if let Some(h) = hexbyte {
            pending.push(u8::from_str_radix(h.as_str(), 16).unwrap_or(b'?'));
        } else if let Some(t) = text {
            flush(&mut pending, &mut out);
            out.push_str(t);
        }
    }
    flush(&mut pending, &mut out);
    if from_html {
        return ("html", out);
    }
    ("text", BLANKS.replace_all(&out, "\n\n").trim().to_string())
}

const PREBUF: &[u8] = b"{\\rtf1\\ansi\\mac\\deff0\\deftab720{\\fonttbl;}{\\f0\\fnil \\froman \\fswiss \\fmodern \\fscript \\fdecor MS Sans SerifSymbolArialTimes New RomanCourier{\\colortbl\\red0\\green0\\blue0\r\n\\par \\pard\\plain\\f0\\fs20\\b\\i\\u\\tab\\tx";

/// Outlook's compressed RTF ([MS-OXRTFCP], "LZFu").
pub fn decompress(data: &[u8]) -> Option<Vec<u8>> {
    if data.len() < 16 {
        return None;
    }
    let u32le = |o: usize| u32::from_le_bytes([data[o], data[o + 1], data[o + 2], data[o + 3]]);
    let (comp_size, raw_size, kind) = (u32le(0) as usize, u32le(4) as usize, u32le(8));
    let end = (comp_size + 4).min(data.len());
    match kind {
        0x414C_454D => return Some(data[16..(16 + raw_size).min(data.len())].to_vec()), // "MELA": stored as is
        0x7546_5A4C => {}                                                                 // "LZFu"
        _ => return None,
    }
    let mut dict = [0u8; 4096];
    dict[..PREBUF.len()].copy_from_slice(PREBUF);
    let mut wpos = PREBUF.len();
    let mut out = Vec::with_capacity(raw_size.min(64 << 20));
    let mut i = 16;
    while i < end {
        let control = data[i];
        i += 1;
        for bit in 0..8 {
            if i >= end {
                break;
            }
            if control & (1 << bit) == 0 {
                let b = data[i];
                i += 1;
                out.push(b);
                dict[wpos] = b;
                wpos = (wpos + 1) % 4096;
            } else {
                if i + 1 >= end {
                    return Some(out);
                }
                let word = u16::from_be_bytes([data[i], data[i + 1]]) as usize;
                i += 2;
                let (offset, len) = (word >> 4, (word & 0xF) + 2);
                if offset == wpos {
                    return Some(out);
                }
                for k in 0..len {
                    let b = dict[(offset + k) % 4096];
                    out.push(b);
                    dict[wpos] = b;
                    wpos = (wpos + 1) % 4096;
                }
            }
        }
    }
    Some(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn plain_and_html_bodies() {
        let r = format!("{}{}{}", r"{\rtf1\ansi\ansicpg1252 {\fonttbl{\f0 Arial;}}Hello\par Caf\'e9 ", "\\u8364?", " done}");
        assert_eq!(decode(&r), ("text", "Hello\nCafé € done".to_string()));
        let (kind, content) = decode(r"{\rtf1\ansi\fromhtml1 {\*\htmltag19 <p>}\htmlrtf {\htmlrtf0 Hi there{\*\htmltag84 </p>}}");
        assert_eq!(kind, "html");
        assert!(content.contains("<p>") && content.contains("Hi there") && content.contains("</p>"), "{content}");
    }

    #[test]
    fn lzfu() {
        // The example from [MS-OXRTFCP] section 4.1.
        let data: Vec<u8> = vec![
            0x2d, 0x00, 0x00, 0x00, 0x2b, 0x00, 0x00, 0x00, 0x4c, 0x5a, 0x46, 0x75, 0xf1, 0xc5, 0xc7, 0xa7, 0x03, 0x00, 0x0a, 0x00, 0x72,
            0x63, 0x70, 0x67, 0x31, 0x32, 0x35, 0x42, 0x32, 0x0a, 0xf3, 0x20, 0x68, 0x65, 0x6c, 0x09, 0x00, 0x20, 0x62, 0x77, 0x05, 0xb0,
            0x6c, 0x64, 0x7d, 0x0a, 0x80, 0x0f, 0xa0,
        ];
        let out = decompress(&data).unwrap();
        assert_eq!(String::from_utf8(out).unwrap(), "{\\rtf1\\ansi\\ansicpg1252\\pard hello world}\r\n");
    }
}
