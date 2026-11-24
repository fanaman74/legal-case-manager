//! Plain text files.

use std::path::Path;

use super::markdown::{escape, tidy};
use super::{ConvResult, Converted, ConversionError, Options};

const MAX_BYTES: usize = 200 << 20;

/// (text, is_unicode). Byte-order marks first, then UTF-8, then Windows'
/// Western European code page, which can decode any byte.
pub fn decode(data: &[u8]) -> (String, bool) {
    if let Some(rest) = data.strip_prefix(b"\xef\xbb\xbf") {
        return (String::from_utf8_lossy(rest).into_owned(), true);
    }
    if data.starts_with(b"\xff\xfe") {
        return (encoding_rs::UTF_16LE.decode_with_bom_removal(data).0.into_owned(), true);
    }
    if data.starts_with(b"\xfe\xff") {
        return (encoding_rs::UTF_16BE.decode_with_bom_removal(data).0.into_owned(), true);
    }
    match std::str::from_utf8(data) {
        Ok(s) => (s.to_string(), true),
        Err(_) => (encoding_rs::WINDOWS_1252.decode_without_bom_handling(data).0.into_owned(), false),
    }
}

pub fn convert(path: &Path, _opts: &Options) -> Converted {
    let mut data = std::fs::read(path).map_err(|_| ConversionError::new("This file can't be read."))?;
    let too_big = data.len() > MAX_BYTES;
    data.truncate(MAX_BYTES);
    let (text, unicode) = decode(&data);
    let mut warnings = Vec::new();
    if !unicode {
        warnings.push("The text wasn't UTF-8, so it was read as Windows Western European text. Check accented characters.".to_string());
    }
    if too_big {
        warnings.push("Only the first 200 MB of this file were converted.".to_string());
    }
    // Line breaks are kept and nothing in the text is read as Markdown.
    let text = text.replace("\r\n", "\n").replace('\r', "\n");
    let paras = regex::Regex::new(r"\n\s*\n").unwrap();
    let body = paras
        .split(&text)
        .filter(|p| !p.trim().is_empty())
        .map(|p| p.trim_matches('\n').split('\n').map(|l| escape(l.trim_end())).collect::<Vec<_>>().join("  \n"))
        .collect::<Vec<_>>()
        .join("\n\n");
    Ok(ConvResult { body: tidy(&body), warnings, ..Default::default() })
}
