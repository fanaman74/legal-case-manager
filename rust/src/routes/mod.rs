//! The API, one module per area, with the same addresses and answers as the
//! Python version so the web app works unchanged.

pub mod audit_log;
pub mod cases;
pub mod files;
pub mod session;
pub mod users;
pub mod work;

use axum::extract::{FromRequest, Query, Request};
use serde::de::DeserializeOwned;

use crate::util::{chars, ApiError, ApiResult};

/// A JSON request body. Unknown fields and wrong types are refused with 422,
/// as the Python version's models did (`extra="forbid"`).
pub struct Body<T>(pub T);

impl<S: Send + Sync, T: DeserializeOwned> FromRequest<S> for Body<T> {
    type Rejection = ApiError;

    async fn from_request(req: Request, state: &S) -> Result<Body<T>, ApiError> {
        let bytes = axum::body::Bytes::from_request(req, state)
            .await
            .map_err(|_| ApiError::invalid("The request couldn't be read."))?;
        serde_json::from_slice(&bytes).map(Body).map_err(|e| ApiError::invalid(e.to_string()))
    }
}

/// Query parameters; a missing or malformed one is refused with 422.
pub struct Params<T>(pub T);

impl<S: Send + Sync, T: DeserializeOwned> axum::extract::FromRequestParts<S> for Params<T> {
    type Rejection = ApiError;

    async fn from_request_parts(parts: &mut axum::http::request::Parts, state: &S) -> Result<Params<T>, ApiError> {
        Query::<T>::from_request_parts(parts, state)
            .await
            .map(|q| Params(q.0))
            .map_err(|e| ApiError::invalid(e.body_text()))
    }
}

/// Length limits, counted in characters like the Python version.
pub fn max_len(field: &str, value: &str, max: usize) -> ApiResult<()> {
    if chars(value) > max {
        return Err(ApiError::invalid(format!("{field}: at most {max} characters")));
    }
    Ok(())
}

pub fn min_len(field: &str, value: &str, min: usize) -> ApiResult<()> {
    if chars(value) < min {
        return Err(ApiError::invalid(format!("{field}: at least {min} characters")));
    }
    Ok(())
}

pub fn max_items<T>(field: &str, list: &[T], max: usize) -> ApiResult<()> {
    if list.len() > max {
        return Err(ApiError::invalid(format!("{field}: at most {max} items")));
    }
    Ok(())
}

/// Range check for a query number (FastAPI's ge/le).
pub fn in_range(field: &str, v: i64, lo: i64, hi: i64) -> ApiResult<()> {
    if v < lo || v > hi {
        return Err(ApiError::invalid(format!("{field}: must be between {lo} and {hi}")));
    }
    Ok(())
}
