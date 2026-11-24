//! Case storage on disk, the same layout as the Python version:
//!
//!     cases/<case-id>/original/<file-id><ext>   uploaded files, byte for byte
//!     cases/<case-id>/markdown/<file-id>.md     the converted Markdown
//!     cases/<case-id>/preview/<file-id>/        page images for the viewer
//!
//! Files are stored under their ID, not their uploaded name, so long paths,
//! reserved Windows names and odd characters can't cause trouble. Originals
//! are made read-only once stored and never changed.

use std::io;
use std::path::{Path, PathBuf};

use crate::util;

#[derive(Clone)]
pub struct Storage {
    pub root: PathBuf,
}

pub fn valid_id(value: &str) -> bool {
    value.len() == 32 && value.bytes().all(|b| matches!(b, b'0'..=b'9' | b'a'..=b'f'))
}

fn valid_ext(ext: &str) -> bool {
    ext.is_empty()
        || (ext.starts_with('.') && (2..=11).contains(&ext.len()) && ext[1..].bytes().all(|b| b.is_ascii_lowercase() || b.is_ascii_digit()))
}

fn check(id: &str) -> io::Result<&str> {
    if valid_id(id) {
        Ok(id)
    } else {
        Err(io::Error::new(io::ErrorKind::InvalidInput, "bad id"))
    }
}

impl Storage {
    pub fn new(root: PathBuf) -> Storage {
        Storage { root }
    }

    pub fn case_dir(&self, case_id: &str) -> io::Result<PathBuf> {
        Ok(self.root.join(check(case_id)?))
    }

    pub fn originals(&self, case_id: &str) -> io::Result<PathBuf> {
        Ok(self.case_dir(case_id)?.join("original"))
    }

    pub fn original(&self, case_id: &str, file_id: &str, ext: &str) -> io::Result<PathBuf> {
        if !valid_ext(ext) {
            return Err(io::Error::new(io::ErrorKind::InvalidInput, "bad extension"));
        }
        Ok(self.originals(case_id)?.join(format!("{}{ext}", check(file_id)?)))
    }

    pub fn markdown(&self, case_id: &str, file_id: &str) -> io::Result<PathBuf> {
        Ok(self.case_dir(case_id)?.join("markdown").join(format!("{}.md", check(file_id)?)))
    }

    pub fn previews(&self, case_id: &str, file_id: &str) -> io::Result<PathBuf> {
        Ok(self.case_dir(case_id)?.join("preview").join(check(file_id)?))
    }

    pub fn preview(&self, case_id: &str, file_id: &str, page: i64) -> io::Result<PathBuf> {
        Ok(self.previews(case_id, file_id)?.join(format!("p{page:04}.jpg")))
    }

    pub fn create_case(&self, case_id: &str) -> io::Result<()> {
        std::fs::create_dir_all(self.originals(case_id)?)
    }

    /// A temporary path for an upload in progress, on the same disk as its
    /// final place so storing it is a rename.
    pub fn incoming(&self, case_id: &str) -> io::Result<PathBuf> {
        let folder = self.originals(case_id)?;
        std::fs::create_dir_all(&folder)?;
        Ok(folder.join(format!(".incoming-{}", util::token_hex(8))))
    }

    pub fn keep(&self, tmp: &Path, case_id: &str, file_id: &str, ext: &str) -> io::Result<PathBuf> {
        let dest = self.original(case_id, file_id, ext)?;
        std::fs::rename(tmp, &dest)?;
        let mut perms = std::fs::metadata(&dest)?.permissions();
        perms.set_readonly(true);
        std::fs::set_permissions(&dest, perms)?;
        Ok(dest)
    }

    /// A scratch folder for one conversion, on the case's disk.
    pub fn work_dir(&self, case_id: &str) -> io::Result<PathBuf> {
        let folder = self.case_dir(case_id)?.join(format!(".work-{}", util::token_hex(8)));
        std::fs::create_dir_all(&folder)?;
        Ok(folder)
    }

    pub fn write_markdown(&self, case_id: &str, file_id: &str, text: &str) -> io::Result<PathBuf> {
        let dest = self.markdown(case_id, file_id)?;
        std::fs::create_dir_all(dest.parent().unwrap())?;
        let tmp = dest.with_extension(format!("tmp-{}", util::token_hex(4)));
        std::fs::write(&tmp, text)?;
        std::fs::rename(&tmp, &dest)?;
        Ok(dest)
    }

    /// Swap in a new set of page images (or remove them).
    pub fn replace_previews(&self, case_id: &str, file_id: &str, src: Option<&Path>) -> io::Result<()> {
        let dest = self.previews(case_id, file_id)?;
        if dest.exists() {
            let old = dest.with_file_name(format!(".old-{file_id}-{}", util::token_hex(4)));
            std::fs::rename(&dest, &old)?;
            let _ = std::fs::remove_dir_all(old);
        }
        if let Some(src) = src.filter(|s| s.is_dir()) {
            std::fs::create_dir_all(dest.parent().unwrap())?;
            std::fs::rename(src, &dest)?;
        }
        Ok(())
    }

    /// Store a new original from bytes (an email attachment).
    pub fn keep_bytes(&self, case_id: &str, file_id: &str, ext: &str, data: &[u8]) -> io::Result<PathBuf> {
        let tmp = self.incoming(case_id)?;
        std::fs::write(&tmp, data)?;
        self.keep(&tmp, case_id, file_id, ext)
    }

    /// Move a case's folder aside in one step, so a delete either takes the
    /// whole folder or nothing (Windows refuses if a file is open).
    pub fn trash_case(&self, case_id: &str) -> io::Result<()> {
        let folder = self.case_dir(case_id)?;
        if folder.exists() {
            std::fs::rename(&folder, self.root.join(format!(".deleted-{case_id}")))?;
        }
        Ok(())
    }

    /// Remove folders of deleted cases. Anything still locked is retried
    /// next time.
    pub fn empty_trash(&self) {
        let Ok(entries) = std::fs::read_dir(&self.root) else { return };
        for e in entries.flatten() {
            if e.file_name().to_string_lossy().starts_with(".deleted-") {
                make_writable(&e.path());
                let _ = std::fs::remove_dir_all(e.path());
            }
        }
    }
}

/// Originals are read-only; clear that so they can be removed.
#[allow(clippy::permissions_set_readonly_false)]
fn make_writable(dir: &Path) {
    let Ok(entries) = std::fs::read_dir(dir) else { return };
    for e in entries.flatten() {
        let p = e.path();
        if p.is_dir() {
            make_writable(&p);
        } else if let Ok(meta) = std::fs::metadata(&p) {
            let mut perms = meta.permissions();
            if perms.readonly() {
                perms.set_readonly(false);
                let _ = std::fs::set_permissions(&p, perms);
            }
        }
    }
}
