from __future__ import annotations

from pathlib import Path
import mido

# Instrument menu: family-guided, with a useful General MIDI subset.
# Program numbers are zero-based General MIDI program numbers.
LOADED_FAMILY_LABEL = "Loaded instruments"
LOADED_INSTRUMENT_LABEL = "Keep loaded track instruments"

INSTRUMENT_MENU: dict[str, list[tuple[str, int | None]]] = {
    LOADED_FAMILY_LABEL: [(LOADED_INSTRUMENT_LABEL, None)],
    "Keyboard": [
        ("Acoustic Grand Piano", 0),
        ("Bright Piano", 1),
        ("Upright Piano", 0),
        ("Honky-Tonk Piano", 3),
        ("Electric Piano", 4),
        ("Harpsichord", 6),
        ("Music Box", 10),
    ],
    "Plucked / Guitar": [
        ("Nylon Guitar", 24),
        ("Steel Guitar", 25),
        ("Jazz Guitar", 26),
        ("Clean Electric Guitar", 27),
        ("Harp", 46),
        ("Banjo", 105),
    ],
    "Strings": [
        ("Violin", 40),
        ("Cello", 42),
        ("Contrabass", 43),
        ("Pizzicato Strings", 45),
        ("String Ensemble", 48),
        ("Slow Strings", 49),
    ],
    "Winds / Brass": [
        ("Flute", 73),
        ("Clarinet", 71),
        ("Oboe", 68),
        ("Bassoon", 70),
        ("Trumpet", 56),
        ("Trombone", 57),
        ("French Horn", 60),
    ],
    "Ensemble / Band": [
        ("Choir Aahs", 52),
        ("Orchestra Hit", 55),
        ("Brass Section", 61),
        ("Synth Brass", 62),
        ("Reed Organ", 20),
        ("Accordion", 21),
    ],
}


def instrument_names_for_family(family: str) -> list[str]:
    family = _normalize_family(family)
    return [name for name, _program in INSTRUMENT_MENU.get(family, INSTRUMENT_MENU[LOADED_FAMILY_LABEL])]


def _normalize_family(family: str) -> str:
    if family in {"Original MIDI", "Use loaded instruments", "Loaded MIDI"}:
        return LOADED_FAMILY_LABEL
    return family or LOADED_FAMILY_LABEL


def program_for_choice(family: str, instrument_name: str) -> int | None:
    family = _normalize_family(family)
    if instrument_name in {"Original MIDI", "Use loaded instruments", LOADED_INSTRUMENT_LABEL}:
        return None
    for name, program in INSTRUMENT_MENU.get(family, []):
        if name == instrument_name:
            return program
    return None


def apply_global_instrument_override(source_midi: str | Path, output_midi: str | Path, program: int | None) -> Path:
    """Write a MIDI copy with a global GM program override.

    The MVP override is deliberately global and simple: it applies to all melodic channels
    while leaving General MIDI percussion channel 10 (zero-based channel 9) alone.
    Existing program changes on melodic channels are removed so the override stays audible.
    """
    target = Path(output_midi)
    target.parent.mkdir(parents=True, exist_ok=True)

    if program is None:
        # Preserve the original file exactly when no override is requested.
        source = Path(source_midi)
        if source.resolve() != target.resolve():
            target.write_bytes(source.read_bytes())
        return target

    midi = mido.MidiFile(source_midi)
    out = mido.MidiFile(type=midi.type, ticks_per_beat=midi.ticks_per_beat, charset=getattr(midi, "charset", "latin1"))

    used_channels: set[int] = set()
    for track in midi.tracks:
        for msg in track:
            if hasattr(msg, "channel") and getattr(msg, "type", "") in {"note_on", "note_off"}:
                channel = int(msg.channel)
                if channel != 9:
                    used_channels.add(channel)

    # If the MIDI has no note channels yet, default to channel 0.
    if not used_channels:
        used_channels.add(0)

    for track_index, track in enumerate(midi.tracks):
        new_track = mido.MidiTrack()
        inserted = False
        pending_time = 0
        for msg in track:
            if msg.type == "program_change" and getattr(msg, "channel", 0) != 9:
                # Remove melodic program changes, but preserve their delta time by carrying it forward.
                pending_time += msg.time
                continue
            new_msg = msg.copy(time=msg.time + pending_time)
            pending_time = 0
            if not inserted and track_index == 0:
                # Insert program changes before the first real event in track 0.
                for channel in sorted(used_channels):
                    new_track.append(mido.Message("program_change", channel=channel, program=int(program), time=0))
                inserted = True
            new_track.append(new_msg)
        if pending_time and len(new_track):
            new_track[-1].time += pending_time
        if track_index == 0 and not inserted:
            for channel in sorted(used_channels):
                new_track.append(mido.Message("program_change", channel=channel, program=int(program), time=0))
        out.tracks.append(new_track)

    out.save(target)
    return target

GM_PROGRAM_NAMES: list[str] = [
    "Acoustic Grand Piano", "Bright Acoustic Piano", "Electric Grand Piano", "Honky-tonk Piano",
    "Electric Piano 1", "Electric Piano 2", "Harpsichord", "Clavinet", "Celesta", "Glockenspiel",
    "Music Box", "Vibraphone", "Marimba", "Xylophone", "Tubular Bells", "Dulcimer",
    "Drawbar Organ", "Percussive Organ", "Rock Organ", "Church Organ", "Reed Organ", "Accordion",
    "Harmonica", "Tango Accordion", "Nylon Guitar", "Steel Guitar", "Jazz Guitar", "Clean Electric Guitar",
    "Muted Electric Guitar", "Overdriven Guitar", "Distortion Guitar", "Guitar Harmonics", "Acoustic Bass",
    "Fingered Bass", "Picked Bass", "Fretless Bass", "Slap Bass 1", "Slap Bass 2", "Synth Bass 1", "Synth Bass 2",
    "Violin", "Viola", "Cello", "Contrabass", "Tremolo Strings", "Pizzicato Strings", "Orchestral Harp", "Timpani",
    "String Ensemble 1", "String Ensemble 2", "Synth Strings 1", "Synth Strings 2", "Choir Aahs", "Voice Oohs",
    "Synth Voice", "Orchestra Hit", "Trumpet", "Trombone", "Tuba", "Muted Trumpet", "French Horn", "Brass Section",
    "Synth Brass 1", "Synth Brass 2", "Soprano Sax", "Alto Sax", "Tenor Sax", "Baritone Sax", "Oboe", "English Horn",
    "Bassoon", "Clarinet", "Piccolo", "Flute", "Recorder", "Pan Flute", "Blown Bottle", "Shakuhachi", "Whistle",
    "Ocarina", "Lead 1 Square", "Lead 2 Sawtooth", "Lead 3 Calliope", "Lead 4 Chiff", "Lead 5 Charang",
    "Lead 6 Voice", "Lead 7 Fifths", "Lead 8 Bass + Lead", "Pad 1 New Age", "Pad 2 Warm", "Pad 3 Polysynth",
    "Pad 4 Choir", "Pad 5 Bowed", "Pad 6 Metallic", "Pad 7 Halo", "Pad 8 Sweep", "FX 1 Rain", "FX 2 Soundtrack",
    "FX 3 Crystal", "FX 4 Atmosphere", "FX 5 Brightness", "FX 6 Goblins", "FX 7 Echoes", "FX 8 Sci-fi",
    "Sitar", "Banjo", "Shamisen", "Koto", "Kalimba", "Bag Pipe", "Fiddle", "Shanai", "Tinkle Bell", "Agogo",
    "Steel Drums", "Woodblock", "Taiko Drum", "Melodic Tom", "Synth Drum", "Reverse Cymbal", "Guitar Fret Noise",
    "Breath Noise", "Seashore", "Bird Tweet", "Telephone Ring", "Helicopter", "Applause", "Gunshot",
]


def gm_choice_names(include_original: bool = True) -> list[str]:
    names = [f"{i + 1:03d} · {name}" for i, name in enumerate(GM_PROGRAM_NAMES)]
    return (["Original MIDI"] + names) if include_original else names


def program_for_gm_choice(choice: str) -> int | None:
    if not choice or choice == "Original MIDI":
        return None
    try:
        return int(choice.split("·", 1)[0].strip()) - 1
    except Exception:
        return None


def gm_choice_for_program(program: int | None) -> str:
    if program is None or not (0 <= int(program) < len(GM_PROGRAM_NAMES)):
        return "Original MIDI"
    return f"{int(program) + 1:03d} · {GM_PROGRAM_NAMES[int(program)]}"
