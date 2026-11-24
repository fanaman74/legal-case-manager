//! Images (PNG, JPEG, TIFF including multi-page scans): read with OCR.

use std::io::BufReader;
use std::path::Path;

use image::{DynamicImage, ImageBuffer, ImageReader};

use super::markdown::{page_marker, tidy};
use super::{ocr, previews, ConvResult, Converted, ConversionError, Options};

/// A 600 dpi A3 scan is about 70 million pixels; refuse anything far bigger
/// rather than run out of memory.
const MAX_PIXELS: u64 = 200_000_000;
const MAX_PAGES: usize = 2000;

fn cant_open() -> ConversionError {
    ConversionError::new("This image can't be opened. It may be damaged, or not really an image. Open it on your computer to check it.")
}

/// Every page of a TIFF (scanners save multi-page TIFFs); one page for
/// anything else. The bool says whether more pages were left out.
fn pages(path: &Path) -> Result<(Vec<DynamicImage>, bool), ConversionError> {
    let head = {
        let mut b = [0u8; 4];
        let mut f = std::fs::File::open(path).map_err(|_| cant_open())?;
        let n = std::io::Read::read(&mut f, &mut b).unwrap_or(0);
        b[..n].to_vec()
    };
    if head.starts_with(b"II*\0") || head.starts_with(b"MM\0*") {
        return tiff_pages(path);
    }
    let mut reader = ImageReader::open(path).map_err(|_| cant_open())?.with_guessed_format().map_err(|_| cant_open())?;
    let mut limits = image::Limits::default();
    limits.max_alloc = Some(MAX_PIXELS * 4);
    reader.limits(limits);
    let img = reader.decode().map_err(|_| cant_open())?;
    Ok((vec![img], false))
}

fn tiff_pages(path: &Path) -> Result<(Vec<DynamicImage>, bool), ConversionError> {
    use tiff::decoder::{Decoder, DecodingResult, Limits};
    let f = std::fs::File::open(path).map_err(|_| cant_open())?;
    let mut limits = Limits::default();
    limits.decoding_buffer_size = (MAX_PIXELS * 4) as usize;
    let mut dec = Decoder::new(BufReader::new(f)).map_err(|_| cant_open())?.with_limits(limits);
    let mut out = Vec::new();
    loop {
        if out.len() == MAX_PAGES {
            return Ok((out, dec.more_images()));
        }
        let (w, h) = dec.dimensions().map_err(|_| cant_open())?;
        if w as u64 * h as u64 > MAX_PIXELS {
            return Err(cant_open());
        }
        let color = dec.colortype().map_err(|_| cant_open())?;
        let data = dec.read_image().map_err(|_| cant_open())?;
        let img = match (data, color) {
            (DecodingResult::U8(d), tiff::ColorType::Gray(8)) => ImageBuffer::from_raw(w, h, d).map(DynamicImage::ImageLuma8),
            (DecodingResult::U8(d), tiff::ColorType::RGB(8)) => ImageBuffer::from_raw(w, h, d).map(DynamicImage::ImageRgb8),
            (DecodingResult::U8(d), tiff::ColorType::RGBA(8)) => ImageBuffer::from_raw(w, h, d).map(DynamicImage::ImageRgba8),
            (DecodingResult::U8(d), tiff::ColorType::GrayA(8)) => ImageBuffer::from_raw(w, h, d).map(DynamicImage::ImageLumaA8),
            (DecodingResult::U8(d), tiff::ColorType::Gray(1)) => {
                // Bilevel fax scans: one bit per pixel, rows padded to a byte.
                let row = w.div_ceil(8) as usize;
                let mut px = Vec::with_capacity((w * h) as usize);
                for y in 0..h as usize {
                    for x in 0..w as usize {
                        let bit = d.get(y * row + x / 8).map(|b| (b >> (7 - x % 8)) & 1).unwrap_or(0);
                        px.push(if bit == 1 { 255 } else { 0 });
                    }
                }
                ImageBuffer::from_raw(w, h, px).map(DynamicImage::ImageLuma8)
            }
            (DecodingResult::U16(d), tiff::ColorType::Gray(16)) => ImageBuffer::from_raw(w, h, d).map(DynamicImage::ImageLuma16),
            (DecodingResult::U16(d), tiff::ColorType::RGB(16)) => ImageBuffer::from_raw(w, h, d).map(DynamicImage::ImageRgb16),
            _ => None,
        };
        out.push(img.ok_or_else(cant_open)?);
        if !dec.more_images() {
            return Ok((out, false));
        }
        dec.next_image().map_err(|_| cant_open())?;
    }
}

pub fn convert(path: &Path, opts: &Options) -> Converted {
    let (frames, more) = pages(path)?;
    let mut parts: Vec<String> = Vec::new();
    let mut warnings = Vec::new();
    let mut found_text = false;
    for (i, frame) in frames.iter().enumerate() {
        let n = i + 1;
        previews::save(frame, opts, n);
        let text = ocr::read_image(frame, opts)?;
        found_text |= !text.is_empty();
        parts.extend([page_marker(n), String::new(), if text.is_empty() { "*(No text found on this page.)*".into() } else { text }, String::new()]);
    }
    if more {
        warnings.push(format!("Only the first {MAX_PAGES} pages were read."));
    }
    if !found_text {
        warnings.push("OCR found no text in this image.".into());
    }
    let count = frames.len();
    Ok(ConvResult {
        body: tidy(&parts.join("\n")),
        page_count: Some(count),
        ocr: true,
        warnings,
        previews: if opts.previews.is_some() { count } else { 0 },
        ..Default::default()
    })
}
