//! The HTTPS certificate. The launcher passes its own (TLS_CERT, TLS_KEY);
//! when the program runs on its own, it makes a certificate for this computer
//! the first time and reuses it, so the browser warning only has to be
//! accepted once.

use axum_server::tls_rustls::RustlsConfig;

use crate::config::Settings;

pub async fn config(cfg: &Settings) -> anyhow::Result<RustlsConfig> {
    if !cfg.tls_cert.is_empty() && !cfg.tls_key.is_empty() {
        return Ok(RustlsConfig::from_pem_file(&cfg.tls_cert, &cfg.tls_key).await?);
    }
    let dir = cfg.run_dir();
    let (cert, key) = (dir.join("local-cert.pem"), dir.join("local-key.pem"));
    if !(cert.is_file() && key.is_file()) {
        std::fs::create_dir_all(&dir)?;
        let mut names = vec!["localhost".to_string(), "127.0.0.1".to_string(), "::1".to_string()];
        if let Ok(host) = std::env::var("COMPUTERNAME").or_else(|_| std::env::var("HOSTNAME")) {
            names.push(host.to_lowercase());
        }
        let made = rcgen::generate_simple_self_signed(names)?;
        std::fs::write(&key, made.key_pair.serialize_pem())?;
        std::fs::write(&cert, made.cert.pem())?;
    }
    Ok(RustlsConfig::from_pem_file(cert, key).await?)
}
