REQUESTED_BY: S1
TASK: Build the complete ffmpeg processor library for TimCut. Each processor is a Python function in /home/darren/Desktop/timcut/backend/processors/ that takes input path + params and returns output path. All implemented via ffmpeg/ffprobe subprocess calls. Must cover ALL Tier 1 operations:

1. processors/ffprobe.py — probe metadata (duration, resolution, codec, fps, bitrate) → JSON
2. processors/trim.py — trim video (start_time, end_time) with keyframe-accurate seeking
3. processors/concat.py — concatenate multiple clips (concat demuxer)
4. processors/convert.py — transcode between formats (mp4/webm/mov/gif) with quality presets
5. processors/resize.py — resolution scaling (preset sizes + custom) with aspect ratio handling
6. processors/framerate.py — frame rate conversion
7. processors/audio.py — extract audio, replace audio, mix audio tracks
8. processors/text_overlay.py — drawtext filter with font selection, position, size, color, animation (fade in/out)
9. processors/image_overlay.py — overlay images/watermarks with position, opacity, scaling
10. processors/color.py — color grading: LUT application (lut3d), curves, eq, hue adjustment
11. processors/speed.py — speed ramping (setpts + atempo for audio)
12. processors/crop.py — crop with position + size, zoompan for Ken Burns effect
13. processors/transitions.py — xfade filter (fade, wipe, dissolve, slide, etc.)
14. processors/gif.py — palette-based GIF pipeline (2-pass for quality)
15. processors/thumbnail.py — single-frame extraction at given timestamp

Every function must:
- Accept pathlib.Path or str for input/output
- Write output to a specified path (or generate one in /storage/temp/)
- Return a dict with {output_path, duration_ms, metadata} on success
- Raise a clear exception on failure with ffmpeg stderr
- Use subprocess.run with timeout (default 600s)
- Log the ffmpeg command being run

SCOPE: /home/darren/Desktop/timcut/backend/processors/
OUTPUT_TO: All files in processors/ directory
PRIORITY: high
CONSTRAINT: Do NOT install or run anything. Write the Python modules only. Test-ready but not tested.
AGENT_TYPE: compute
DEPENDS_ON: none
CLAIMED_BY: S1
CLAIMED_AT: 2026-02-20T23:07:19Z
COMPLETED_AT: 2026-02-20T23:18:39Z
RESULT: All files written successfully
