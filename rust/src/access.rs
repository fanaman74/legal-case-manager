//! Who can see and change which case. Every case route goes through these
//! checks on the server; the browser never decides.
//!
//! - Admin: every case.
//! - Editor: assigned cases; can upload, tag and edit details (not rename).
//! - Read-only: assigned cases; can view and download only.
//!
//! A case someone isn't assigned to is reported as not found, so its name and
//! existence don't leak.

use rusqlite::{Connection, OptionalExtension};
use serde_json::{json, Value};

use crate::accounts::User;
use crate::util::{ApiError, ApiResult};

pub const NOT_FOUND: &str = "That case doesn't exist, or you don't have access to it.";

#[derive(Clone, Debug)]
pub struct Case {
    pub id: String,
    pub name: String,
    pub reference: String,
    pub client: String,
    pub description: String,
    pub notes: String,
    pub status: String,
    pub created_at: String,
    pub created_by: Option<i64>,
}

impl Case {
    pub const COLUMNS: &'static str = "c.id, c.name, c.reference, c.client, c.description, c.notes, c.status, c.created_at, c.created_by";

    pub fn from_row(r: &rusqlite::Row) -> rusqlite::Result<Case> {
        Ok(Case {
            id: r.get(0)?,
            name: r.get(1)?,
            reference: r.get(2)?,
            client: r.get(3)?,
            description: r.get(4)?,
            notes: r.get(5)?,
            status: r.get(6)?,
            created_at: r.get(7)?,
            created_by: r.get(8)?,
        })
    }

    pub fn view(&self) -> serde_json::Map<String, Value> {
        let v = json!({
            "id": self.id, "name": self.name, "reference": self.reference, "client": self.client,
            "description": self.description, "notes": self.notes, "status": self.status, "created_at": self.created_at,
        });
        match v {
            Value::Object(m) => m,
            _ => unreachable!(),
        }
    }
}

pub fn load_case(db: &Connection, case_id: &str) -> rusqlite::Result<Option<Case>> {
    db.query_row(&format!("SELECT {} FROM cases c WHERE c.id = ?", Case::COLUMNS), [case_id], Case::from_row)
        .optional()
}

pub fn visible_case(db: &Connection, user: &User, case_id: &str) -> ApiResult<Case> {
    let case = load_case(db, case_id)?.ok_or_else(|| ApiError::new(404, NOT_FOUND))?;
    if !user.is_admin() {
        let member: Option<i64> = db
            .query_row("SELECT 1 FROM case_members WHERE case_id = ? AND user_id = ?", (case_id, user.id), |r| r.get(0))
            .optional()?;
        if member.is_none() {
            return Err(ApiError::new(404, NOT_FOUND));
        }
    }
    Ok(case)
}

/// Upload, tag and edit details, in a case the user can already see.
pub fn can_edit(user: &User) -> bool {
    user.role == "admin" || user.role == "editor"
}

pub fn require_edit(user: &User, case: &Case) -> ApiResult<()> {
    if !can_edit(user) {
        return Err(ApiError::new(403, "Your role is read-only in this case. Ask the Admin if you need to make changes."));
    }
    if case.status == "archived" {
        return Err(ApiError::new(409, "This case is archived. The Admin can restore it to make changes."));
    }
    Ok(())
}

pub fn permissions(user: &User, case: &Case) -> Value {
    let active = case.status == "active";
    json!({ "edit": can_edit(user) && active, "upload": can_edit(user) && active, "manage": user.is_admin() })
}
