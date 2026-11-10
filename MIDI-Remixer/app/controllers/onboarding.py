from __future__ import annotations

"""Pure onboarding state rules, separated from Qt widget rendering."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class OnboardingReadiness:
    fluidsynth_ready: bool
    soundfont_ready: bool
    library_loading: bool
    library_refreshing: bool
    library_ready: bool
    library_setup_pending: bool
    mode_confirmed: bool

    def block_reason(self, *, require_mode: bool = True) -> str | None:
        if not self.fluidsynth_ready:
            return "Locate or install FluidSynth first."
        if not self.soundfont_ready:
            return "Choose a SoundFont before selecting a remix mode."
        if self.library_loading:
            return "Wait for the saved MIDI Library to finish loading."
        if self.library_refreshing:
            return "Wait for the committed MIDI Library state to finish refreshing."
        if not self.library_ready:
            return "Open MIDI Library Manager and establish a usable MIDI Library."
        if self.library_setup_pending:
            return "Finish Library analysis and eligibility setup in MIDI Library Manager."
        if require_mode and not self.mode_confirmed:
            return "Choose Guided Remix, Version Fusion, Assisted Studio, or Custom / Advanced."
        return None

    @property
    def setup_complete(self) -> bool:
        return self.fluidsynth_ready and self.soundfont_ready and self.library_ready

    @property
    def mode_unlocked(self) -> bool:
        return self.block_reason(require_mode=False) is None
