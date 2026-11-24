//! Converting case files to Markdown for reading, searching and AI.
//!
//! The converters only read the original and write new files; originals are
//! never changed. Each file is converted in a child process
//! (`casefiles convert ...`, see `main`), so a parser crash or hang can't take
//! the worker down, and memory is returned after each file. The child writes
//! body.md, result.json, previews/pNNNN.jpg and attachments/N, the same
//! files as the Python version's `python -m app.convert`.

pub mod email;
pub mod eml;
pub mod html;
pub mod image;
pub mod markdown;
pub mod msg;
pub mod ocr;
pub mod pdf;
pub mod previews;
pub mod rtf;
pub mod text;
pub mod word;

use std::path::{Path, PathBuf};

use serde_json::{json, Map, Value};

/// Bump when the Markdown a converter produces changes.
pub const CONVERTER_VERSION: u32 = 1;

/// A file that can't be converted, with a plain-language reason and next
/// step. Shown to the user as is.
#[derive(Debug)]
pub struct ConversionError(pub String);

impl ConversionError {
    pub fn new(msg: impl Into<String>) -> ConversionError {
        ConversionError(msg.into())
    }
}

pub type Converted = Result<ConvResult, ConversionError>;

pub struct Attachment {
    pub name: String,
    pub data: Vec<u8>,
}

#[derive(Clone)]
pub struct Options {
    /// Read pages with no text layer with OCR.
    pub ocr: bool,
    pub ocr_lang: String,
    pub tesseract: String,
    /// Where to write page images, if wanted.
    pub previews: Option<PathBuf>,
}

impl Default for Options {
    fn default() -> Options {
        Options { ocr: false, ocr_lang: "eng".into(), tesseract: "tesseract".into(), previews: None }
    }
}

#[derive(Default)]
pub struct ConvResult {
    pub body: String,
    pub page_count: Option<usize>,
    pub ocr: bool,
    pub warnings: Vec<String>,
    pub meta: Map<String, Value>,
    pub doc_date: Option<String>,
    pub title: Option<String>,
    /// Pages that are images with no text layer, not read with OCR yet.
    pub scanned_pages: Vec<usize>,
    pub attachments: Vec<Attachment>,
    pub previews: usize,
}

pub fn convert(path: &Path, ext: &str, opts: &Options) -> Converted {
    match ext {
        ".docx" => word::convert(path, opts),
        ".pdf" => pdf::convert(path, opts),
        ".msg" => msg::convert(path, opts),
        ".eml" => eml::convert(path, opts),
        ".txt" => text::convert(path, opts),
        ".png" | ".jpg" | ".jpeg" | ".tif" | ".tiff" => image::convert(path, opts),
        _ => Err(ConversionError::new(format!(
            "{} files can't be converted.",
            if ext.is_empty() { "Files without an extension" } else { ext }
        ))),
    }
}

/// `casefiles convert <original> <ext> <out-folder> [--ocr] [--lang eng] [--previews]`
///
/// A file that can't be converted gives result.json with "error" and exit
/// code 0; any other exit means the converter itself failed.
pub fn main(args: &[String]) -> i32 {
    if args.len() < 3 {
        eprintln!("usage: casefiles convert <original> <ext> <out-folder> [--ocr] [--lang eng] [--previews]");
        return 2;
    }
    let (src, ext, out) = (PathBuf::from(&args[0]), args[1].as_str(), PathBuf::from(&args[2]));
    let flags = &args[3..];
    let lang = flags.iter().position(|f| f == "--lang").and_then(|i| flags.get(i + 1)).cloned().unwrap_or_else(|| "eng".into());
    let opts = Options {
        ocr: flags.iter().any(|f| f == "--ocr"),
        ocr_lang: lang,
        tesseract: ocr::tesseract_cmd().map(|p| p.to_string_lossy().into_owned()).unwrap_or_else(|| "tesseract".into()),
        previews: flags.iter().any(|f| f == "--previews").then(|| out.join("previews")),
    };
    if let Err(e) = std::fs::create_dir_all(&out) {
        eprintln!("can't make {}: {e}", out.display());
        return 1;
    }
    let result = match std::panic::catch_unwind(|| convert(&src, ext, &opts)) {
        Ok(r) => r,
        Err(_) => return 1,
    };
    let write = |name: &str, data: &[u8]| std::fs::write(out.join(name), data);
    let r = match result {
        Err(ConversionError(msg)) => {
            return if write("result.json", json!({"error": msg}).to_string().as_bytes()).is_ok() { 0 } else { 1 };
        }
        Ok(r) => r,
    };
    let mut names = Vec::new();
    for (i, a) in r.attachments.iter().enumerate() {
        let dir = out.join("attachments");
        if std::fs::create_dir_all(&dir).and_then(|_| std::fs::write(dir.join(i.to_string()), &a.data)).is_err() {
            return 1;
        }
        names.push(a.name.clone());
    }
    let info = json!({
        "page_count": r.page_count, "ocr": r.ocr, "warnings": r.warnings, "meta": r.meta, "doc_date": r.doc_date,
        "title": r.title, "scanned_pages": r.scanned_pages, "attachments": names, "previews": r.previews,
    });
    if write("body.md", r.body.as_bytes()).is_err() || write("result.json", info.to_string().as_bytes()).is_err() {
        return 1;
    }
    0
}
