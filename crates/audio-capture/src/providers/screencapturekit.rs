//! ScreenCaptureKit system-audio backend (macOS 13 – 14.5 fallback).
//!
//! SCStream always produces video; we request a 2x2 px, 1 fps stream of the main display,
//! drop every video sample immediately, and forward only `SCStreamOutputType::Audio`.
//! MeetPilot's own audio is excluded. No screen content is stored or transmitted.

use std::ffi::c_void;
use std::ptr::{self, NonNull};
use std::sync::mpsc;
use std::time::Duration;

use block2::RcBlock;
use dispatch2::{DispatchQueue, DispatchRetained};
use objc2::rc::Retained;
use objc2::runtime::ProtocolObject;
use objc2::{define_class, msg_send, AnyThread, DefinedClass};
use objc2_core_audio_types::{AudioBuffer, AudioBufferList, AudioStreamBasicDescription};
use objc2_core_foundation::CFRetained;
use objc2_core_graphics::{CGPreflightScreenCaptureAccess, CGRequestScreenCaptureAccess};
use objc2_core_media::{
    kCMSampleBufferFlag_AudioBufferList_Assure16ByteAlignment,
    CMAudioFormatDescriptionGetStreamBasicDescription, CMBlockBuffer, CMSampleBuffer, CMTime,
    CMTimeFlags,
};
use objc2_foundation::{NSArray, NSError, NSObject, NSObjectProtocol};
use objc2_screen_capture_kit::{
    SCContentFilter, SCShareableContent, SCStream, SCStreamConfiguration, SCStreamDelegate,
    SCStreamOutput, SCStreamOutputType,
};

use crate::error::{CaptureError, Result};
use crate::provider::{CaptureStream, RawAudioSink};
use crate::types::{AudioSource, NativeFormat, PermissionState};

const SAMPLE_RATE: u32 = 48_000;
const CHANNELS: u16 = 2;
/// SCStreamErrorUserDeclined
const ERR_USER_DECLINED: isize = -3801;

const K_AUDIO_FORMAT_FLAG_IS_FLOAT: u32 = 1 << 0;
const K_AUDIO_FORMAT_FLAG_IS_NON_INTERLEAVED: u32 = 1 << 5;

pub fn preflight() -> PermissionState {
    if CGPreflightScreenCaptureAccess() {
        PermissionState::Granted
    } else {
        // The API cannot distinguish "denied" from "not asked yet".
        PermissionState::NotDetermined
    }
}

pub fn request() -> PermissionState {
    if CGRequestScreenCaptureAccess() {
        PermissionState::Granted
    } else {
        PermissionState::Denied
    }
}

fn permission_error() -> CaptureError {
    CaptureError::PermissionDenied {
        audio: AudioSource::System,
        hint: "Open System Settings > Privacy & Security > Screen & System Audio Recording, enable MeetPilot, then restart MeetPilot.".into(),
    }
}

fn ns_error(e: &NSError) -> CaptureError {
    if e.code() == ERR_USER_DECLINED {
        return permission_error();
    }
    CaptureError::Backend {
        audio: AudioSource::System,
        detail: format!(
            "ScreenCaptureKit: {} (code {})",
            e.localizedDescription(),
            e.code()
        ),
    }
}

pub struct OutputIvars {
    sink: RawAudioSink,
}

define_class!(
    // SAFETY: NSObject has no subclassing requirements; we do not implement Drop on the class.
    #[unsafe(super(NSObject))]
    #[name = "MeetPilotSCAudioOutput"]
    #[ivars = OutputIvars]
    pub struct AudioOutput;

    unsafe impl NSObjectProtocol for AudioOutput {}

    unsafe impl SCStreamOutput for AudioOutput {
        #[unsafe(method(stream:didOutputSampleBuffer:ofType:))]
        unsafe fn stream_did_output(
            &self,
            _stream: &SCStream,
            sample_buffer: &CMSampleBuffer,
            kind: SCStreamOutputType,
        ) {
            if kind != SCStreamOutputType::Audio {
                return; // video frames are dropped immediately
            }
            let sink = &self.ivars().sink;
            if sink.is_paused() {
                return;
            }
            if let Some((samples, format)) = unsafe { extract_interleaved_f32(sample_buffer) } {
                sink.push_f32(&samples, format);
            }
        }
    }

    unsafe impl SCStreamDelegate for AudioOutput {
        #[unsafe(method(stream:didStopWithError:))]
        unsafe fn stream_did_stop(&self, _stream: &SCStream, error: &NSError) {
            let err = ns_error(error);
            let err = match err {
                CaptureError::Backend {
                    audio: source,
                    detail,
                } => CaptureError::DeviceUnavailable {
                    audio: source,
                    detail,
                },
                other => other,
            };
            self.ivars().sink.report_error(err);
        }
    }
);

impl AudioOutput {
    fn new(sink: RawAudioSink) -> Retained<Self> {
        let this = Self::alloc().set_ivars(OutputIvars { sink });
        unsafe { msg_send![super(this), init] }
    }
}

/// Copy the audio in a CMSampleBuffer into interleaved f32.
///
/// # Safety
/// `sb` must be a valid audio sample buffer delivered by ScreenCaptureKit.
unsafe fn extract_interleaved_f32(sb: &CMSampleBuffer) -> Option<(Vec<f32>, NativeFormat)> {
    let desc = unsafe { sb.format_description() }?;
    let asbd: *const AudioStreamBasicDescription =
        unsafe { CMAudioFormatDescriptionGetStreamBasicDescription(&desc) };
    let asbd = unsafe { asbd.as_ref() }?;
    let channels = asbd.mChannelsPerFrame.max(1) as usize;
    let is_float = asbd.mFormatFlags & K_AUDIO_FORMAT_FLAG_IS_FLOAT != 0;
    let non_interleaved = asbd.mFormatFlags & K_AUDIO_FORMAT_FLAG_IS_NON_INTERLEAVED != 0;
    let bits = asbd.mBitsPerChannel;
    if !(is_float && bits == 32) && !(!is_float && bits == 16) {
        log::warn!("unsupported ScreenCaptureKit audio format: float={is_float} bits={bits}");
        return None;
    }

    // Query the required AudioBufferList size, then fetch it (u64 storage => 8-byte aligned).
    let mut needed: usize = 0;
    let _ = unsafe {
        sb.audio_buffer_list_with_retained_block_buffer(
            &mut needed,
            ptr::null_mut(),
            0,
            None,
            None,
            kCMSampleBufferFlag_AudioBufferList_Assure16ByteAlignment,
            ptr::null_mut(),
        )
    };
    if needed == 0 {
        return None;
    }
    let mut storage = vec![0u64; needed.div_ceil(8)];
    let list = storage.as_mut_ptr() as *mut AudioBufferList;
    let mut block: *mut CMBlockBuffer = ptr::null_mut();
    let status = unsafe {
        sb.audio_buffer_list_with_retained_block_buffer(
            ptr::null_mut(),
            list,
            needed,
            None,
            None,
            kCMSampleBufferFlag_AudioBufferList_Assure16ByteAlignment,
            &mut block,
        )
    };
    // Take ownership of the +1 retained block buffer so it is released on return.
    let _block: Option<CFRetained<CMBlockBuffer>> =
        NonNull::new(block).map(|b| unsafe { CFRetained::from_raw(b) });
    if status != 0 {
        return None;
    }

    let n_buffers = unsafe { (*list).mNumberBuffers } as usize;
    let first: *const AudioBuffer = unsafe { ptr::addr_of!((*list).mBuffers) }.cast();
    let buffers: Vec<&AudioBuffer> = (0..n_buffers)
        .filter_map(|i| unsafe { first.add(i).as_ref() })
        .collect();
    let read = |b: &AudioBuffer| -> Vec<f32> {
        if b.mData.is_null() {
            return Vec::new();
        }
        let data: *const c_void = b.mData;
        if is_float {
            let n = b.mDataByteSize as usize / 4;
            unsafe { std::slice::from_raw_parts(data as *const f32, n) }.to_vec()
        } else {
            let n = b.mDataByteSize as usize / 2;
            unsafe { std::slice::from_raw_parts(data as *const i16, n) }
                .iter()
                .map(|&s| s as f32 / 32768.0)
                .collect()
        }
    };

    let interleaved = if non_interleaved && buffers.len() > 1 {
        let planes: Vec<Vec<f32>> = buffers.iter().map(|b| read(b)).collect();
        let frames = planes.iter().map(Vec::len).min().unwrap_or(0);
        let ch = planes.len();
        let mut out = Vec::with_capacity(frames * ch);
        for f in 0..frames {
            for plane in &planes {
                out.push(plane[f]);
            }
        }
        return Some((
            out,
            NativeFormat {
                sample_rate: asbd.mSampleRate as u32,
                channels: ch as u16,
            },
        ));
    } else {
        buffers.first().map(|b| read(b)).unwrap_or_default()
    };
    let ch = if non_interleaved { 1 } else { channels };
    Some((
        interleaved,
        NativeFormat {
            sample_rate: asbd.mSampleRate as u32,
            channels: ch as u16,
        },
    ))
}

/// Wrapper to move Objective-C objects across the completion-handler thread boundary.
struct SendBox<T>(T);
// SAFETY: the wrapped objects are immutable snapshots / thread-safe ScreenCaptureKit objects.
unsafe impl<T> Send for SendBox<T> {}

fn wait_for_error_block(
    f: impl FnOnce(&block2::DynBlock<dyn Fn(*mut NSError)>),
) -> std::result::Result<(), CaptureError> {
    let (tx, rx) = mpsc::sync_channel::<SendBox<Option<Retained<NSError>>>>(1);
    let block = RcBlock::new(move |err: *mut NSError| {
        let err = unsafe { Retained::retain(err) };
        let _ = tx.try_send(SendBox(err));
    });
    f(&block);
    match rx.recv_timeout(Duration::from_secs(10)) {
        Ok(SendBox(None)) => Ok(()),
        Ok(SendBox(Some(e))) => Err(ns_error(&e)),
        Err(_) => Err(CaptureError::Backend {
            audio: AudioSource::System,
            detail: "ScreenCaptureKit did not respond".into(),
        }),
    }
}

pub struct ScreenCaptureKitStream {
    stream: Retained<SCStream>,
    _output: Retained<AudioOutput>,
    _queue: DispatchRetained<DispatchQueue>,
    running: bool,
}

impl ScreenCaptureKitStream {
    pub fn start(sink: RawAudioSink) -> Result<Self> {
        // 1. Shareable content (fails with "user declined" without permission).
        let (tx, rx) = mpsc::sync_channel::<
            SendBox<std::result::Result<Retained<SCShareableContent>, CaptureError>>,
        >(1);
        let block = RcBlock::new(move |content: *mut SCShareableContent, err: *mut NSError| {
            let result = match (unsafe { Retained::retain(content) }, unsafe {
                Retained::retain(err)
            }) {
                (Some(c), _) => Ok(c),
                (None, Some(e)) => Err(ns_error(&e)),
                (None, None) => Err(permission_error()),
            };
            let _ = tx.try_send(SendBox(result));
        });
        unsafe { SCShareableContent::getShareableContentWithCompletionHandler(&block) };
        let content = rx
            .recv_timeout(Duration::from_secs(10))
            .map_err(|_| CaptureError::Backend {
                audio: AudioSource::System,
                detail: "timed out listing shareable content".into(),
            })?
            .0?;

        let displays = unsafe { content.displays() };
        let display = displays.firstObject().ok_or(CaptureError::DeviceNotFound {
            audio: AudioSource::System,
        })?;

        // 2. Filter + audio-focused configuration.
        let filter = unsafe {
            SCContentFilter::initWithDisplay_excludingWindows(
                SCContentFilter::alloc(),
                &display,
                &NSArray::new(),
            )
        };
        let config = unsafe { SCStreamConfiguration::new() };
        unsafe {
            config.setCapturesAudio(true);
            config.setExcludesCurrentProcessAudio(true);
            config.setSampleRate(SAMPLE_RATE as isize);
            config.setChannelCount(CHANNELS as isize);
            config.setWidth(2);
            config.setHeight(2);
            config.setMinimumFrameInterval(CMTime {
                value: 1,
                timescale: 1,
                flags: CMTimeFlags::Valid,
                epoch: 0,
            });
            config.setQueueDepth(3);
        }

        // 3. Stream with our output/delegate on a private serial queue.
        let output = AudioOutput::new(sink);
        let queue = DispatchQueue::new("app.meetpilot.sck-audio", None);
        let stream = unsafe {
            SCStream::initWithFilter_configuration_delegate(
                SCStream::alloc(),
                &filter,
                &config,
                Some(ProtocolObject::from_ref(&*output)),
            )
        };
        let proto: &ProtocolObject<dyn SCStreamOutput> = ProtocolObject::from_ref(&*output);
        for kind in [SCStreamOutputType::Audio, SCStreamOutputType::Screen] {
            unsafe {
                stream.addStreamOutput_type_sampleHandlerQueue_error(proto, kind, Some(&queue))
            }
            .map_err(|e| ns_error(&e))?;
        }

        let mut this = Self {
            stream,
            _output: output,
            _queue: queue,
            running: false,
        };
        this.start_capture()?;
        Ok(this)
    }

    fn start_capture(&mut self) -> Result<()> {
        let stream = &self.stream;
        wait_for_error_block(|b| unsafe { stream.startCaptureWithCompletionHandler(Some(b)) })?;
        self.running = true;
        Ok(())
    }

    fn stop_capture(&mut self) -> Result<()> {
        if !self.running {
            return Ok(());
        }
        let stream = &self.stream;
        let r =
            wait_for_error_block(|b| unsafe { stream.stopCaptureWithCompletionHandler(Some(b)) });
        self.running = false;
        r
    }
}

impl CaptureStream for ScreenCaptureKitStream {
    fn native_format(&self) -> NativeFormat {
        NativeFormat {
            sample_rate: SAMPLE_RATE,
            channels: CHANNELS,
        }
    }
    fn device_name(&self) -> String {
        "System audio (ScreenCaptureKit)".into()
    }
    /// ScreenCaptureKit has no pause; stop the stream so nothing is captured while paused.
    fn pause(&mut self) -> Result<()> {
        self.stop_capture()
    }
    fn resume(&mut self) -> Result<()> {
        self.start_capture()
    }
}

impl Drop for ScreenCaptureKitStream {
    fn drop(&mut self) {
        if let Err(e) = self.stop_capture() {
            log::warn!("failed to stop ScreenCaptureKit stream: {e}");
        }
    }
}
