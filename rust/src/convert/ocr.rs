//! Reading text from page images with Tesseract (installed by the
//! launcher). Tesseract is run as a program with a fixed command line; only
//! the image path changes.

use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

use image::DynamicImage;

use super::markdown::{escape, join_lines};
use super::{ConversionError, Options};

pub const OCR_DPI: f32 = 300.0;
const PAGE_TIMEOUT: Duration = Duration::from_secs(180);

/// The Tesseract program: TESSERACT_CMD (set by the launcher), else on the
/// PATH, else where its Windows installer puts it.
pub fn tesseract_cmd() -> Option<std::path::PathBuf> {
    if let Some(c) = std::env::var_os("TESSERACT_CMD").filter(|c| !c.is_empty()) {
        return which::which(&c).ok();
    }
    if let Ok(p) = which::which("tesseract") {
        return Some(p);
    }
    #[cfg(windows)]
    for var in ["ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"] {
        if let Some(base) = std::env::var_os(var) {
            let mut p = std::path::PathBuf::from(base);
            p.extend(if var == "LOCALAPPDATA" { vec!["Programs", "Tesseract-OCR", "tesseract.exe"] } else { vec!["Tesseract-OCR", "tesseract.exe"] });
            if p.is_file() {
                return Some(p);
            }
        }
    }
    None
}

pub fn no_window(cmd: &mut Command) -> &mut Command {
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        cmd.creation_flags(0x0800_0000); // CREATE_NO_WINDOW
    }
    cmd
}

/// OCR one page image; returns Markdown paragraphs.
pub fn read_image(img: &DynamicImage, opts: &Options) -> Result<String, ConversionError> {
    let dir = std::env::temp_dir().join(format!("ocr-{}", crate::util::token_hex(8)));
    std::fs::create_dir_all(&dir).map_err(|_| ConversionError::new("There's no room to read scanned pages on this computer."))?;
    let path = dir.join("page.png");
    let result = (|| {
        img.to_luma8().save(&path).map_err(|_| ConversionError::new("A scanned page couldn't be prepared for OCR."))?;
        let mut cmd = Command::new(&opts.tesseract);
        cmd.arg(&path).args(["stdout", "-l", &opts.ocr_lang, "--psm", "3"]).env("OMP_THREAD_LIMIT", "1");
        cmd.stdout(Stdio::piped()).stderr(Stdio::piped());
        let mut child = no_window(&mut cmd).spawn().map_err(|_| {
            ConversionError::new("OCR isn't installed, so scanned pages can't be read. The Admin can install it from Components in the Control Center.")
        })?;
        // Read output on threads so a full pipe can't stall Tesseract.
        let mut out = child.stdout.take().unwrap();
        let mut err = child.stderr.take().unwrap();
        let t_out = std::thread::spawn(move || {
            let mut b = Vec::new();
            let _ = std::io::Read::read_to_end(&mut out, &mut b);
            b
        });
        let t_err = std::thread::spawn(move || {
            let mut b = Vec::new();
            let _ = std::io::Read::read_to_end(&mut err, &mut b);
            b
        });
        let start = Instant::now();
        let status = loop {
            if let Some(s) = child.try_wait().ok().flatten() {
                break s;
            }
            if start.elapsed() > PAGE_TIMEOUT {
                let _ = child.kill();
                let _ = child.wait();
                return Err(ConversionError::new("Reading a scanned page took too long. Try again; if it keeps happening, the scan may be damaged."));
            }
            std::thread::sleep(Duration::from_millis(50));
        };
        let stdout = t_out.join().unwrap_or_default();
        let stderr = t_err.join().unwrap_or_default();
        if !status.success() {
            let msg = String::from_utf8_lossy(&stderr);
            let last = msg.trim().lines().last().map(|l| format!(" ({})", l.chars().take(200).collect::<String>())).unwrap_or_default();
            return Err(ConversionError::new(format!("OCR couldn't read a scanned page{last}.")));
        }
        Ok(paragraphs(&String::from_utf8_lossy(&stdout)))
    })();
    let _ = std::fs::remove_dir_all(&dir);
    result
}

/// Tesseract separates paragraphs with blank lines.
pub fn paragraphs(text: &str) -> String {
    let mut blocks = Vec::new();
    let mut current: Vec<&str> = Vec::new();
    let text = text.replace('\x0c', "\n");
    for line in text.lines() {
        if !line.trim().is_empty() {
            current.push(line);
        } else if !current.is_empty() {
            blocks.push(escape(&join_lines(&current)));
            current.clear();
        }
    }
    if !current.is_empty() {
        blocks.push(escape(&join_lines(&current)));
    }
    blocks.join("\n\n")
}

#[cfg(test)]
mod tests {
    #[test]
    fn tesseract_paragraphs() {
        assert_eq!(super::paragraphs("WITNESS\nSTATE-\nment\n\n\nsecond\x0c"), "WITNESS STATEment\n\nsecond");
    }
}
