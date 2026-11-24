//! PDF to Markdown, with PDFium (the PDF engine in Chrome; the Python
//! version used it too, for page images).
//!
//! PDFium gives each character with its position and font. Characters are
//! grouped into lines and lines into blocks, so two-column pages read column
//! by column. Ruled tables (cells drawn with lines) become Markdown tables.
//! Running headers, footers and page numbers are dropped. Each page starts
//! with a page marker so passages can be cited by page.
//!
//! Pages that are pictures of text (scans) are detected. They are read with
//! OCR only when asked (opts.ocr); the worker asks in the background.

use std::collections::{BTreeSet, HashMap};
use std::path::{Path, PathBuf};
use std::sync::LazyLock;

use pdfium_render::prelude::*;
use regex::Regex;

use super::markdown::{escape, join_lines, page_list, page_marker, table, tidy};
use super::{ocr, previews, ConvResult, Converted, ConversionError, Options};

const MAX_PAGES: usize = 5000;

static PAGE_NUMBER: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?i)^(page\s*)?[-–—(]?\s*([0-9]{1,4}|[ivxlcdm]{1,6})\s*[-–—)]?(\s*(of|/)\s*[0-9]{1,4})?$").unwrap()
});
static BULLET: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"^[•●▪■◦‣∙·–]\s*").unwrap());
static DIGITS: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"\d+").unwrap());

/// PDFium is a library (pdfium.dll) shipped next to casefiles.exe.
pub fn pdfium() -> Result<Pdfium, PdfiumError> {
    let mut places: Vec<PathBuf> = Vec::new();
    if let Ok(p) = std::env::var("PDFIUM_PATH") {
        places.push(PathBuf::from(p));
    }
    if let Some(dir) = std::env::current_exe().ok().and_then(|e| e.parent().map(Path::to_path_buf)) {
        places.push(Pdfium::pdfium_platform_library_name_at_path(&dir));
    }
    for p in places {
        if p.is_file() {
            return Pdfium::bind_to_library(&p).map(Pdfium::new);
        }
    }
    Pdfium::bind_to_system_library().map(Pdfium::new)
}

fn norm(text: &str) -> String {
    DIGITS.replace_all(&text.split_whitespace().collect::<Vec<_>>().join(" ").to_lowercase(), "#").into_owned()
}

#[derive(Clone, Debug)]
struct Line {
    text: String,
    block: usize,
    top: f32,
    bottom: f32,
    x0: f32,
    x1: f32,
    size: f32,
    sizes: Vec<f32>,
    bold: bool,
}

struct Glyph {
    ch: char,
    left: f32,
    right: f32,
    top: f32,
    bottom: f32,
    size: f32,
    bold: bool,
    generated: bool,
}

fn median(v: &[f32]) -> f32 {
    if v.is_empty() {
        return 0.0;
    }
    let mut s = v.to_vec();
    s.sort_by(|a, b| a.partial_cmp(b).unwrap());
    let n = s.len();
    if n % 2 == 1 { s[n / 2] } else { (s[n / 2 - 1] + s[n / 2]) / 2.0 }
}

/// Glyphs into lines (PDFium marks line ends with generated characters, and
/// a jump in the baseline starts a new line too), then lines into blocks:
/// a block ends where the next line doesn't sit under it in the same column.
fn lines_of(glyphs: &[Glyph]) -> Vec<Line> {
    let mut raw: Vec<Vec<&Glyph>> = vec![Vec::new()];
    for g in glyphs {
        let cur = raw.last_mut().unwrap();
        let breaks = g.ch == '\n' || g.ch == '\r';
        if breaks {
            if !cur.is_empty() {
                raw.push(Vec::new());
            }
            continue;
        }
        if let Some(prev) = cur.iter().rev().find(|p| !p.ch.is_whitespace()) {
            let h = (prev.top - prev.bottom).abs().max(1.0);
            let moved = (g.bottom - prev.bottom).abs() > h * 0.6 && !g.ch.is_whitespace();
            let went_back = g.left + h < prev.left && !g.ch.is_whitespace();
            if moved || went_back {
                raw.push(Vec::new());
            }
        }
        raw.last_mut().unwrap().push(g);
    }
    let mut out: Vec<Line> = Vec::new();
    for gs in raw {
        let visible: Vec<&&Glyph> = gs.iter().filter(|g| !g.ch.is_whitespace() && !g.generated).collect();
        if visible.is_empty() {
            continue;
        }
        // Spaces PDFium works out from gaps are generated; keep them.
        let mut text = String::new();
        let mut last_right: Option<f32> = None;
        for g in &gs {
            if let (Some(r), false) = (last_right, g.ch.is_whitespace()) {
                // A wide gap with no space character still separates words.
                let h = (g.top - g.bottom).abs().max(1.0);
                if g.left - r > h * 0.25 && !text.ends_with(' ') {
                    text.push(' ');
                }
            }
            text.push(if g.ch.is_whitespace() { ' ' } else { g.ch });
            if !g.ch.is_whitespace() {
                last_right = Some(g.right);
            }
        }
        let text = text.split_whitespace().collect::<Vec<_>>().join(" ");
        let sizes: Vec<f32> = visible.iter().map(|g| (g.size * 2.0).round() / 2.0).collect();
        out.push(Line {
            text,
            block: 0,
            top: visible.iter().map(|g| g.top).fold(f32::MAX, f32::min),
            bottom: visible.iter().map(|g| g.bottom).fold(f32::MIN, f32::max),
            x0: visible.iter().map(|g| g.left).fold(f32::MAX, f32::min),
            x1: visible.iter().map(|g| g.right).fold(f32::MIN, f32::max),
            size: median(&sizes),
            bold: visible.iter().all(|g| g.bold),
            sizes,
        });
    }
    let mut block = 0;
    for i in 1..out.len() {
        let (prev, line) = (&out[i - 1], &out[i]);
        let h = (prev.bottom - prev.top).max(1.0);
        let gap = line.top - prev.bottom;
        let overlaps = line.x0 < prev.x1 && line.x1 > prev.x0;
        if !(overlaps && gap > -h && gap <= h * 1.5) {
            block += 1;
        }
        out[i].block = block;
    }
    out
}

/// Group lines into paragraphs. A new paragraph starts at a new block
/// unless the previous line ran to the column's edge, and at any change of
/// font size, a wide gap or a short last line.
fn paragraphs(lines: &[Line]) -> Vec<Vec<Line>> {
    if lines.is_empty() {
        return Vec::new();
    }
    let right = lines.iter().map(|l| l.x1).fold(f32::MIN, f32::max);
    let left = lines.iter().map(|l| l.x0).fold(f32::MAX, f32::min);
    let width = (right - left).max(1.0);
    let mut paras: Vec<Vec<Line>> = vec![vec![lines[0].clone()]];
    for w in lines.windows(2) {
        let (prev, line) = (&w[0], &w[1]);
        let height = (prev.bottom - prev.top).max(1.0);
        let gap = line.top - prev.bottom;
        let full = prev.x1 >= right - width * 0.12;
        let same_font = (line.size - prev.size).abs() < 0.6 && line.bold == prev.bold;
        let joined = same_font
            && -height < gap
            && gap <= height * 1.2
            && (line.x0 - prev.x0).abs() < 20.0
            && (line.block == prev.block || (full && !prev.text.ends_with(':')));
        if joined {
            paras.last_mut().unwrap().push(line.clone());
        } else {
            paras.push(vec![line.clone()]);
        }
    }
    paras
}

struct Table {
    top: f32,
    bbox: (f32, f32, f32, f32), // x0, top, x1, bottom
    rows: Vec<Vec<String>>,
}

fn inside(l: &Line, b: (f32, f32, f32, f32)) -> bool {
    let (cx, cy) = ((l.x0 + l.x1) / 2.0, (l.top + l.bottom) / 2.0);
    b.0 - 2.0 <= cx && cx <= b.2 + 2.0 && b.1 - 2.0 <= cy && cy <= b.3 + 2.0
}

/// Ruled tables: grids of drawn lines (or cell rectangles). Each grid with
/// at least two rows and two columns becomes a table; the text of each cell
/// is the glyphs inside it.
fn tables(edges: &[(f32, f32, f32, f32)], glyphs: &[Glyph]) -> Vec<Table> {
    // Split drawn shapes into horizontal and vertical segments (top-down coordinates).
    let (mut hs, mut vs): (Vec<(f32, f32, f32)>, Vec<(f32, f32, f32)>) = (Vec::new(), Vec::new());
    for &(x0, top, x1, bottom) in edges {
        let (w, h) = (x1 - x0, bottom - top);
        if w < 0.5 && h < 0.5 {
            continue;
        }
        if h <= 2.0 {
            hs.push(((top + bottom) / 2.0, x0, x1));
        } else if w <= 2.0 {
            vs.push(((x0 + x1) / 2.0, top, bottom));
        } else {
            hs.push((top, x0, x1));
            hs.push((bottom, x0, x1));
            vs.push((x0, top, bottom));
            vs.push((x1, top, bottom));
        }
    }
    if hs.len() < 2 || vs.len() < 2 {
        return Vec::new();
    }
    // Group segments that touch into grids.
    let n = hs.len() + vs.len();
    let mut parent: Vec<usize> = (0..n).collect();
    fn find(p: &mut [usize], i: usize) -> usize {
        let mut i = i;
        while p[i] != i {
            p[i] = p[p[i]];
            i = p[i];
        }
        i
    }
    const TOL: f32 = 3.0;
    for (i, h) in hs.iter().enumerate() {
        for (j, v) in vs.iter().enumerate() {
            if v.0 >= h.1 - TOL && v.0 <= h.2 + TOL && h.0 >= v.1 - TOL && h.0 <= v.2 + TOL {
                let (a, b) = (find(&mut parent, i), find(&mut parent, hs.len() + j));
                parent[a] = b;
            }
        }
    }
    let mut groups: HashMap<usize, (Vec<usize>, Vec<usize>)> = HashMap::new();
    for i in 0..hs.len() {
        let r = find(&mut parent, i);
        groups.entry(r).or_default().0.push(i);
    }
    for j in 0..vs.len() {
        let r = find(&mut parent, hs.len() + j);
        groups.entry(r).or_default().1.push(j);
    }
    let snap = |vals: Vec<f32>| -> Vec<f32> {
        let mut vals = vals;
        vals.sort_by(|a, b| a.partial_cmp(b).unwrap());
        let mut out: Vec<f32> = Vec::new();
        for v in vals {
            if out.last().is_none_or(|l| v - l > TOL) {
                out.push(v);
            }
        }
        out
    };
    let mut found = Vec::new();
    for (h_ids, v_ids) in groups.values() {
        let ys = snap(h_ids.iter().map(|&i| hs[i].0).collect());
        let xs = snap(v_ids.iter().map(|&j| vs[j].0).collect());
        if ys.len() < 3 || xs.len() < 3 {
            continue; // at least two rows and two columns
        }
        let mut rows = vec![vec![String::new(); xs.len() - 1]; ys.len() - 1];
        let mut cells: Vec<Vec<Vec<&Glyph>>> = vec![vec![Vec::new(); xs.len() - 1]; ys.len() - 1];
        for g in glyphs.iter().filter(|g| !g.generated || g.ch == ' ') {
            let (cx, cy) = ((g.left + g.right) / 2.0, (g.top + g.bottom) / 2.0);
            let r = ys.windows(2).position(|w| cy > w[0] && cy < w[1]);
            let c = xs.windows(2).position(|w| cx > w[0] && cx < w[1]);
            if let (Some(r), Some(c)) = (r, c) {
                cells[r][c].push(g);
            }
        }
        for (r, row) in cells.iter().enumerate() {
            for (c, gs) in row.iter().enumerate() {
                let owned: Vec<Glyph> = gs
                    .iter()
                    .map(|g| Glyph { ch: g.ch, left: g.left, right: g.right, top: g.top, bottom: g.bottom, size: g.size, bold: g.bold, generated: g.generated })
                    .collect();
                rows[r][c] = lines_of(&owned).into_iter().map(|l| l.text).collect::<Vec<_>>().join("\n");
            }
        }
        found.push(Table { top: ys[0], bbox: (xs[0], ys[0], *xs.last().unwrap(), *ys.last().unwrap()), rows });
    }
    found.sort_by(|a, b| a.top.partial_cmp(&b.top).unwrap());
    found
}

struct PageData {
    lines: Vec<Line>,
    tables: Vec<Table>,
    ocr: Option<String>,
}

/// What PDFium gives for a glyph its font has no character for. (Its own
/// "map error" flag is set for many readable standard fonts, so isn't used.)
fn unmapped_char(ch: char) -> bool {
    ch == '\u{fffd}' || ch == '\u{fffe}' || ch == '\u{ffff}' || (ch.is_control() && !ch.is_whitespace()) || ('\u{e000}'..='\u{f8ff}').contains(&ch)
}

fn is_bold(font_name: &str, weight: Option<PdfFontWeight>) -> bool {
    font_name.to_lowercase().contains("bold")
        || matches!(weight, Some(PdfFontWeight::Weight700Bold | PdfFontWeight::Weight800 | PdfFontWeight::Weight900))
}

pub fn convert(path: &Path, opts: &Options) -> Converted {
    let pdfium = pdfium().map_err(|_| ConversionError::new("The PDF reader (pdfium.dll) is missing from this copy of the app. Unzip the latest download again."))?;
    let doc = match pdfium.load_pdf_from_file(path, None) {
        Ok(d) => d,
        Err(PdfiumError::PdfiumLibraryInternalError(PdfiumInternalError::PasswordError)) => {
            return Err(ConversionError::new("This PDF is password-protected. Open it, save a copy without the password, and upload that."));
        }
        Err(_) => {
            return Err(ConversionError::new("This PDF can't be opened. It may be damaged or not really a PDF. Open it on your computer to check it."));
        }
    };
    let mut warnings: Vec<String> = Vec::new();
    let mut scanned: Vec<usize> = Vec::new();
    let mut pages: Vec<PageData> = Vec::new();
    let total = doc.pages().len() as usize;
    if total > MAX_PAGES {
        warnings.push(format!("Only the first {MAX_PAGES} of {total} pages were converted."));
    }
    for i in 0..total.min(MAX_PAGES) {
        let n = i + 1;
        let Ok(page) = doc.pages().get(i as PdfPageIndex) else {
            warnings.push(format!("Page {n} couldn't be read."));
            pages.push(PageData { lines: Vec::new(), tables: Vec::new(), ocr: None });
            continue;
        };
        let height = page.height().value;
        let mut glyphs = Vec::new();
        let mut unmapped = 0usize;
        let mut real = 0usize;
        let mut has_image = false;
        let mut edges = Vec::new();
        for obj in page.objects().iter() {
            match obj.object_type() {
                PdfPageObjectType::Image => has_image = true,
                PdfPageObjectType::Path => {
                    if let Ok(b) = obj.bounds() {
                        let r = b.to_rect();
                        edges.push((r.left().value, height - r.top().value, r.right().value, height - r.bottom().value));
                    }
                }
                _ => {}
            }
        }
        if let Ok(text) = page.text() {
            for c in text.chars().iter() {
                let Some(ch) = c.unicode_char() else { continue };
                let generated = c.is_generated().unwrap_or(false);
                if !generated && !ch.is_whitespace() {
                    real += 1;
                    if unmapped_char(ch) {
                        unmapped += 1;
                    }
                }
                let Ok(b) = c.loose_bounds() else { continue };
                glyphs.push(Glyph {
                    ch,
                    left: b.left().value,
                    right: b.right().value,
                    top: height - b.top().value,
                    bottom: height - b.bottom().value,
                    size: c.scaled_font_size().value,
                    bold: !ch.is_whitespace() && is_bold(&c.font_name(), c.font_weight()),
                    generated,
                });
            }
        }
        let tables = tables(&edges, &glyphs);
        let lines: Vec<Line> = lines_of(&glyphs).into_iter().filter(|l| !tables.iter().any(|t| inside(l, t.bbox))).collect();
        let all_text: String = lines.iter().map(|l| l.text.as_str()).collect::<Vec<_>>().join(" ");
        let visible = all_text.chars().filter(|c| !c.is_whitespace()).count();
        let no_text = visible < 10 && tables.is_empty();
        // Text layers from fonts without a character map can't be read.
        let unreadable = !no_text && unmapped * 10 > real.max(1) * 3;
        let mut entry = PageData { lines, tables, ocr: None };
        if (no_text && has_image) || unreadable {
            if opts.ocr {
                let width_px = (page.width().value * ocr::OCR_DPI / 72.0) as i32;
                let img = page
                    .render_with_config(&PdfRenderConfig::new().set_target_width(width_px))
                    .and_then(|b| b.as_image())
                    .map_err(|_| ConversionError::new(format!("Page {n} couldn't be drawn for OCR.")))?;
                entry.ocr = Some(ocr::read_image(&img, opts)?);
                entry.lines.clear();
                entry.tables.clear();
            } else {
                scanned.push(n);
            }
        }
        if opts.previews.is_some() {
            let w = page.width().value.max(1.0);
            let target = (w * (previews::PREVIEW_WIDTH as f32 / w).min(3.0)) as i32;
            if let Ok(img) = page.render_with_config(&PdfRenderConfig::new().set_target_width(target)).and_then(|b| b.as_image()) {
                previews::save(&img, opts, n);
            }
        }
        pages.push(entry);
    }

    // Running headers and footers: the same first or last lines on most pages.
    let mut edge: HashMap<String, usize> = HashMap::new();
    for p in &pages {
        if p.lines.is_empty() {
            continue;
        }
        let mut by_top: Vec<&Line> = p.lines.iter().collect();
        by_top.sort_by(|a, b| a.top.partial_cmp(&b.top).unwrap());
        let k = by_top.len();
        let picked: BTreeSet<String> = by_top[..k.min(2)].iter().chain(by_top[k.saturating_sub(2)..].iter()).map(|l| norm(&l.text)).collect();
        for s in picked {
            *edge.entry(s).or_default() += 1;
        }
    }
    let threshold = 3f32.max(pages.len() as f32 * 0.5);
    let repeated: BTreeSet<String> = edge.into_iter().filter(|(k, n)| *n as f32 >= threshold && k.chars().count() <= 200).map(|(k, _)| k).collect();

    let mut size_count: HashMap<i32, usize> = HashMap::new();
    for p in &pages {
        for l in &p.lines {
            for s in &l.sizes {
                *size_count.entry((s * 2.0) as i32).or_default() += 1;
            }
        }
    }
    let body_size = size_count.iter().max_by_key(|(s, n)| (**n, -**s)).map(|(s, _)| *s as f32 / 2.0).unwrap_or(0.0);
    let mut heading_sizes: Vec<f32> = Vec::new();
    for p in &pages {
        for l in &p.lines {
            if body_size > 0.0 && l.size >= body_size * 1.15 && l.text.chars().count() <= 200 && !heading_sizes.contains(&l.size) {
                heading_sizes.push(l.size);
            }
        }
    }
    heading_sizes.sort_by(|a, b| b.partial_cmp(a).unwrap());

    let mut parts: Vec<String> = Vec::new();
    for (i, p) in pages.iter().enumerate() {
        parts.push(page_marker(i + 1));
        if let Some(text) = &p.ocr {
            parts.push(if text.is_empty() { "*(No text found on this page.)*".into() } else { text.clone() });
            continue;
        }
        let top_edge = p.lines.iter().map(|l| l.top).fold(f32::MAX, f32::min);
        let bottom_edge = p.lines.iter().map(|l| l.bottom).fold(f32::MIN, f32::max);
        let mut pending: Vec<&Table> = p.tables.iter().collect();
        let lines: Vec<Line> = p
            .lines
            .iter()
            .filter(|l| {
                let at_edge = l.top <= top_edge + 1.0 || l.bottom >= bottom_edge - 1.0;
                !(at_edge && (repeated.contains(&norm(&l.text)) || PAGE_NUMBER.is_match(&l.text)))
            })
            .cloned()
            .collect();
        for para in paragraphs(&lines) {
            while pending.first().is_some_and(|t| t.top < para[0].top) {
                parts.push(table(&pending.remove(0).rows));
            }
            let first = &para[0];
            let texts: Vec<&str> = para.iter().map(|l| l.text.as_str()).collect();
            let joined = texts.join(" ");
            let text = escape(&join_lines(&texts));
            if let Some(pos) = heading_sizes.iter().position(|s| *s == first.size).filter(|_| joined.chars().count() <= 200) {
                parts.push(format!("{} {text}", "#".repeat((pos + 1).min(3))));
            } else if first.bold && para.len() <= 2 && joined.chars().count() <= 120 && !joined.trim_end().ends_with(['.', ',', ';']) {
                parts.push(format!("### {text}"));
            } else if BULLET.is_match(&joined) {
                let mut items = vec![BULLET.replace(&para[0].text, "").into_owned()];
                items.extend(para[1..].iter().map(|l| l.text.clone()));
                parts.push(format!("- {}", escape(&join_lines(&items))));
            } else {
                parts.push(text);
            }
        }
        for t in pending {
            parts.push(table(&t.rows));
        }
    }

    if !scanned.is_empty() {
        if scanned.len() == pages.len() {
            warnings.push("This PDF is a scan with no text layer. It is read with OCR by itself.".into());
        } else {
            let many = scanned.len() > 1;
            warnings.push(format!(
                "Page{} {} {} with no text. {} read with OCR by itself.",
                if many { "s" } else { "" },
                page_list(&scanned),
                if many { "are scanned images" } else { "is a scanned image" },
                if many { "They are" } else { "It is" }
            ));
        }
    }
    let ocred: Vec<usize> = pages.iter().enumerate().filter(|(_, p)| p.ocr.is_some()).map(|(i, _)| i + 1).collect();
    if !ocred.is_empty() {
        let many = ocred.len() > 1;
        warnings.push(format!(
            "Page{} {} {} read with OCR. Check names and figures against the original.",
            if many { "s" } else { "" },
            page_list(&ocred),
            if many { "were" } else { "was" }
        ));
    }
    let meta = doc.metadata();
    let title = meta.get(PdfDocumentMetadataTagType::Title).map(|t| t.value().trim().to_string()).filter(|t| !t.is_empty());
    let doc_date = meta.get(PdfDocumentMetadataTagType::CreationDate).and_then(|t| pdf_date(t.value()));
    let count = pages.len();
    Ok(ConvResult {
        body: tidy(&parts.into_iter().filter(|x| !x.is_empty()).collect::<Vec<_>>().join("\n\n")),
        page_count: Some(count),
        ocr: !ocred.is_empty(),
        warnings,
        scanned_pages: scanned,
        title,
        doc_date,
        previews: if opts.previews.is_some() { count } else { 0 },
        ..Default::default()
    })
}

/// D:20240305142200+00'00' -> 2024-03-05T14:22:00+00:00
pub fn pdf_date(value: &str) -> Option<String> {
    let re = Regex::new(r"^D?:?(\d{4})(\d{2})?(\d{2})?(\d{2})?(\d{2})?(\d{2})?([Zz+-])?(\d{2})?'?(\d{2})?").unwrap();
    let c = re.captures(value.trim())?;
    let g = |i: usize, d: &str| c.get(i).map(|m| m.as_str().to_string()).unwrap_or_else(|| d.to_string());
    let mut out = format!("{}-{}-{}T{}:{}:{}", g(1, ""), g(2, "01"), g(3, "01"), g(4, "00"), g(5, "00"), g(6, "00"));
    chrono::NaiveDateTime::parse_from_str(&out, "%Y-%m-%dT%H:%M:%S").ok()?;
    match c.get(7).map(|m| m.as_str()) {
        Some(sign @ ("+" | "-")) if c.get(8).is_some() => out.push_str(&format!("{sign}{}:{}", g(8, ""), g(9, "00"))),
        Some("Z" | "z") => out.push_str("+00:00"),
        _ => {}
    }
    Some(out)
}

#[cfg(test)]
mod tests {
    #[test]
    fn dates() {
        assert_eq!(super::pdf_date("D:20240305142200+00'00'").as_deref(), Some("2024-03-05T14:22:00+00:00"));
        assert_eq!(super::pdf_date("D:2024").as_deref(), Some("2024-01-01T00:00:00"));
        assert_eq!(super::pdf_date("D:20241345").as_deref(), None);
    }
}
