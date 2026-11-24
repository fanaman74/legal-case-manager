//! Word documents (.docx) to Markdown.
//!
//! Walks the document body in order, so headings, numbered clauses, lists,
//! tables, footnotes and text boxes keep their place. Headers and footers
//! are not part of the body. Clause numbers that Word generates ("4.2", "(b)")
//! are worked out and written into the text, because lawyers cite them.
//! Word saves where it last broke pages; those become page markers.

use std::collections::HashMap;
use std::io::Read;
use std::path::Path;

use roxmltree::{Document, Node};

use super::markdown::{escape, page_marker, table, tidy};
use super::{ConvResult, Converted, ConversionError, Options};

const W: &str = "http://schemas.openxmlformats.org/wordprocessingml/2006/main";
const R: &str = "http://schemas.openxmlformats.org/officeDocument/2006/relationships";
const REL_FOOTNOTES: &str = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/footnotes";
const REL_ENDNOTES: &str = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/endnotes";
const REL_COMMENTS: &str = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/comments";
const REL_NUMBERING: &str = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/numbering";
const REL_STYLES: &str = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles";
const REL_DOCUMENT: &str = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument";

fn is(n: &Node, tag: &str) -> bool {
    n.is_element() && n.tag_name().name() == tag && n.tag_name().namespace() == Some(W)
}

fn child<'a, 'i>(n: &Node<'a, 'i>, tag: &str) -> Option<Node<'a, 'i>> {
    n.children().find(|c| is(c, tag))
}

fn children<'a, 'i>(n: &Node<'a, 'i>, tag: &'static str) -> impl Iterator<Item = Node<'a, 'i>> {
    n.children().filter(move |c| is(c, tag))
}

fn wattr<'a>(n: &Node<'a, '_>, name: &str) -> Option<&'a str> {
    n.attribute((W, name))
}

/// A Word on/off property (<w:b/>, <w:b w:val="0"/>).
fn on(el: Option<Node>) -> bool {
    match el {
        None => false,
        Some(e) => !matches!(wattr(&e, "val").unwrap_or("true").to_lowercase().as_str(), "0" | "false" | "off" | "none"),
    }
}

// --- The package --------------------------------------------------------------------

struct Package {
    files: HashMap<String, Vec<u8>>,
}

impl Package {
    fn open(path: &Path) -> Option<Package> {
        let f = std::fs::File::open(path).ok()?;
        let mut z = zip::ZipArchive::new(f).ok()?;
        let mut files = HashMap::new();
        for i in 0..z.len() {
            let mut entry = z.by_index(i).ok()?;
            if entry.is_dir() || entry.size() > 512 << 20 {
                continue;
            }
            let mut buf = Vec::new();
            entry.read_to_end(&mut buf).ok()?;
            files.insert(entry.name().trim_start_matches('/').to_string(), buf);
        }
        Some(Package { files })
    }

    fn text(&self, name: &str) -> Option<String> {
        self.files.get(name).map(|b| String::from_utf8_lossy(b).into_owned())
    }

    /// Relationships of a part: id -> (type, target, external).
    fn rels(&self, part: &str) -> HashMap<String, (String, String, bool)> {
        let (dir, file) = part.rsplit_once('/').unwrap_or(("", part));
        let rels_path = if dir.is_empty() { format!("_rels/{file}.rels") } else { format!("{dir}/_rels/{file}.rels") };
        let mut out = HashMap::new();
        let Some(xml) = self.text(&rels_path) else { return out };
        let Ok(doc) = Document::parse(&xml) else { return out };
        for r in doc.descendants().filter(|n| n.tag_name().name() == "Relationship") {
            let (Some(id), Some(kind), Some(target)) = (r.attribute("Id"), r.attribute("Type"), r.attribute("Target")) else { continue };
            let external = r.attribute("TargetMode") == Some("External");
            let target = if external {
                target.to_string()
            } else if let Some(abs) = target.strip_prefix('/') {
                abs.to_string()
            } else {
                normalise(&if dir.is_empty() { target.to_string() } else { format!("{dir}/{target}") })
            };
            out.insert(id.to_string(), (kind.to_string(), target, external));
        }
        out
    }
}

fn normalise(path: &str) -> String {
    let mut parts: Vec<&str> = Vec::new();
    for p in path.split('/') {
        match p {
            "" | "." => {}
            ".." => {
                parts.pop();
            }
            p => parts.push(p),
        }
    }
    parts.join("/")
}

// --- Numbering --------------------------------------------------------------------

fn roman(mut n: usize) -> String {
    let mut out = String::new();
    for (v, s) in [(1000, "m"), (900, "cm"), (500, "d"), (400, "cd"), (100, "c"), (90, "xc"), (50, "l"), (40, "xl"), (10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i")] {
        while n >= v {
            out.push_str(s);
            n -= v;
        }
    }
    out
}

fn letters(n: usize) -> String {
    // Word repeats letters after z: a..z, aa..zz
    let n = n.max(1);
    let ch = (b'a' + ((n - 1) % 26) as u8) as char;
    std::iter::repeat_n(ch, (n - 1) / 26 + 1).collect()
}

fn fmt_number(n: usize, kind: &str) -> String {
    match kind {
        "lowerLetter" => letters(n),
        "upperLetter" => letters(n).to_uppercase(),
        "lowerRoman" => roman(n),
        "upperRoman" => roman(n).to_uppercase(),
        "decimalZero" => format!("{n:02}"),
        "none" => String::new(),
        _ => n.to_string(),
    }
}

#[derive(Clone)]
struct Level {
    fmt: String,
    text: String,
    start: usize,
}

fn level_of(lvl: &Node) -> Level {
    let val = |tag: &str, default: &str| child(lvl, tag).and_then(|e| wattr(&e, "val")).unwrap_or(default).to_string();
    Level { fmt: val("numFmt", "decimal"), text: val("lvlText", "%1."), start: val("start", "1").parse().unwrap_or(1) }
}

/// Works out list labels ("4.2", "(b)", "•") the way Word displays them.
#[derive(Default)]
struct Numbering {
    levels: HashMap<String, HashMap<usize, Level>>,
    counters: HashMap<String, std::collections::BTreeMap<usize, usize>>,
}

impl Numbering {
    fn load(xml: Option<String>) -> Numbering {
        let mut me = Numbering::default();
        let Some(xml) = xml else { return me };
        let Ok(doc) = Document::parse(&xml) else { return me };
        let root = doc.root_element();
        let mut abstract_: HashMap<String, HashMap<usize, Level>> = HashMap::new();
        for a in children(&root, "abstractNum") {
            let levels = children(&a, "lvl").map(|l| (wattr(&l, "ilvl").and_then(|v| v.parse().ok()).unwrap_or(0), level_of(&l))).collect();
            abstract_.insert(wattr(&a, "abstractNumId").unwrap_or("").to_string(), levels);
        }
        for num in children(&root, "num") {
            let Some(r) = child(&num, "abstractNumId") else { continue };
            let mut levels = abstract_.get(wattr(&r, "val").unwrap_or("")).cloned().unwrap_or_default();
            for ov in children(&num, "lvlOverride") {
                let ilvl: usize = wattr(&ov, "ilvl").and_then(|v| v.parse().ok()).unwrap_or(0);
                if let Some(lvl) = child(&ov, "lvl") {
                    levels.insert(ilvl, level_of(&lvl));
                }
                if let Some(start) = child(&ov, "startOverride") {
                    if let Some(l) = levels.get_mut(&ilvl) {
                        l.start = wattr(&start, "val").and_then(|v| v.parse().ok()).unwrap_or(1);
                    }
                }
            }
            me.levels.insert(wattr(&num, "numId").unwrap_or("").to_string(), levels);
        }
        me
    }

    /// (label, is_bullet) for the next item of this list.
    fn label(&mut self, num_id: &str, ilvl: usize) -> Option<(String, bool)> {
        let levels = self.levels.get(num_id)?;
        if num_id == "0" || !levels.contains_key(&ilvl) {
            return None;
        }
        let counters = self.counters.entry(num_id.to_string()).or_default();
        let lvl = &levels[&ilvl];
        let next = counters.get(&ilvl).copied().unwrap_or(lvl.start.saturating_sub(1)) + 1;
        counters.insert(ilvl, next);
        counters.retain(|k, _| *k <= ilvl);
        if lvl.fmt == "bullet" {
            return Some((String::new(), true));
        }
        let re = regex::Regex::new(r"%(\d)").unwrap();
        let text = re.replace_all(&lvl.text, |c: &regex::Captures| {
            let k: usize = c[1].parse::<usize>().unwrap_or(1).saturating_sub(1);
            match levels.get(&k) {
                None => String::new(),
                Some(l) => fmt_number(counters.get(&k).copied().unwrap_or(l.start), &l.fmt),
            }
        });
        Some((text.trim().to_string(), false))
    }
}

// --- Styles ------------------------------------------------------------------------

struct StyleInfo {
    name: String,
    based: Option<String>,
    outline: Option<usize>,
    num: Option<(String, usize)>,
}

#[derive(Default)]
struct Styles {
    by_id: HashMap<String, StyleInfo>,
}

fn num_pr(ppr: &Node) -> Option<(String, usize)> {
    let num = child(ppr, "numPr")?;
    let nid = child(&num, "numId")?;
    let ilvl = child(&num, "ilvl").and_then(|i| wattr(&i, "val")).and_then(|v| v.parse().ok()).unwrap_or(0);
    Some((wattr(&nid, "val").unwrap_or("").to_string(), ilvl))
}

impl Styles {
    fn load(xml: Option<String>) -> Styles {
        let mut me = Styles::default();
        let Some(xml) = xml else { return me };
        let Ok(doc) = Document::parse(&xml) else { return me };
        for s in children(&doc.root_element(), "style") {
            let sid = wattr(&s, "styleId").unwrap_or("").to_string();
            let name = child(&s, "name").and_then(|n| wattr(&n, "val")).map(str::to_string).unwrap_or_else(|| sid.clone());
            let based = child(&s, "basedOn").and_then(|b| wattr(&b, "val")).map(str::to_string);
            let (mut outline, mut num) = (None, None);
            if let Some(ppr) = child(&s, "pPr") {
                outline = child(&ppr, "outlineLvl").map(|o| wattr(&o, "val").and_then(|v| v.parse().ok()).unwrap_or(9));
                num = num_pr(&ppr);
            }
            me.by_id.insert(sid, StyleInfo { name, based, outline, num });
        }
        me
    }

    fn heading_level<'a>(&'a self, mut style: Option<&'a str>) -> Option<usize> {
        let re = regex::Regex::new(r"^heading\s*(\d)$").unwrap();
        for _ in 0..10 {
            let info = self.by_id.get(style?)?;
            let name = info.name.to_lowercase();
            if name == "title" {
                return Some(1);
            }
            if let Some(c) = re.captures(&name) {
                return Some((c[1].parse::<usize>().unwrap() + 1).min(6));
            }
            if let Some(o) = info.outline.filter(|o| *o < 9) {
                return Some((o + 2).min(6));
            }
            style = info.based.as_deref();
        }
        None
    }

    fn numbering<'a>(&'a self, mut style: Option<&'a str>) -> Option<(String, usize)> {
        for _ in 0..10 {
            let info = self.by_id.get(style?)?;
            if info.num.is_some() {
                return info.num.clone();
            }
            style = info.based.as_deref();
        }
        None
    }

    fn name(&self, style: Option<&str>) -> &str {
        style.and_then(|s| self.by_id.get(s)).map(|i| i.name.as_str()).unwrap_or("")
    }
}

// --- Body ---------------------------------------------------------------------------

struct Writer<'a> {
    styles: Styles,
    numbering: Numbering,
    notes: HashMap<String, String>,
    links: &'a HashMap<String, (String, String, bool)>,
    blocks: Vec<String>,
    page: usize,
    page_breaks: usize,
    text_since_break: bool,
    tracked: bool,
    used_notes: Vec<String>,
}

impl Writer<'_> {
    /// A page break becomes a marker on its own line.
    fn new_page(&mut self, current: Vec<String>) -> Vec<String> {
        if !self.text_since_break {
            return current;
        }
        let joined = current.concat();
        if !joined.trim().is_empty() {
            self.blocks.push(joined.trim().to_string());
        }
        self.page += 1;
        self.page_breaks += 1;
        self.blocks.push(page_marker(self.page));
        self.text_since_break = false;
        Vec::new()
    }

    /// Inline content of a paragraph (or hyperlink, insertion, field).
    fn runs(&mut self, el: &Node, mut out: Vec<String>, extra: &mut Vec<String>, in_table: bool) -> Vec<String> {
        for c in el.children().filter(|c| c.is_element() && c.tag_name().namespace() == Some(W)) {
            match c.tag_name().name() {
                "r" => out = self.run(&c, out, extra, in_table),
                "hyperlink" => {
                    let text = self.runs(&c, Vec::new(), extra, in_table).concat();
                    let url = c
                        .attribute((R, "id"))
                        .and_then(|id| self.links.get(id))
                        .filter(|(_, _, ext)| *ext)
                        .map(|(_, t, _)| t.clone());
                    match url {
                        Some(u) if (u.starts_with("http://") || u.starts_with("https://") || u.starts_with("mailto:")) && !text.trim().is_empty() => {
                            out.push(format!("[{text}]({})", u.replace(')', "%29").replace(' ', "%20")))
                        }
                        _ => out.push(text),
                    }
                }
                "ins" | "moveTo" => {
                    self.tracked = true;
                    out = self.runs(&c, out, extra, in_table);
                }
                "del" | "moveFrom" => self.tracked = true,
                "smartTag" | "sdt" | "sdtContent" | "fldSimple" | "customXml" | "bdo" | "dir" => {
                    out = self.runs(&c, out, extra, in_table);
                }
                _ => {}
            }
        }
        out
    }

    fn run(&mut self, r: &Node, mut out: Vec<String>, extra: &mut Vec<String>, in_table: bool) -> Vec<String> {
        let rpr = child(r, "rPr");
        if rpr.is_some_and(|p| on(child(&p, "vanish"))) {
            return out;
        }
        let bold = rpr.is_some_and(|p| on(child(&p, "b")));
        let italic = rpr.is_some_and(|p| on(child(&p, "i")));
        let mut text = String::new();
        let flush = |text: &mut String, out: &mut Vec<String>, since: &mut bool| {
            let s = std::mem::take(text);
            if s.is_empty() {
                return;
            }
            if !s.trim().is_empty() && (bold || italic) {
                let core = s.trim();
                let lead = &s[..s.len() - s.trim_start().len()];
                let trail = &s[s.trim_end().len()..];
                let mark = if bold && italic { "***" } else if bold { "**" } else { "*" };
                out.push(format!("{lead}{mark}{}{mark}{trail}", escape(core)));
            } else {
                out.push(escape(&s));
            }
            if !s.trim().is_empty() {
                *since = true;
            }
        };
        for c in r.children().filter(|c| c.is_element()) {
            let ns_w = c.tag_name().namespace() == Some(W);
            match (ns_w, c.tag_name().name()) {
                (true, "t") => text.push_str(c.text().unwrap_or("")),
                (true, "tab") => text.push(' '),
                (true, "br") | (true, "cr") => {
                    if wattr(&c, "type") == Some("page") && !in_table {
                        flush(&mut text, &mut out, &mut self.text_since_break);
                        out = self.new_page(out);
                    } else {
                        text.push('\n');
                    }
                }
                (true, "lastRenderedPageBreak") if !in_table => {
                    flush(&mut text, &mut out, &mut self.text_since_break);
                    out = self.new_page(out);
                }
                (true, "noBreakHyphen") => text.push('-'),
                (true, "softHyphen") => {}
                (true, "sym") => text.push('•'),
                (true, kind @ ("footnoteReference" | "endnoteReference")) => {
                    flush(&mut text, &mut out, &mut self.text_since_break);
                    let key = format!("{}{}", if kind == "footnoteReference" { "f" } else { "e" }, wattr(&c, "id").unwrap_or(""));
                    if self.notes.contains_key(&key) {
                        if !self.used_notes.contains(&key) {
                            self.used_notes.push(key.clone());
                        }
                        let n = self.used_notes.iter().position(|k| *k == key).unwrap() + 1;
                        out.push(format!("[^{n}]"));
                    }
                }
                (w, name) if (w && matches!(name, "drawing" | "pict" | "object")) || name == "AlternateContent" => {
                    // Text boxes: their paragraphs follow the one that holds them.
                    if let Some(bx) = c.descendants().find(|d| is(d, "txbxContent")) {
                        for p in bx.descendants().filter(|d| is(d, "p")) {
                            let t = self.runs(&p, Vec::new(), &mut Vec::new(), true).concat();
                            if !t.trim().is_empty() {
                                extra.push(t.trim().to_string());
                            }
                        }
                    }
                }
                _ => {}
            }
        }
        flush(&mut text, &mut out, &mut self.text_since_break);
        out
    }

    fn paragraph(&mut self, p: &Node, in_table: bool) -> Option<String> {
        let ppr = child(p, "pPr");
        let style_id = ppr.and_then(|pp| child(&pp, "pStyle")).and_then(|s| wattr(&s, "val")).map(str::to_string);
        let mut num = ppr.and_then(|pp| num_pr(&pp));
        let mut extra = Vec::new();
        let content = self.runs(p, Vec::new(), &mut extra, in_table);
        // Lines inside one paragraph keep their breaks.
        let text = content.concat().split('\n').map(str::trim).collect::<Vec<_>>().join("  \n").trim().to_string();
        let level = if in_table { None } else { self.styles.heading_level(style_id.as_deref()) };
        if num.is_none() {
            num = self.styles.numbering(style_id.as_deref());
        }
        let (mut label, mut bullet, mut ilvl) = (String::new(), false, 0);
        if let (Some((nid, lvl)), false) = (&num, text.is_empty()) {
            if let Some((l, b)) = self.numbering.label(nid, *lvl) {
                label = l;
                bullet = b;
                ilvl = *lvl;
            }
        }
        if text.is_empty() {
            self.blocks.extend(extra);
            return None;
        }
        let indent = "  ".repeat(ilvl);
        let line = if level.is_some() && text.chars().count() <= 300 && !text.contains('\n') {
            let plain = if text.starts_with("**") { text.replace("**", "").replace('*', "") } else { text.clone() };
            let lbl = if label.is_empty() { String::new() } else { format!("{} ", escape(&label)) };
            format!("{} {lbl}{plain}", "#".repeat(level.unwrap()))
        } else if bullet {
            format!("{indent}- {text}")
        } else if !label.is_empty() {
            format!("{indent}{} {text}", escape(&label))
        } else if self.styles.name(style_id.as_deref()).to_lowercase().contains("list bullet") {
            format!("- {text}")
        } else {
            text
        };
        if in_table {
            return Some(line);
        }
        self.blocks.push(line.clone());
        self.blocks.extend(extra);
        Some(line)
    }

    fn table(&mut self, tbl: &Node) {
        let mut rows = Vec::new();
        for tr in children(tbl, "tr") {
            let mut row = Vec::new();
            for tc in children(&tr, "tc") {
                let (mut span, mut merged) = (1usize, false);
                if let Some(tcpr) = child(&tc, "tcPr") {
                    span = child(&tcpr, "gridSpan").and_then(|g| wattr(&g, "val")).and_then(|v| v.parse().ok()).unwrap_or(1);
                    merged = child(&tcpr, "vMerge").is_some_and(|v| wattr(&v, "val").unwrap_or("continue") == "continue");
                }
                let mut parts = Vec::new();
                if !merged {
                    let paras: Vec<Node> = tc.descendants().filter(|d| is(d, "p")).collect();
                    for p in paras {
                        if let Some(t) = self.paragraph(&p, true) {
                            parts.push(t.replace("  \n", "\n"));
                        }
                    }
                }
                row.push(parts.join("\n"));
                row.extend(std::iter::repeat_n(String::new(), span.saturating_sub(1)));
            }
            rows.push(row);
        }
        let rendered = table(&rows);
        if !rendered.is_empty() {
            self.blocks.push(rendered);
            self.text_since_break = true;
        }
    }

    fn body(&mut self, body: &Node) {
        for c in body.children().filter(|c| c.is_element() && c.tag_name().namespace() == Some(W)) {
            match c.tag_name().name() {
                "p" => {
                    self.paragraph(&c, false);
                }
                "tbl" => self.table(&c),
                "sdt" => {
                    if let Some(content) = child(&c, "sdtContent") {
                        self.body(&content);
                    }
                }
                "ins" | "customXml" => {
                    self.tracked = self.tracked || c.tag_name().name() == "ins";
                    self.body(&c);
                }
                _ => {}
            }
        }
    }
}

/// Footnote and endnote text by id ("f2", "e1"), and whether the document
/// has comments.
fn notes(pkg: &Package, rels: &HashMap<String, (String, String, bool)>) -> (HashMap<String, String>, bool) {
    let (mut notes, mut comments) = (HashMap::new(), false);
    for (kind, target, external) in rels.values() {
        if *external {
            continue;
        }
        if kind == REL_COMMENTS {
            comments = true;
            continue;
        }
        let prefix = match kind.as_str() {
            REL_FOOTNOTES => "f",
            REL_ENDNOTES => "e",
            _ => continue,
        };
        let Some(xml) = pkg.text(target) else { continue };
        let Ok(doc) = Document::parse(&xml) else { continue };
        for note in doc.root_element().children().filter(|n| n.is_element()) {
            if matches!(wattr(&note, "type"), Some("separator" | "continuationSeparator" | "continuationNotice")) {
                continue;
            }
            let text = note
                .descendants()
                .filter(|d| is(d, "p"))
                .map(|p| p.descendants().filter(|d| is(d, "t")).map(|t| t.text().unwrap_or("")).collect::<String>().trim().to_string())
                .collect::<Vec<_>>()
                .join(" ")
                .trim()
                .to_string();
            if !text.is_empty() {
                notes.insert(format!("{prefix}{}", wattr(&note, "id").unwrap_or("")), text);
            }
        }
    }
    (notes, comments)
}

/// Blank lines between blocks, but list items stay together.
fn join_blocks(blocks: &[String]) -> String {
    let re = regex::Regex::new(r"^\s*(- |\d+\. )").unwrap();
    let mut out: Vec<String> = Vec::new();
    for b in blocks {
        let joins = out.last().is_some_and(|last| re.is_match(b) && re.is_match(last.rsplit('\n').next().unwrap_or("")));
        if joins {
            let last = out.last_mut().unwrap();
            last.push('\n');
            last.push_str(b);
        } else {
            out.push(b.clone());
        }
    }
    out.join("\n\n")
}

fn core_props(pkg: &Package) -> (Option<String>, Option<String>) {
    let Some(xml) = pkg.text("docProps/core.xml") else { return (None, None) };
    let Ok(doc) = Document::parse(&xml) else { return (None, None) };
    let get = |name: &str| doc.descendants().find(|n| n.tag_name().name() == name).and_then(|n| n.text()).map(|t| t.trim().to_string()).filter(|t| !t.is_empty());
    let title = get("title");
    // python-docx gives the date without its zone: 2024-03-01T10:00:00
    let date = get("created").or_else(|| get("modified")).and_then(|d| {
        let d = d.trim_end_matches('Z');
        let d = d.split('.').next().unwrap_or(d);
        chrono::NaiveDateTime::parse_from_str(d, "%Y-%m-%dT%H:%M:%S").ok().map(|t| t.format("%Y-%m-%dT%H:%M:%S").to_string())
    });
    (title, date)
}

fn saved_pages(pkg: &Package) -> Option<usize> {
    let xml = pkg.text("docProps/app.xml")?;
    let re = regex::Regex::new(r"<Pages>(\d+)</Pages>").unwrap();
    re.captures(&xml)?[1].parse().ok()
}

pub fn convert(path: &Path, _opts: &Options) -> Converted {
    let damaged = || {
        ConversionError::new(
            "This Word document can't be opened. It may be password-protected or damaged. \
             If it has a password, open it in Word, remove the password, save a copy and upload that.",
        )
    };
    let pkg = Package::open(path).ok_or_else(damaged)?;
    let main_part = pkg
        .rels("")
        .into_values()
        .find(|(k, _, _)| k == REL_DOCUMENT)
        .map(|(_, t, _)| t)
        .unwrap_or_else(|| "word/document.xml".into());
    let xml = pkg.text(&main_part).ok_or_else(damaged)?;
    let doc = Document::parse_with_options(&xml, roxmltree::ParsingOptions { allow_dtd: false, nodes_limit: u32::MAX, ..Default::default() }).map_err(|_| damaged())?;
    let body = doc.root_element().children().find(|c| is(c, "body")).ok_or_else(damaged)?;
    let rels = pkg.rels(&main_part);
    let part_of = |kind: &str| rels.values().find(|(k, _, e)| k == kind && !e).and_then(|(_, t, _)| pkg.text(t));
    let (notes, comments) = notes(&pkg, &rels);
    let mut w = Writer {
        styles: Styles::load(part_of(REL_STYLES)),
        numbering: Numbering::load(part_of(REL_NUMBERING)),
        notes,
        links: &rels,
        blocks: vec![page_marker(1)],
        page: 1,
        page_breaks: 0,
        text_since_break: false,
        tracked: false,
        used_notes: Vec::new(),
    };
    w.body(&body);
    let mut warnings = Vec::new();
    let saved = saved_pages(&pkg);
    let page_count = if w.page_breaks == 0 && saved.unwrap_or(1) > 1 {
        // No page positions saved: one marker for page 1 would mislead.
        let first = page_marker(1);
        w.blocks.retain(|b| *b != first);
        warnings.push("Word didn't save page positions in this document, so passages can't be cited by page number.".to_string());
        saved
    } else if w.page_breaks > 0 {
        Some(w.page.max(saved.unwrap_or(0)))
    } else {
        Some(saved.unwrap_or(1))
    };
    if w.tracked {
        warnings.push("This document has tracked changes. The Markdown shows the text with all changes accepted.".into());
    }
    if comments {
        warnings.push("This document has comments. They aren't included in the Markdown; open the original to read them.".into());
    }
    if !w.used_notes.is_empty() {
        w.blocks.push(String::new());
        let lines: Vec<String> = w.used_notes.iter().enumerate().map(|(i, k)| format!("[^{}]: {}", i + 1, escape(&w.notes[k]))).collect();
        w.blocks.extend(lines);
    }
    let body = join_blocks(&w.blocks);
    if body.replace(&page_marker(1), "").trim().is_empty() {
        warnings.push("This document has no text.".into());
    }
    let (title, doc_date) = core_props(&pkg);
    Ok(ConvResult { body: tidy(&body), page_count, warnings, doc_date, title, ..Default::default() })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn numbers() {
        assert_eq!(roman(14), "xiv");
        assert_eq!(letters(27), "aa");
        assert_eq!(fmt_number(3, "upperLetter"), "C");
        assert_eq!(fmt_number(3, "decimalZero"), "03");
    }

    #[test]
    fn list_items_stay_together() {
        let b = vec!["a".to_string(), "1. x".to_string(), "2. y".to_string(), "b".to_string()];
        assert_eq!(join_blocks(&b), "a\n\n1. x\n2. y\n\nb");
    }
}
