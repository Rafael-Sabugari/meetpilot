//! cpal-based backend.
//!
//! * Microphone on Windows (WASAPI), macOS (CoreAudio) and Linux (ALSA, dev only).
//! * System audio on Windows: WASAPI **loopback**. cpal enables `AUDCLNT_STREAMFLAGS_LOOPBACK`
//!   when an input stream is built on a render (output) endpoint. When no device is chosen
//!   the default output is used and cpal follows default-device changes.
//! * System audio on macOS 14.6+: CoreAudio **process tap** (cpal creates a private tap +
//!   aggregate device when an input stream is built on an output device). Requires the
//!   "System Audio Recording" privacy permission (`NSAudioCaptureUsageDescription`).
//!
//! For loopback we always open the output device with its *current* default format, so
//! MeetPilot never changes the user's speaker configuration.

use std::str::FromStr;

use cpal::traits::{DeviceTrait, HostTrait, StreamTrait};
use cpal::{FromSample, Sample, SampleFormat, SizedSample};

use crate::error::{CaptureError, Result};
use crate::provider::{AudioCaptureProvider, CaptureStream, RawAudioSink};
use crate::types::{
    AudioSource, DeviceDirection, DeviceInfo, NativeFormat, PermissionState, PlatformCapabilities,
    SourceSelection, SupportLevel,
};

pub struct CpalProvider {
    system_audio: SupportLevel,
}

impl CpalProvider {
    /// `system_audio` describes whether loopback through cpal is valid on this OS/version.
    pub fn new(system_audio: SupportLevel) -> Self {
        Self { system_audio }
    }

    pub fn for_current_platform() -> Self {
        if cfg!(windows) {
            Self::new(SupportLevel::Supported {
                backend: "WASAPI loopback".into(),
            })
        } else if cfg!(target_os = "linux") {
            Self::new(SupportLevel::Unsupported {
                reason:
                    "Linux is a development platform only; system-audio capture is not implemented."
                        .into(),
            })
        } else {
            Self::new(SupportLevel::Supported {
                backend: "CoreAudio process tap".into(),
            })
        }
    }

    fn host() -> cpal::Host {
        cpal::default_host()
    }

    fn find_device(source: AudioSource, selection: &SourceSelection) -> Result<cpal::Device> {
        let host = Self::host();
        let device = match &selection.device_id {
            Some(id) => {
                let parsed =
                    cpal::DeviceId::from_str(id).map_err(|e| CaptureError::InvalidConfig {
                        detail: format!("invalid device id: {e}"),
                    })?;
                host.device_by_id(&parsed)
            }
            None => match source {
                AudioSource::System => host.default_output_device(),
                AudioSource::Microphone => host.default_input_device(),
            },
        };
        let device = device.ok_or(CaptureError::DeviceNotFound { audio: source })?;
        let usable = match source {
            AudioSource::System => device.supports_output(),
            AudioSource::Microphone => device.supports_input(),
        };
        if !usable && selection.device_id.is_none() {
            return Err(CaptureError::DeviceNotFound { audio: source });
        }
        match source {
            AudioSource::System if !device.supports_output() => Err(CaptureError::InvalidConfig {
                detail: "system audio must be captured from an output (playback) device".into(),
            }),
            AudioSource::Microphone if !device.supports_input() => {
                Err(CaptureError::InvalidConfig {
                    detail: "the microphone must be an input device".into(),
                })
            }
            _ => Ok(device),
        }
    }

    fn list(direction: DeviceDirection) -> Result<Vec<DeviceInfo>> {
        let host = Self::host();
        let src = match direction {
            DeviceDirection::Input => AudioSource::Microphone,
            DeviceDirection::Output => AudioSource::System,
        };
        let default_id = match direction {
            DeviceDirection::Input => host.default_input_device(),
            DeviceDirection::Output => host.default_output_device(),
        }
        .and_then(|d| d.id().ok())
        .map(|id| id.to_string());
        let devices = match direction {
            DeviceDirection::Input => host.input_devices().map(|d| d.collect::<Vec<_>>()),
            DeviceDirection::Output => host.output_devices().map(|d| d.collect::<Vec<_>>()),
        }
        .map_err(|e| map_cpal_error(src, &e))?;
        Ok(devices
            .into_iter()
            .filter_map(|d| {
                let id = d.id().ok()?.to_string();
                let name = d
                    .description()
                    .map(|desc| desc.name().to_string())
                    .unwrap_or_else(|_| id.clone());
                Some(DeviceInfo {
                    is_default: default_id.as_deref() == Some(id.as_str()),
                    id,
                    name,
                    direction,
                })
            })
            .collect())
    }

    pub fn open_stream(
        source: AudioSource,
        selection: &SourceSelection,
        sink: RawAudioSink,
    ) -> Result<CpalStream> {
        let device = Self::find_device(source, selection)?;
        let supported = match source {
            AudioSource::System => device.default_output_config(),
            AudioSource::Microphone => device.default_input_config(),
        }
        .map_err(|e| map_cpal_error(source, &e))?;
        let format = NativeFormat {
            sample_rate: supported.sample_rate(),
            channels: supported.channels(),
        };
        let config = cpal::StreamConfig {
            channels: format.channels,
            sample_rate: format.sample_rate,
            buffer_size: cpal::BufferSize::Default,
        };
        let stream = match supported.sample_format() {
            SampleFormat::F32 => build::<f32>(&device, config, sink, format, source),
            SampleFormat::I16 => build::<i16>(&device, config, sink, format, source),
            SampleFormat::I32 => build::<i32>(&device, config, sink, format, source),
            SampleFormat::U16 => build::<u16>(&device, config, sink, format, source),
            SampleFormat::F64 => build::<f64>(&device, config, sink, format, source),
            SampleFormat::U8 => build::<u8>(&device, config, sink, format, source),
            other => {
                return Err(CaptureError::Unsupported {
                    audio: source,
                    reason: format!("sample format {other:?} is not supported"),
                })
            }
        }
        .map_err(|e| map_cpal_error(source, &e))?;
        stream.play().map_err(|e| map_cpal_error(source, &e))?;
        let name = device
            .description()
            .map(|d| d.name().to_string())
            .unwrap_or_else(|_| "Unknown device".into());
        Ok(CpalStream {
            stream,
            format,
            name,
            source,
        })
    }
}

fn build<T>(
    device: &cpal::Device,
    config: cpal::StreamConfig,
    sink: RawAudioSink,
    format: NativeFormat,
    source: AudioSource,
) -> std::result::Result<cpal::Stream, cpal::Error>
where
    T: SizedSample + Send + 'static,
    f32: FromSample<T>,
{
    let err_sink = sink.clone();
    device.build_input_stream::<T, _, _>(
        config,
        move |data: &[T], _info| sink.push_with(data, format, f32::from_sample),
        move |e| {
            match e.kind() {
                // Rerouted to the new default device by cpal; nothing to do.
                cpal::ErrorKind::DeviceChanged => log::info!("{source}: audio route changed"),
                cpal::ErrorKind::Xrun | cpal::ErrorKind::RealtimeDenied => {
                    log::debug!("{source}: {e}")
                }
                _ => err_sink.report_error(map_cpal_error(source, &e)),
            }
        },
        None,
    )
}

pub(crate) fn permission_hint(source: AudioSource) -> String {
    match (source, std::env::consts::OS) {
        (AudioSource::Microphone, "windows") => "Open Settings > Privacy & security > Microphone and allow desktop apps to access the microphone.".into(),
        (AudioSource::Microphone, "macos") => "Open System Settings > Privacy & Security > Microphone and enable MeetPilot.".into(),
        (AudioSource::System, "macos") => "Open System Settings > Privacy & Security > Screen & System Audio Recording and allow MeetPilot under System Audio Recording.".into(),
        _ => "Check the operating system privacy settings and try again.".into(),
    }
}

pub(crate) fn map_cpal_error(source: AudioSource, e: &cpal::Error) -> CaptureError {
    let detail = e.to_string();
    match e.kind() {
        cpal::ErrorKind::PermissionDenied => CaptureError::PermissionDenied {
            audio: source,
            hint: permission_hint(source),
        },
        cpal::ErrorKind::DeviceNotAvailable
        | cpal::ErrorKind::StreamInvalidated
        | cpal::ErrorKind::DeviceBusy => CaptureError::DeviceUnavailable {
            audio: source,
            detail,
        },
        cpal::ErrorKind::UnsupportedOperation | cpal::ErrorKind::UnsupportedConfig => {
            CaptureError::Unsupported {
                audio: source,
                reason: detail,
            }
        }
        _ => CaptureError::Backend {
            audio: source,
            detail,
        },
    }
}

pub struct CpalStream {
    stream: cpal::Stream,
    format: NativeFormat,
    name: String,
    source: AudioSource,
}

impl CaptureStream for CpalStream {
    fn native_format(&self) -> NativeFormat {
        self.format
    }
    fn device_name(&self) -> String {
        self.name.clone()
    }
    fn pause(&mut self) -> Result<()> {
        self.stream
            .pause()
            .map_err(|e| map_cpal_error(self.source, &e))
    }
    fn resume(&mut self) -> Result<()> {
        self.stream
            .play()
            .map_err(|e| map_cpal_error(self.source, &e))
    }
}

impl AudioCaptureProvider for CpalProvider {
    fn backend_name(&self) -> String {
        format!("cpal/{}", Self::host().id().name())
    }

    fn capabilities(&self) -> PlatformCapabilities {
        let mut notes = Vec::new();
        if cfg!(windows) {
            notes.push("System audio is captured with WASAPI loopback from the selected output device (default: follows the Windows default output).".into());
            notes.push("If your meeting app plays to a different device (e.g. a headset), select that device.".into());
            notes.push("Apps using exclusive-mode audio cannot be captured by loopback.".into());
        }
        if cfg!(target_os = "linux") {
            notes.push("Linux is used for development only. Microphone capture works via ALSA/PipeWire; system audio is not implemented.".into());
        }
        PlatformCapabilities {
            os: std::env::consts::OS.into(),
            os_version: None,
            system_audio: self.system_audio.clone(),
            microphone: SupportLevel::Supported {
                backend: format!("cpal/{}", Self::host().id().name()),
            },
            system_audio_permission: self.permission_status(AudioSource::System),
            microphone_permission: self.permission_status(AudioSource::Microphone),
            notes,
        }
    }

    fn list_input_devices(&self) -> Result<Vec<DeviceInfo>> {
        Self::list(DeviceDirection::Input)
    }

    fn list_output_devices(&self) -> Result<Vec<DeviceInfo>> {
        Self::list(DeviceDirection::Output)
    }

    fn permission_status(&self, source: AudioSource) -> PermissionState {
        match (source, std::env::consts::OS) {
            (AudioSource::System, "windows") => PermissionState::NotRequired,
            // Windows exposes the privacy toggle only through the registry/consent store;
            // we detect denial on open and show guidance instead.
            (AudioSource::Microphone, "windows") => PermissionState::Unknown,
            (_, "linux") => PermissionState::NotRequired,
            _ => PermissionState::Unknown,
        }
    }

    fn request_permission(&self, source: AudioSource) -> PermissionState {
        self.permission_status(source)
    }

    fn open(
        &self,
        source: AudioSource,
        selection: &SourceSelection,
        sink: RawAudioSink,
    ) -> Result<Box<dyn CaptureStream>> {
        if source == AudioSource::System {
            if let SupportLevel::Unsupported { reason } = &self.system_audio {
                return Err(CaptureError::Unsupported {
                    audio: source,
                    reason: reason.clone(),
                });
            }
        }
        Ok(Box::new(Self::open_stream(source, selection, sink)?))
    }
}
