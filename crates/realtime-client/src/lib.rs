//! MeetPilot desktop ↔ backend client: REST (auth, meetings) and the realtime audio uplink.
//!
//! Only audio the user explicitly chose to capture is sent, only while capture is active and
//! not paused, and only to the user's configured MeetPilot server over TLS (plain HTTP is
//! accepted for localhost development only).

pub mod api;
pub mod stream;

pub use api::{
    ApiClient, ApiError, ContextSegment, CopilotAnswer, CopilotRequest, Me, MeetingCreated,
    StreamGrant, TokenPair, Usage,
};
pub use stream::{
    encode_frame, spawn_stream, EventSink, LinkStatus, Source, StreamConfig, StreamEvent,
    StreamHandle, TokenFuture, TokenProvider,
};
