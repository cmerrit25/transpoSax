from music21 import converter, harmony, instrument, key, meter, note, stream

from main import detect_source, main, transpose_to_alto


def make_part(names, ks=None, inst=None):
    p = stream.Part()
    if inst:
        p.insert(0, inst)
    if ks is not None:
        p.append(key.KeySignature(ks))
    p.append(meter.TimeSignature("4/4"))
    for n in names:
        p.append(note.Note(n, quarterLength=1))
    return p


def names(score):
    return [n.pitch.nameWithOctave for n in score.recurse().getElementsByClass(note.Note)]


def first_key(score):
    return next(iter(score.recurse().getElementsByClass(key.KeySignature))).sharps


def test_tenor_to_alto_is_up_a_fifth_in_range():
    # Tenor written C4 sounds Bb2; alto written for Bb2 is G3 (out of range) -> G4 after auto-octave
    res = transpose_to_alto(make_part(["C4", "D4", "E4", "B4"]), "tenor")
    assert names(res.score) == ["G4", "A4", "B4", "F#5"]
    assert res.octave_shift == 1
    assert res.out_of_range == []


def test_tenor_to_alto_no_octave_shift_is_down_a_fourth():
    res = transpose_to_alto(make_part(["C5", "E5"]), "tenor", octave=0)
    assert names(res.score) == ["G4", "B4"]


def test_concert_to_alto_up_major_sixth():
    res = transpose_to_alto(make_part(["C4", "B-3"]), "concert", octave=0)
    assert names(res.score) == ["A4", "G4"]


def test_soprano_and_baritone():
    # Soprano written D5 sounds C5 -> alto A5
    assert names(transpose_to_alto(make_part(["D5"]), "soprano", octave=0).score) == ["A5"]
    # Bari is an octave below alto: same written notes
    assert names(transpose_to_alto(make_part(["G4"]), "baritone").score) == ["G4"]


def test_alto_source_unchanged():
    assert names(transpose_to_alto(make_part(["C4", "F#5"]), "alto").score) == ["C4", "F#5"]


def test_key_signature_transposed():
    # Tenor in Bb (2 flats) -> alto F (1 flat)
    res = transpose_to_alto(make_part(["B-4"], ks=-2), "tenor")
    assert first_key(res.score) == -1


def test_awkward_key_respelled():
    # Tenor in F# (6 sharps) -> alto would be C# (7 sharps) -> respelled Db (5 flats)
    res = transpose_to_alto(make_part(["F#4", "C#5"], ks=6), "tenor")
    assert first_key(res.score) == -5
    assert names(res.score) == ["D-4", "A-4"]


def test_out_of_range_reported():
    # Concert C3 -> alto A3, impossible to fit together with a very high note
    res = transpose_to_alto(make_part(["C3", "C6"]), "concert", octave=0)
    assert res.out_of_range  # A3 is below low Bb


def test_instrument_replaced_and_detected():
    part = make_part(["C4"], inst=instrument.TenorSaxophone())
    assert detect_source(part) == "tenor"
    res = transpose_to_alto(part, "tenor")
    insts = list(res.score.recurse().getElementsByClass(instrument.Instrument))
    assert len(insts) == 1 and isinstance(insts[0], instrument.AltoSaxophone)


def test_chord_symbols_transposed():
    p = make_part(["C4"])
    p.insert(0, harmony.ChordSymbol("Cmaj7"))
    res = transpose_to_alto(p, "tenor")
    cs = next(iter(res.score.recurse().getElementsByClass(harmony.ChordSymbol)))
    assert cs.root().name == "G"


def test_cli_round_trip(tmp_path):
    src = tmp_path / "solo.musicxml"
    s = stream.Score()
    s.insert(0, make_part(["C4", "D4", "E4", "F4"], ks=0, inst=instrument.TenorSaxophone()))
    s.write("musicxml", fp=str(src))

    assert main([str(src)]) == 0  # source detected from file
    out = tmp_path / "solo_alto.musicxml"
    assert out.exists()
    back = converter.parse(str(out))
    assert names(back) == ["G4", "A4", "B4", "C5"]
    assert first_key(back) == 1
