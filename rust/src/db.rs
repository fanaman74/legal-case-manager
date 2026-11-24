//! SQLite database: users, sessions, cases, files, jobs and the audit log.
//!
//! One file, app.db, in the data folder, with the same tables as the Python
//! version: either version can open the other's database. Schema changes are
//! numbered steps; the database records how many have run (PRAGMA
//! user_version), so an upgrade only runs the new ones.

use std::path::{Path, PathBuf};

use rusqlite::{Connection, Transaction, TransactionBehavior};

use crate::notes;

enum Step {
    Sql(&'static str),
    Code(fn(&Transaction) -> rusqlite::Result<()>),
}

const MIGRATIONS: &[Step] = &[
    Step::Sql(include_str!("migrations/01.sql")),
    Step::Sql(include_str!("migrations/02.sql")),
    Step::Sql(include_str!("migrations/03.sql")),
    Step::Sql(include_str!("migrations/04.sql")),
    Step::Sql(include_str!("migrations/05.sql")),
    Step::Sql(include_str!("migrations/06.sql")),
    Step::Sql(include_str!("migrations/07.sql")),
    // 8: move notes out of the warnings of files converted before 7.
    Step::Code(notes::migrate),
];

#[derive(Clone)]
pub struct Database {
    path: PathBuf,
}

impl Database {
    pub fn open(path: &Path) -> anyhow::Result<Database> {
        if let Some(dir) = path.parent() {
            std::fs::create_dir_all(dir)?;
        }
        let db = Database { path: path.to_path_buf() };
        let mut conn = db.connect()?;
        conn.pragma_update(None, "journal_mode", "WAL")?;
        migrate(&mut conn)?;
        Ok(db)
    }

    pub fn connect(&self) -> rusqlite::Result<Connection> {
        let conn = Connection::open(&self.path)?;
        conn.busy_timeout(std::time::Duration::from_secs(30))?;
        conn.pragma_update(None, "foreign_keys", "ON")?;
        Ok(conn)
    }
}

fn migrate(conn: &mut Connection) -> anyhow::Result<()> {
    let version: usize = conn.query_row("PRAGMA user_version", [], |r| r.get::<_, i64>(0))? as usize;
    if version > MIGRATIONS.len() {
        anyhow::bail!(
            "This database was made by a newer version of Case File Manager (schema {version}). Install the newer version."
        );
    }
    for (i, step) in MIGRATIONS.iter().enumerate().skip(version) {
        let tx = conn.transaction_with_behavior(TransactionBehavior::Immediate)?;
        match step {
            Step::Sql(sql) => tx.execute_batch(sql)?,
            Step::Code(f) => f(&tx)?,
        }
        tx.pragma_update(None, "user_version", (i + 1) as i64)?;
        tx.commit()?;
    }
    Ok(())
}

/// A write transaction that takes the write lock up front (BEGIN IMMEDIATE),
/// so writers queue and audit entries chain in order.
pub fn write_tx(conn: &mut Connection) -> rusqlite::Result<Transaction<'_>> {
    conn.transaction_with_behavior(TransactionBehavior::Immediate)
}

/// Whether SQLite here has FTS5 (keyword search needs it).
pub fn fts5_available() -> bool {
    Connection::open_in_memory()
        .and_then(|c| c.execute_batch("CREATE VIRTUAL TABLE t USING fts5(body)"))
        .is_ok()
}
