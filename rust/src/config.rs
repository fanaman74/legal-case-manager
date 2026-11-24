//! Settings come from environment variables, the same names the Python
//! version used, so the launcher can start either one. The app runs only on
//! the local computer; there is no cloud mode.

use std::path::PathBuf;

#[derive(Clone, Debug)]
pub struct Settings {
    pub data_dir: PathBuf,
    pub version: String,
    pub host: String,
    pub port: u16,
    pub tls_cert: String,
    pub tls_key: String,
    pub max_upload_bytes: u64,
    pub ocr_lang: String,
    pub convert_workers: usize,
    pub ollama_url: String,
    pub embed_model: String,
    pub max_mailbox_bytes: u64,
    /// Where the built web screens are (index.html and assets/).
    pub web_dir: PathBuf,
}

fn env(name: &str, default: &str) -> String {
    std::env::var(name).ok().filter(|v| !v.is_empty()).unwrap_or_else(|| default.to_string())
}

impl Settings {
    pub fn load() -> anyhow::Result<Settings> {
        let mode = env("DEPLOY_MODE", "local");
        if mode != "local" {
            anyhow::bail!("DEPLOY_MODE must be 'local', got {mode:?}. The app only runs on the local computer.");
        }
        let cpus = std::thread::available_parallelism().map(|n| n.get()).unwrap_or(1);
        let workers: usize = env("CONVERT_WORKERS", "0").parse().unwrap_or(0);
        let workers = if workers == 0 { cpus.min(2) } else { workers };
        let exe_dir = std::env::current_exe()
            .ok()
            .and_then(|p| p.parent().map(|d| d.to_path_buf()))
            .unwrap_or_else(|| PathBuf::from("."));
        Ok(Settings {
            data_dir: PathBuf::from(env("DATA_DIR", default_data_dir())),
            version: env("APP_VERSION", env!("CARGO_PKG_VERSION")),
            // Started by the launcher (which passes its certificate), the app
            // serves the office network; double-clicked on its own, only this
            // computer, so Windows doesn't ask about the firewall.
            host: env("HOST", if env("TLS_CERT", "").is_empty() { "127.0.0.1" } else { "0.0.0.0" }),
            // 8444 on its own, so it can run beside the installed app on 8443.
            port: env("PORT", if env("TLS_CERT", "").is_empty() { "8444" } else { "8443" }).parse()?,
            tls_cert: env("TLS_CERT", ""),
            tls_key: env("TLS_KEY", ""),
            max_upload_bytes: env("MAX_UPLOAD_MB", "2048").parse::<u64>()? << 20,
            ocr_lang: env("OCR_LANG", "eng"),
            convert_workers: workers.clamp(1, 8),
            ollama_url: env("OLLAMA_URL", "http://127.0.0.1:11434"),
            embed_model: env("EMBED_MODEL", "bge-m3"),
            max_mailbox_bytes: env("MAX_MAILBOX_GB", "50").parse::<u64>()? << 30,
            web_dir: std::env::var("WEB_DIR").map(PathBuf::from).unwrap_or_else(|_| exe_dir.join("web")),
        })
    }

    /// Small runtime files (worker heartbeat, the Admin account handed over
    /// by the launcher, the local certificate).
    pub fn run_dir(&self) -> PathBuf {
        self.data_dir.join(".run")
    }
    pub fn db_path(&self) -> PathBuf {
        self.data_dir.join("app.db")
    }
    pub fn cases_dir(&self) -> PathBuf {
        self.data_dir.join("cases")
    }
    pub fn admin_account_file(&self) -> PathBuf {
        self.run_dir().join("admin-account.json")
    }
}

/// The Rust version keeps its data apart from the Python install until it
/// replaces it, so trying it out can't touch real case files.
fn default_data_dir() -> &'static str {
    if cfg!(windows) {
        r"C:\CaseFiles-rust\data"
    } else {
        "/data"
    }
}
