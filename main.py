"""
Turn a saxophone solo into alto saxophone sheet music.

Input can be:
  * a YouTube or Spotify link, with --start/--end for the solo's time range
  * a local audio file (.mp3, .wav, .m4a, ...)
  * existing notation (MusicXML / .mxl, MIDI, ABC, ...) for any sax or concert pitch

Audio is transcribed (pitch tracking + beat quantization) at concert pitch and
then transposed to alto sax written pitch. Output is MusicXML, plus optional
PDF (MuseScore) and a concert-pitch MIDI for checking the transcription by ear.

Examples:
    python main.py "https://www.youtube.com/watch?v=..." --start 1:32 --end 2:05
    python main.py "https://open.spotify.com/track/..." --start 2:10 --end 2:40 --isolate
    python main.py solo.musicxml --from tenor
"""

from __future__ import annotations

import argparse
import logging
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from music21 import converter, instrument, interval, key, pitch, stream

# How far each source pitch sits *above* concert (sounding) pitch when written.
# Writing for an instrument = concert pitch transposed UP by this interval.
WRITTEN_ABOVE_CONCERT = {
    "concert": "P1",
    "soprano": "M2",    # Bb soprano sounds a major 2nd below written
    "alto": "M6",       # Eb alto sounds a major 6th below written
    "tenor": "M9",      # Bb tenor sounds a major 9th below written
    "baritone": "M13",  # Eb baritone sounds an octave + major 6th below written
}

# Standard written range of the alto sax (low Bb to high F#; F#6 is on most modern horns).
ALTO_LOW = pitch.Pitch("B-3")
ALTO_HIGH = pitch.Pitch("F#6")

# music21 instrument classes -> source names, used to guess --from when not given.
_INSTRUMENT_TO_SOURCE = {
    "SopranoSaxophone": "soprano",
    "AltoSaxophone": "alto",
    "TenorSaxophone": "tenor",
    "BaritoneSaxophone": "baritone",
}


@dataclass
class TransposeResult:
    score: stream.Score
    source: str
    octave_shift: int
    out_of_range: list[str] = field(default_factory=list)


def detect_source(score: stream.Stream) -> str | None:
    """Guess the source saxophone from the instrument stored in the file, if any."""
    for inst in score.recurse().getElementsByClass(instrument.Instrument):
        for cls in type(inst).__mro__:
            if cls.__name__ in _INSTRUMENT_TO_SOURCE:
                return _INSTRUMENT_TO_SOURCE[cls.__name__]
    return None


def _all_pitches(s: stream.Stream) -> list[pitch.Pitch]:
    return [p for n in s.recurse().notes if not _is_chord_symbol(n) for p in n.pitches]


def _is_chord_symbol(el) -> bool:
    return "ChordSymbol" in el.classes


def _count_out_of_range(pitches: list[pitch.Pitch], semitone_shift: int) -> int:
    return sum(
        1 for p in pitches
        if not (ALTO_LOW.ps <= p.ps + semitone_shift <= ALTO_HIGH.ps)
    )


def _best_octave_shift(s: stream.Stream) -> int:
    """Pick the octave shift (-2..+2) that puts the most notes in alto range.

    Ties go to the smallest shift, then to shifting up (alto is the higher horn).
    """
    pitches = _all_pitches(s)
    if not pitches:
        return 0
    candidates = sorted(range(-2, 3), key=lambda o: (abs(o), -o))
    return min(candidates, key=lambda o: _count_out_of_range(pitches, 12 * o))


def _simplify_key_signatures(s: stream.Stream) -> stream.Stream:
    """Respell enharmonically if the result lands in a key with 7+ sharps/flats
    (e.g. C# major -> Db major)."""
    keys = list(s.recurse().getElementsByClass(key.KeySignature))
    if not keys:
        return s
    sharps = keys[0].sharps
    if sharps >= 7:
        return s.transpose("d2")    # C# -> Db, G# -> Ab, ...
    if sharps <= -7:
        return s.transpose("-d2")   # Cb -> B, Fb -> E, ...
    return s


def _set_alto_instrument(score: stream.Score) -> None:
    parts = list(score.parts) or [score]
    for part in parts:
        for inst in list(part.recurse().getElementsByClass(instrument.Instrument)):
            inst.activeSite.remove(inst)
        alto = instrument.AltoSaxophone()
        part.insert(0, alto)
        part.partName = "Alto Saxophone"
        part.partAbbreviation = "A. Sax."


def transpose_to_alto(
    score: stream.Stream,
    source: str,
    octave: int | None = None,
) -> TransposeResult:
    """Return a new score in alto sax written pitch.

    Args:
        score: the parsed solo, in the written pitch of `source`.
        source: one of WRITTEN_ABOVE_CONCERT's keys.
        octave: extra octave shift; None = choose automatically to fit alto range.
    """
    if source not in WRITTEN_ABOVE_CONCERT:
        raise ValueError(f"Unknown source {source!r}; choose from {sorted(WRITTEN_ABOVE_CONCERT)}")

    # source written -> concert -> alto written
    to_concert = interval.Interval(WRITTEN_ABOVE_CONCERT[source]).reverse()
    to_alto = interval.Interval(WRITTEN_ABOVE_CONCERT["alto"])
    result = score.transpose(to_concert).transpose(to_alto)

    if octave is None:
        octave = _best_octave_shift(result)
    if octave:
        result = result.transpose(interval.Interval(12 * octave))  # P8 / -P8, same spelling

    result = _simplify_key_signatures(result)

    if not isinstance(result, stream.Score):
        wrapper = stream.Score()
        wrapper.insert(0, result)
        result = wrapper
    _set_alto_instrument(result)
    result.atSoundingPitch = False

    out_of_range = []
    for n in result.recurse().notes:
        if _is_chord_symbol(n):
            continue
        for p in n.pitches:
            if not (ALTO_LOW.ps <= p.ps <= ALTO_HIGH.ps):
                out_of_range.append(f"measure {n.measureNumber}: {p.nameWithOctave}")

    return TransposeResult(result, source, octave, out_of_range)


def find_musescore() -> str | None:
    for name in ("MuseScore4", "MuseScore3", "mscore", "musescore", "mscore4portable"):
        found = shutil.which(name)
        if found:
            return found
    for candidate in (
        r"C:\Program Files\MuseScore 4\bin\MuseScore4.exe",
        r"C:\Program Files\MuseScore 3\bin\MuseScore3.exe",
        "/Applications/MuseScore 4.app/Contents/MacOS/mscore",
    ):
        if Path(candidate).exists():
            return candidate
    return None


def render_pdf(musicxml_path: Path, pdf_path: Path) -> None:
    exe = find_musescore()
    if exe is None:
        raise RuntimeError("MuseScore not found. Install it from musescore.org, or open the "
                           ".musicxml file in any notation program and export a PDF.")
    proc = subprocess.run([exe, str(musicxml_path), "-o", str(pdf_path)],
                          capture_output=True, text=True)
    if proc.returncode != 0 or not pdf_path.exists():
        raise RuntimeError(f"MuseScore failed to export PDF (exit {proc.returncode}):\n{proc.stderr}")


def _transcribe_audio(args, workdir: Path) -> stream.Score:
    """URL / audio file -> concert-pitch score."""
    from audio_source import get_clip, isolate_sax, parse_time
    from transcribe import transcribe

    start, end = parse_time(args.start), parse_time(args.end)
    print("Fetching audio..." if args.input.startswith("http") else "Loading audio...")
    clip = get_clip(args.input, workdir, start, end)

    melody = clip
    if args.isolate:
        print("Separating the sax from the band (Demucs; can take a minute)...")
        melody = isolate_sax(clip, workdir)

    print("Transcribing...")
    score, bpm = transcribe(melody, bpm=args.bpm, grid=args.grid, beat_source=clip)
    print(f"Tempo: {bpm:.0f} BPM")

    if args.keep_audio:
        keep = Path(args.keep_audio)
        keep.mkdir(parents=True, exist_ok=True)
        shutil.copy(clip, keep / "clip.wav")
        if melody != clip:
            shutil.copy(melody, keep / "clip_isolated.wav")
        print(f"Saved audio to {keep}")
    return score


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Turn a sax solo (YouTube/Spotify link, audio file, or notation) "
                    "into alto sax sheet music.")
    ap.add_argument("input", help="YouTube/Spotify URL, audio file, or notation file "
                                  "(.musicxml, .mxl, .mid, .abc, ...)")
    ap.add_argument("-o", "--output", type=Path,
                    help="Output .musicxml path (default: <input>_alto.musicxml, "
                         "or solo_alto.musicxml for links)")
    ap.add_argument("--octave", type=int, choices=range(-2, 3), default=None,
                    help="Force an octave shift instead of auto-fitting to alto range")
    ap.add_argument("--pdf", action="store_true", help="Also render a PDF with MuseScore")
    ap.add_argument("-v", "--verbose", action="store_true")

    audio = ap.add_argument_group("audio / link input")
    audio.add_argument("--start", help="Start of the solo, e.g. 1:32 or 92")
    audio.add_argument("--end", help="End of the solo, e.g. 2:05")
    audio.add_argument("--isolate", action="store_true",
                       help="Separate the sax from the band with Demucs first "
                            "(recommended when other instruments play under the solo)")
    audio.add_argument("--bpm", type=float, help="Tempo, if auto-detection gets it wrong")
    audio.add_argument("--grid", choices=["8", "16", "triplet"], default="16",
                       help="Shortest rhythm to snap to (default 16 = sixteenths; "
                            "use 8 for swung eighth-note lines)")
    audio.add_argument("--midi", action="store_true",
                       help="Also write a concert-pitch MIDI of the transcription to check by ear")
    audio.add_argument("--keep-audio", metavar="DIR",
                       help="Keep the trimmed (and isolated) audio clip in DIR")

    notation = ap.add_argument_group("notation input")
    notation.add_argument("--from", dest="source", choices=sorted(WRITTEN_ABOVE_CONCERT),
                          help="What the notation is written for. If omitted, it is read from "
                               "the file's instrument; MIDI defaults to concert pitch.")
    args = ap.parse_args(argv)

    sys.stdout.reconfigure(line_buffering=True)  # keep progress lines in order with stderr
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")

    from audio_source import AUDIO_EXTS, is_url

    in_path = Path(args.input)
    is_audio = (is_url(args.input) or args.input.startswith("spotify:")
                or in_path.suffix.lower() in AUDIO_EXTS)

    if is_audio:
        with tempfile.TemporaryDirectory(prefix="sax2alto_") as tmp:
            try:
                score = _transcribe_audio(args, Path(tmp))
            except Exception as e:
                logging.getLogger(__name__).info("audio pipeline failed", exc_info=True)
                print(f"Error: {e}" + ("" if args.verbose else "  (run with -v for details)"),
                      file=sys.stderr)
                return 1
        if not list(score.recurse().notes):
            print("Error: no notes detected in that time range. Check --start/--end, "
                  "or try --isolate.", file=sys.stderr)
            return 1
        source = "concert"
        out = args.output or (Path("solo_alto.musicxml") if is_url(args.input)
                              else in_path.with_name(f"{in_path.stem}_alto.musicxml"))
        if args.midi:
            mid = out.with_name(out.stem.replace("_alto", "") + "_concert.mid")
            score.write("midi", fp=str(mid))
            print(f"Wrote {mid} (concert pitch, for checking by ear)")
    else:
        if not in_path.exists():
            ap.error(f"File not found: {in_path}")
        score = converter.parse(str(in_path))
        source = args.source or detect_source(score)
        if source is None and in_path.suffix.lower() in (".mid", ".midi"):
            source = "concert"
        if source is None:
            ap.error("Couldn't tell what instrument the solo is written for; pass --from "
                     "(tenor, alto, soprano, baritone or concert).")
        out = args.output or in_path.with_name(f"{in_path.stem}_alto.musicxml")

    res = transpose_to_alto(score, source, args.octave)
    res.score.write("musicxml", fp=str(out))

    print(f"Source: {source} sax" if source != "concert" else "Source: concert pitch")
    if res.octave_shift:
        print(f"Shifted {abs(res.octave_shift)} octave(s) "
              f"{'up' if res.octave_shift > 0 else 'down'} to fit alto range")
    print(f"Wrote {out}")

    if res.out_of_range:
        print(f"\nWarning:{len(res.out_of_range)} note(s) outside alto range "
              f"({ALTO_LOW.nameWithOctave}-{ALTO_HIGH.nameWithOctave} written):", file=sys.stderr)
        for line in res.out_of_range[:20]:
            print(f"  {line}", file=sys.stderr)
        if len(res.out_of_range) > 20:
            print(f"  ... and {len(res.out_of_range) - 20} more", file=sys.stderr)

    if args.pdf:
        pdf = out.with_suffix(".pdf")
        try:
            render_pdf(out, pdf)
        except RuntimeError as e:
            print(f"PDF not created: {e}", file=sys.stderr)
            return 1
        print(f"Wrote {pdf}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
