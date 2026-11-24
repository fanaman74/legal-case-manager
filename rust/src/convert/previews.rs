//! Page images for the document viewer. Made by the worker from the
//! original, so the browser only ever shows plain JPEGs the app created,
//! never the uploaded file itself.

use image::codecs::jpeg::JpegEncoder;
use image::imageops::FilterType;
use image::DynamicImage;

use super::Options;

pub const PREVIEW_WIDTH: u32 = 1100;

pub fn save(img: &DynamicImage, opts: &Options, page: usize) -> bool {
    let Some(dir) = &opts.previews else { return false };
    if std::fs::create_dir_all(dir).is_err() {
        return false;
    }
    let mut rgb = img.to_rgb8();
    if rgb.width() > PREVIEW_WIDTH {
        let h = ((rgb.height() as f64 * PREVIEW_WIDTH as f64 / rgb.width() as f64).round() as u32).max(1);
        rgb = image::imageops::resize(&rgb, PREVIEW_WIDTH, h, FilterType::Lanczos3);
    }
    let Ok(file) = std::fs::File::create(dir.join(format!("p{page:04}.jpg"))) else { return false };
    let mut w = std::io::BufWriter::new(file);
    JpegEncoder::new_with_quality(&mut w, 72).encode_image(&rgb).is_ok()
}
