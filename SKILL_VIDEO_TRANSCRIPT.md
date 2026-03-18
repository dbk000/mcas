# Skill: MP4 Video to Actionable Bug List

Parse a screen recording where the user points at the live UI and describes bugs.

## Prerequisites

```bash
which ffmpeg ffprobe  # system package, pre-installed

# Whisper venv (one-time setup)
python3 -m venv /tmp/whisper_env
/tmp/whisper_env/bin/pip install openai-whisper
```

## Step 1: Probe

```bash
ffprobe -v quiet -print_format json -show_format -show_streams VIDEO.mp4 | python3 -c "
import json, sys
data = json.load(sys.stdin)
fmt = data.get('format', {})
print(f'Duration: {float(fmt.get(\"duration\", 0)):.1f}s')
for s in data.get('streams', []):
    if s['codec_type'] == 'video':
        print(f'Video: {s[\"width\"]}x{s[\"height\"]} @ {s.get(\"r_frame_rate\")} fps')
    elif s['codec_type'] == 'audio':
        print(f'Audio: {s[\"codec_name\"]} {s.get(\"sample_rate\")}Hz')
"
```

## Step 2: Extract frames + audio

Create the output directory first as its own command. Do NOT chain `mkdir` with
`ffmpeg` via `&&` using shell variable expansion — the `$OUTDIR` variable silently
evaluates to empty in the Bash tool, causing `mkdir ""` to fail and the whole
chain to abort.

```bash
# Command 1: create directory
mkdir -p /home/darren/Documents/MCAS/scripts/output/video_debug_N

# Command 2: extract frames (1 per second)
ffmpeg -y -i VIDEO.mp4 -vf "fps=1" -q:v 2 /home/darren/Documents/MCAS/scripts/output/video_debug_N/frame_%04d.jpg

# Command 3: extract audio (16kHz mono WAV for Whisper)
ffmpeg -y -i VIDEO.mp4 -vn -acodec pcm_s16le -ar 16000 -ac 1 /home/darren/Documents/MCAS/scripts/output/video_debug_N/audio.wav
```

Commands 2 and 3 can run in parallel (separate Bash calls in one message).

**IMPORTANT:** Use literal paths everywhere. No shell variables (`$OUTDIR`).
The Bash tool does not reliably expand variables set in the same command.

## Step 3: Transcribe with Whisper

Use `tiny` model. Do NOT use `base` — it hangs on CPU for minutes on files
longer than 2 minutes.

```bash
/tmp/whisper_env/bin/python3 -c "
import whisper, json
model = whisper.load_model('tiny')
result = model.transcribe('/path/to/audio.wav', language='en')
with open('/path/to/audio.json', 'w') as f:
    json.dump(result, f, indent=2)
for seg in result['segments']:
    print(f'[{seg[\"start\"]:.1f}s - {seg[\"end\"]:.1f}s] {seg[\"text\"]}')
"
```

Expect ~1-2 minutes for a 3-minute file on CPU. The `tiny` model occasionally
garbles proper nouns (e.g. "Market Cognition" → "Marja Cognitions"). Use the
visual frames to disambiguate.

## Step 4: View frames

Frames are JPEGs numbered by second. Read them in batches of 8 using the
Read tool. Start with every 5th frame to get the visual timeline, then fill
gaps around moments of interest from the transcript.

```
# Timestamp → frame mapping (fps=1):
# Transcript at 15.0s → frame_0015.jpg
# Transcript at 120.0s → frame_0120.jpg
```

## Step 5: Produce bug list

For each transcript segment:

1. **What was said** — paraphrase (fix Whisper garbles using visual context)
2. **What's visible** — describe the screen state from the matching frame
3. **Likely file(s)** — map to the frontend codebase
4. **Fix** — exact code change, or "needs input" if ambiguous

Group by file at the end so edits can be batched.

## Output format

```markdown
# Bug N: Short title
- **What was said** (Ns): paraphrase
- **Screen state**: page, component, what's visible
- **File(s)**: path:line
- **Fix**: exact change

## Grouped by file
### path/to/file.jsx (Bugs N, M)
- line X: change A → B
- line Y: change C → D
```
