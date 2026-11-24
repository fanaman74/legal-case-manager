//! Everything a request needs that outlives it: settings, the database,
//! case storage and the sign-in throttle.

use std::sync::Arc;

use crate::accounts::Throttle;
use crate::config::Settings;
use crate::db::Database;
use crate::storage::Storage;
use crate::util::ApiResult;

#[derive(Clone)]
pub struct AppState(Arc<Inner>);

pub struct Inner {
    pub cfg: Settings,
    pub db: Database,
    pub storage: Storage,
    pub throttle: Throttle,
}

impl std::ops::Deref for AppState {
    type Target = Inner;
    fn deref(&self) -> &Inner {
        &self.0
    }
}

impl AppState {
    pub fn new(cfg: Settings) -> anyhow::Result<AppState> {
        let db = Database::open(&cfg.db_path())?;
        let storage = Storage::new(cfg.cases_dir());
        Ok(AppState(Arc::new(Inner { cfg, db, storage, throttle: Throttle::default() })))
    }

    /// Run blocking work (SQLite, files, password hashing) off the async
    /// threads.
    pub async fn run<T: Send + 'static>(&self, f: impl FnOnce(&AppState) -> ApiResult<T> + Send + 'static) -> ApiResult<T> {
        let st = self.clone();
        tokio::task::spawn_blocking(move || f(&st)).await?
    }
}
