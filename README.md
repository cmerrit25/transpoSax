# Sax solo → alto sax sheet music

Give it a YouTube or Spotify link and the time range of the solo. It writes alto sax sheet music
(MusicXML) that you can open in MuseScore, Finale, Sibelius, Dorico, etc.

It also accepts a local audio file, or existing notation written for any sax.

## Setup

```sh
python -m venv .venv
.venv\Scripts\activate        # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
pip install demucs            # optional: needed for --isolate (large download, includes PyTorch)
```

ffmpeg is bundled through `imageio-ffmpeg`, so you don't need to install it separately.

## Usage

```sh
# YouTube: solo runs from 1:32 to 2:05
python main.py "https://www.youtube.com/watch?v=VIDEO_ID" --start 1:32 --end 2:05

# Spotify, with the sax separated from the band first
python main.py "https://open.spotify.com/track/TRACK_ID" --start 2:10 --end 2:40 --isolate

# Swing tune: snap to eighth notes, and check the result by ear
python main.py "https://youtu.be/VIDEO_ID" --start 0:58 --end 1:30 --grid 8 --midi

# Local audio file
python main.py solo.mp3 --start 0:20 --end 0:45

# Existing notation, e.g. a tenor part
python main.py solo.musicxml --from tenor
```

| Option | What it does |
|---|---|
| `--start`, `--end` | The solo's time range (`92`, `1:32`, or `1:01:32`) |
| `--isolate` | Separates the sax from the rhythm section with Demucs before transcribing. **Use this whenever other instruments play under the solo.** Expect 1–3 minutes per clip on CPU. |
| `--bpm 168` | Sets the tempo if auto-detection is off (half or double the true tempo is common) |
| `--grid 8\|16\|triplet` | Shortest rhythm to snap to. The default is 16ths; `8` reads better for swung eighth-note lines |
| `--midi` | Also writes a concert-pitch `.mid` file of the transcription so you can listen and check it |
| `--keep-audio DIR` | Keeps the trimmed (and isolated) clip |
| `--octave N` | Forces an octave shift instead of auto-fitting to alto range |
| `--pdf` | Also renders a PDF, if MuseScore is installed |
| `-o out.musicxml` | Output path (default for links: `solo_alto.musicxml`) |

## How it works

1. **Get the audio.** YouTube links are downloaded with yt-dlp. Spotify audio is DRM-protected and
   can't be downloaded, so spotdl looks up the same recording on YouTube instead. Check that the
   match is the version you meant (run with `-v` to see the YouTube URL it chose).
2. **Trim** the audio to `--start`/`--end`.
3. **Isolate** the sax (`--isolate`). Demucs's 6-stem model puts horns in its "other" stem.
4. **Transcribe** the notes. pYIN pitch tracking follows the melody, corrects for recordings that
   aren't tuned to A=440, smooths out vibrato, and splits repeated notes at their attacks. Note
   times are then snapped to a beat grid using the detected tempo.
5. **Transpose** from concert pitch to alto written pitch (up a major 6th). The octave is chosen
   so the line fits the alto's range (B♭3–F♯6 written), the key signature is detected, and keys
   with 7 sharps or flats are respelled.

## Expect to tidy up the result

Automatic transcription gives you a solid first draft, not a finished chart:

- **Barlines may be shifted.** The tool finds the beat, but it doesn't know where beat 1 is.
  Pickups and downbeats may need moving.
- **Very fast runs, ghost notes, growls, and bends** may come out as wrong or missing notes.
- **A busy band will confuse the pitch tracker** if you skip `--isolate`.
- **Check by ear** with `--midi`, and fix things in your notation program.

## Notation input

| Written for | Interval to alto written pitch |
|---|---|
| Tenor (B♭)    | down a perfect 4th (often becomes up a 5th after octave fitting) |
| Soprano (B♭)  | up a perfect 5th |
| Baritone (E♭) | same note names, an octave lower (usually moved back up by octave fitting) |
| Concert pitch | up a major 6th |

A PDF or photo of sheet music has to be converted to MusicXML first, with MuseScore's PDF import or
Audiveris.

## Note on sources

Downloading from YouTube may go against YouTube's Terms of Service. Use this for personal
practice and study, and only with recordings you have the right to use.

## Tests

```sh
python -m pytest -q
```
