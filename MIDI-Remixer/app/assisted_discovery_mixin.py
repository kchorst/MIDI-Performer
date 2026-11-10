from __future__ import annotations

"""Anchor discovery, short audition previews, and saved-idea comparison."""

from pathlib import Path
from itertools import combinations
from typing import TYPE_CHECKING
import time
import weakref

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QFrame, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QMessageBox,
    QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from core.assisted_studio import (
    AssistedDiscoveryResult,
    AssistedSetPlan,
    ASSISTED_GROUP_ADAPTABLE, ASSISTED_GROUP_BROADER, ASSISTED_GROUP_GUIDED,
    ASSISTED_GROUP_MULTI, ASSISTED_GROUP_SECTION,
    assisted_anchor_recommendations, assisted_plan_group,
    assisted_library_discovery, version_fusion_plan_for_pair,
    assisted_library_summary, assisted_source_gate,
    assisted_score_label,
)
from core.performance_trace import record_performance
from core.song_identity import arrangement_distance, fusion_difference_kind, mash_pair_is_too_similar, recommendation_family_key
from library_engine.prepared_plan import assisted_plan_from_snapshot
from core.remix_discovery import (
    discovery_idea_payload,
    discovery_plan_id,
    generate_assisted_preview,
)

if TYPE_CHECKING:
    from core.remix_engine import RemixTake


class AssistedWizardDiscoveryMixin:
    """Feature mixin kept separate from plan generation and transport logic."""

    _discovery_operation_name: str
    _preview_operation_name: str

    def _initialize_discovery(self, initial_anchor_path: str | None = None) -> None:
        self.discovery_result = AssistedDiscoveryResult(None, (), (), 0)
        self._initial_anchor_path = str(initial_anchor_path or "")
        self._discovery_operation_name = f"assisted-discovery-{id(self)}"
        self._preview_operation_name = f"assisted-preview-{id(self)}"
        self._preview_buttons: list[QPushButton] = []
        self._closing_discovery = False
        self._discovery_cancel_requested = False
        # Assisted discovery results are session-local and reusable. Back/Forward
        # navigation must never repeat an expensive 96k-library search unless
        # the anchor/library inputs actually changed or the user explicitly asks
        # for fresh ideas. Store the unfiltered result so Ignore/Restore can be
        # reapplied without rescanning.
        self._cached_discovery_key: tuple[str, int, int] | None = None
        self._cached_discovery_result: AssistedDiscoveryResult | None = None
        self._displayed_discovery_result: AssistedDiscoveryResult | None = None
        self._discovery_seen_key: tuple[str, int] | None = None
        self._discovery_seen_paths: set[str] = set()
        self._discovery_seen_families: set[str] = set()
        self._prepared_reservoir_offsets: dict[str, int] = {}
        self._discovery_round = 0
        self._populate_anchor_choices()
        self._refresh_saved_ideas_panel()


    def load_preselected_fusion_plan(self, plan: AssistedSetPlan) -> None:
        """Open one already-validated Mash pair directly in Version Fusion."""
        if not bool(getattr(self, "fusion_only", False)) or len(plan.profiles) != 2:
            raise ValueError("A preselected Fusion plan requires Version Fusion and exactly two source files")
        self.discovery_result = AssistedDiscoveryResult(plan.profiles[0], (), (plan,), 1)
        self.recommendations = (plan,)
        self.selected_plan = None
        self._cached_discovery_result = self.discovery_result
        self._displayed_discovery_result = self.discovery_result
        self._populate_ideas()
        self.scan_status.setText("Selected Mash pair passed Version Fusion review and is ready to shape.")
        self._show_page(1)

    def _discovery_cache_key(self, anchor_path: str | None = None) -> tuple[str, int, int]:
        """Return a cheap session-local key for recommendation reuse.

        Detailed-analysis completion must *not* invalidate Back/Forward reuse.
        A 96k background deep scan may finish another file every few seconds;
        tying cache reuse to the ready count made ordinary Back/Forward navigation
        unexpectedly rerun discovery. Explicit catalogue actions invalidate the
        cache before they begin.
        """
        anchor = str(self._selected_anchor_path() if anchor_path is None else anchor_path)
        return (
            anchor,
            len(self.studio.profiles),
            int(getattr(self.studio, "_library_catalogue_revision", 0) or 0),
        )

    def _discovery_seen_scope_key(self, anchor_path: str) -> tuple[str, int]:
        return (
            str(anchor_path or ""),
            int(getattr(self.studio, "_library_catalogue_revision", 0) or 0),
        )

    def _invalidate_discovery_cache(self) -> None:
        self._cached_discovery_key = None
        self._cached_discovery_result = None
        self._displayed_discovery_result = None
        self._discovery_seen_key = None
        self._discovery_seen_paths.clear()
        self._discovery_seen_families.clear()
        self._prepared_reservoir_offsets.clear()
        self._discovery_round = 0

    def _restore_cached_discovery(self, anchor_path: str) -> bool:
        key = self._discovery_cache_key(anchor_path)
        raw = self._displayed_discovery_result or self._cached_discovery_result
        if raw is None or self._cached_discovery_key != key:
            return False
        result = self._without_ignored_pairs(raw)
        self.discovery_result = result
        self.recommendations = result.plans
        self.cancel_search_button.setVisible(False)
        self._set_scanning(False, "Using the mash ideas already found — no library search was repeated.")
        self._populate_ideas()
        self._show_page(1)
        return True

    def _show_next_cached_different_batch(self, anchor_path: str, *, limit: int = 8) -> bool:
        """Page through already-ranked unseen ideas before running another search."""
        key = self._discovery_cache_key(anchor_path)
        raw = self._cached_discovery_result
        if raw is None or self._cached_discovery_key != key:
            return False
        available: list[AssistedSetPlan] = []
        for plan in raw.standard:
            if self._plan_has_ignored_pair(plan):
                continue
            partner_paths = {
                profile.path for profile in plan.profiles
                if not anchor_path or profile.path != anchor_path
            }
            if partner_paths & self._discovery_seen_paths:
                continue
            available.append(plan)
            if len(available) >= max(1, int(limit)):
                break
        if not available:
            return False
        result = AssistedDiscoveryResult(raw.anchor, tuple(available), (), raw.evaluated_candidates)
        self.discovery_result = result
        self._displayed_discovery_result = result
        self.recommendations = result.plans
        for plan in result.standard:
            for profile in plan.profiles:
                if not anchor_path or profile.path != anchor_path:
                    self._discovery_seen_paths.add(profile.path)
        self.cancel_search_button.setVisible(False)
        if hasattr(self, "ideas_cancel_search_button"):
            self.ideas_cancel_search_button.setVisible(False)
        self._set_scanning(
            False,
            f"Showing {len(result.standard)} more already-ranked unseen idea(s) — no new library search was needed.",
        )
        self._populate_ideas()
        self._show_page(1)
        return True

    def _populate_anchor_choices(self) -> None:
        if not hasattr(self, "anchor_combo"):
            return
        previous = str(self.anchor_combo.currentData() or self._initial_anchor_path or "")
        fusion_mode = bool(getattr(self, "fusion_only", False))
        mode = "fusion" if fusion_mode else "assisted"
        total = len(self.studio.profiles)
        if total <= 2500:
            eligible = []
            for profile in self.studio.profiles.values():
                allowed, _reason = self.studio._library_path_admission(profile.path, mode=mode)
                if not allowed:
                    continue
                if fusion_mode or assisted_source_gate(profile).eligible:
                    eligible.append(profile)
        else:
            if fusion_mode:
                paths = sorted(self.studio.library_scan.version_fusion_candidates(), key=str.casefold)[:512]
                mash_ready = self.studio._authoritative_eligible_paths(__import__("midi_library_scan.lifecycle", fromlist=["EligibilityState"]).EligibilityState.MASH_READY)
                paths.extend(path for path in sorted(mash_ready, key=str.casefold)[:128] if path not in paths)
            else:
                paths = list(self.studio.assisted_anchor_paths_cached())
            for extra in (previous, self._initial_anchor_path, self.studio.source_specs[0].path if self.studio.source_specs else ""):
                if not extra or extra in paths:
                    continue
                allowed, _reason = self.studio._library_path_admission(extra, mode=mode)
                if allowed:
                    profile = self.studio.profiles.get(extra)
                    if profile is not None and (fusion_mode or assisted_source_gate(profile).eligible):
                        paths.insert(0, extra)
            eligible = [self.studio.profiles[path] for path in paths if path in self.studio.profiles]
        eligible.sort(key=lambda profile: (profile.title.casefold(), profile.path.casefold()))
        self.anchor_combo.blockSignals(True)
        self.anchor_combo.clear()
        for profile in eligible:
            tempo = int(round(float(profile.felt_bpm or profile.tempo_bpm or 0)))
            label = f"{profile.title} · {profile.key_display} · {tempo} BPM"
            self.anchor_combo.addItem(label, profile.path)
        target = self.anchor_combo.findData(previous)
        if target < 0 and self.studio.source_specs:
            target = self.anchor_combo.findData(self.studio.source_specs[0].path)
        if target < 0:
            target = 0
        if self.anchor_combo.count():
            self.anchor_combo.setCurrentIndex(max(0, target))
        self.anchor_combo.blockSignals(False)
        self._initial_anchor_path = ""
        self._sync_anchor_ui()

    def _choose_anchor_midi(self) -> None:
        """Choose an anchor from the saved Library, never from Windows Explorer."""
        self.studio.open_library_browser(purpose="fusion_anchor" if bool(getattr(self, "fusion_only", False)) else "anchor", selected_callback=self._set_anchor_path)

    def _set_anchor_path(self, selected: str) -> None:
        try:
            selected = str(Path(selected).expanduser().resolve())
        except (OSError, ValueError, TypeError):
            selected = str(selected or "")
        mode = "fusion" if bool(getattr(self, "fusion_only", False)) else "assisted"
        allowed, reason = self.studio._library_path_admission(selected, mode=mode)
        if not allowed:
            QMessageBox.information(
                self, "Library song is not eligible",
                f"That file is not currently approved for this workflow by the authoritative MIDI Library.\n\nEligibility: {reason}",
            )
            return
        profile = self.studio.profiles.get(selected)
        if profile is None:
            loaded = self.studio.load_deep_profiles([selected])
            profile = loaded.get(selected)
            if profile is not None:
                self.studio.profiles[selected] = profile
        if profile is None:
            QMessageBox.information(
                self, "Library song is not ready",
                "That file is approved in the saved catalogue but its musical profile is not available to Remixer yet. Leave MIDI Library Manager analyzing it, then try again.",
            )
            return
        if mode == "assisted":
            gate = assisted_source_gate(profile)
            if not gate.eligible:
                QMessageBox.information(
                    self, "Song needs analysis",
                    f"{profile.title} is Library-approved but not yet musically ready for Assisted Studio.\n\n{gate.summary}",
                )
                return
        index = self.anchor_combo.findData(profile.path)
        if index < 0:
            tempo = int(round(float(profile.felt_bpm or profile.tempo_bpm or 0)))
            self.anchor_combo.insertItem(0, f"{profile.title} · {profile.key_display} · {tempo} BPM", profile.path)
            index = 0
        self.anchor_combo.setCurrentIndex(index)
        if hasattr(self, "anchor_mode_anchor"):
            self.anchor_mode_anchor.setChecked(True)
        if mode == "assisted":
            try:
                self.studio.library_engine_client.command("prepare_anchor", timeout=4.0, start_if_needed=False, path=profile.path)
            except (OSError, RuntimeError, ValueError, TypeError) as exc:
                self.studio._write_ui_error_log("Prepare Assisted anchor", exc)
        self._sync_anchor_ui()

    def _sync_anchor_ui(self) -> None:
        if not hasattr(self, "anchor_combo"):
            return
        anchor_mode = bool(getattr(self, "anchor_mode_anchor", None) and self.anchor_mode_anchor.isChecked())
        has_anchor = self.anchor_combo.count() > 0
        self.anchor_combo.setEnabled(anchor_mode and has_anchor)
        if hasattr(self, "anchor_picker_button"):
            self.anchor_picker_button.setEnabled(anchor_mode)
        path = str(self.anchor_combo.currentData() or "") if anchor_mode and has_anchor else ""
        profile = self.studio.profiles.get(path)
        fusion = bool(getattr(self, "fusion_only", False))
        if hasattr(self, "anchor_summary"):
            if anchor_mode and profile is not None:
                if fusion:
                    self.anchor_summary.setText(
                        f"Fusion song: {profile.title}. MIDI Remixer will compare alternate files of this composition and recommend only materially different arrangements."
                    )
                else:
                    self.anchor_summary.setText(
                        f"Your anchor: {profile.title}. MIDI Remixer will open prepared relationships around this song. "
                        "Change the anchor explicitly to start a different discovery session."
                    )
            elif anchor_mode:
                self.anchor_summary.setText(
                    "Choose a catalogued song from the saved Library." if fusion else
                    "Choose an analyzed MIDI as the anchor. MIDI Library Manager can prioritize a song that still needs preparation."
                )
            else:
                self.anchor_summary.setText(
                    "Search the Library for composition families that contain genuinely different arrangements." if fusion else
                    "Recommend Songs for Me opens diverse prepared relationships across the library; no anchor is required."
                )
        if hasattr(self, "use_library"):
            self.use_library.setText(
                (f"Find Fusion Versions for {profile.title}" if anchor_mode and profile is not None else "Find Strong Fusion Candidates")
                if fusion else
                (f"Find Mash Ideas for {profile.title}" if anchor_mode and profile is not None else "Discover Mash Ideas")
            )
        if hasattr(self, "next") and getattr(self, "current_page", 0) == 0:
            self.next.setText(
                (f"Find Fusion Versions for {profile.title} →" if anchor_mode and profile is not None else "Find Version Fusion Candidates →")
                if fusion else
                (f"Find Ideas for {profile.title} →" if anchor_mode and profile is not None else "Discover Mash Ideas →")
            )

    def _selected_anchor_path(self) -> str:
        if not hasattr(self, "anchor_combo"):
            return ""
        if hasattr(self, "anchor_mode_anchor") and not self.anchor_mode_anchor.isChecked():
            return ""
        return str(self.anchor_combo.currentData() or "")

    def _selected_preview_bars(self) -> int:
        value = self.preview_bars_combo.currentData() if hasattr(self, "preview_bars_combo") else 8
        return 16 if int(value or 8) >= 16 else 8

    def _clear_layout(self, layout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
            child = item.layout()
            if child:
                self._clear_layout(child)

    def _cancel_discovery_search(self) -> None:
        if self.studio.operation_manager.is_active(self._discovery_operation_name):
            self._discovery_cancel_requested = True
            self.scan_status.setText("Cancelling discovery search…")
            self.cancel_search_button.setText("Cancelling…")
            self.cancel_search_button.setEnabled(False)
            if hasattr(self, "ideas_cancel_search_button"):
                self.ideas_cancel_search_button.setText("Cancelling…")
                self.ideas_cancel_search_button.setEnabled(False)
            self.studio.operation_manager.cancel(self._discovery_operation_name)
            return
        self.cancel_search_button.setVisible(False)
        if hasattr(self, "ideas_cancel_search_button"):
            self.ideas_cancel_search_button.setVisible(False)

    def _recommendation_progress(self, message: str, percent: int) -> None:
        if self._discovery_cancel_requested:
            self.scan_status.setText("Cancelling discovery search…")
            return
        self.scan_frame.setVisible(True)
        self.scan_progress.setRange(0, 100)
        self.scan_progress.setValue(max(0, min(100, int(percent))))
        self.scan_status.setText(str(message))
        if self.current_page == 1 and hasattr(self, "idea_summary"):
            self.idea_summary.setText(f"Opening another prepared batch… {max(0, min(100, int(percent)))}%\n{message}")

    @staticmethod
    def _fusion_profile_family_key(profile) -> str:
        return recommendation_family_key(profile)

    def _fusion_family_members(self) -> dict[str, tuple[str, ...]]:
        """Return authoritative Contract-4 Fusion families, including the canonical Mash representative."""
        revision = int(getattr(self.studio, "_library_catalogue_revision", 0) or 0)
        cached = getattr(self, "_fusion_family_members_cache", None)
        if cached is not None and getattr(self, "_fusion_family_cache_revision", -1) == revision:
            return cached
        fusion_by_path = self.studio.library_scan.version_fusion_candidates()
        family_lookup = self.studio.library_scan.database.family_index()
        from midi_library_scan.lifecycle import EligibilityState
        mash_ready = self.studio._authoritative_eligible_paths(EligibilityState.MASH_READY)
        groups: dict[str, list[str]] = {}
        for path, family_id in fusion_by_path.items():
            if path in self.studio.profiles:
                groups.setdefault(str(family_id), []).append(path)
        # Contract 4 generally keeps one Mash Ready representative and marks its
        # alternate versions Fusion Candidate. Add that canonical representative
        # only to families that already contain an authoritative Fusion candidate.
        for path in mash_ready:
            family_id = str(family_lookup.get(path) or "")
            if family_id and family_id in groups and path in self.studio.profiles and path not in groups[family_id]:
                groups[family_id].append(path)
        result = {
            family_id: tuple(sorted(paths, key=str.casefold))
            for family_id, paths in groups.items() if len(paths) >= 2
        }
        self._fusion_family_members_cache = result
        self._fusion_family_cache_revision = revision
        return result

    def _find_fusion_recommendations(self, *, force: bool = False, different: bool = False) -> None:
        """Find bounded, materially different same-song arrangements for Fusion."""
        if not self.studio.require_authoritative_family_identity(self, "Version Fusion"):
            self.scan_status.setText("Waiting for MIDI Library Manager to finish composition-family reconciliation…")
            return
        if self.studio.operation_manager.is_active(self._discovery_operation_name):
            return
        if not self.studio.audio_ready():
            QMessageBox.information(self, "Version Fusion", self.studio.audio_readiness_message())
            return
        anchor_path = self._selected_anchor_path()
        if not different:
            self._fusion_family_offset = 0
        offset = int(getattr(self, "_fusion_family_offset", 0) or 0)
        self._discovery_cancel_requested = False
        self.scan_status.setText("Finding materially different versions of the same song…")
        weak_self = weakref.ref(self)

        def rank(profile):
            return (
                int(getattr(profile, "orchestration_readiness", 0) or 0),
                int(getattr(profile, "track_count", 0) or 0),
                int(getattr(profile, "note_count", 0) or 0),
            )

        def work(cancel_event, progress):
            if cancel_event.is_set():
                raise InterruptedError("Version Fusion search was cancelled.")
            progress("Grouping alternate versions by composition…", 12)
            family_map = self._fusion_family_members()
            if anchor_path:
                anchor_quick = self.studio.profiles.get(anchor_path)
                if anchor_quick is None:
                    return AssistedDiscoveryResult(None, (), (), 0), 0, {"exact": 0, "tempo_only": 0, "near_duplicate": 0}
                anchor_key = str(getattr(self.studio, "_family_by_path", {}).get(anchor_path) or f"path:{anchor_path}")
                families = [(anchor_key, family_map.get(anchor_key, (anchor_path,)))]
            else:
                families = sorted(
                    family_map.items(),
                    key=lambda item: (len(item[1]), item[0]),
                    reverse=True,
                )
                # Each request examines a bounded group of families. More
                # Different Fusion Ideas advances through the cached family map.
                families = families[offset: offset + 48]
            if not families:
                return AssistedDiscoveryResult(None, (), (), 0), 0, {"exact": 0, "tempo_only": 0, "near_duplicate": 0}

            progress("Loading only candidate versions that have musical profiles…", 28)
            requested: list[str] = []
            selected_families: list[tuple[str, tuple[str, ...]]] = []
            for key, member_paths in families:
                if cancel_event.is_set():
                    raise InterruptedError("Version Fusion search was cancelled.")
                quick_members = [self.studio.profiles[path] for path in member_paths if path in self.studio.profiles]
                quick_members.sort(key=rank, reverse=True)
                bounded = tuple(profile.path for profile in quick_members[:8])
                if len(bounded) >= 2:
                    selected_families.append((key, bounded))
                    for path in bounded:
                        if path not in requested:
                            requested.append(path)
            loaded = self.studio.load_deep_profiles(requested)
            variants: list[AssistedSetPlan] = []
            skipped = {"exact": 0, "tempo_only": 0, "near_duplicate": 0}
            anchor_profile = loaded.get(anchor_path) if anchor_path else None
            family_total = max(1, len(selected_families))
            for family_index, (_key, member_paths) in enumerate(selected_families, 1):
                if cancel_event.is_set():
                    raise InterruptedError("Version Fusion search was cancelled.")
                progress("Comparing musical roles and arrangements…", 30 + int(60 * family_index / family_total))
                deep_members = [loaded[path] for path in member_paths if path in loaded]
                if len(deep_members) < 2:
                    continue
                primary = anchor_profile if anchor_profile in deep_members else max(deep_members, key=rank)
                alternates = [profile for profile in deep_members if profile.path != primary.path]
                alternates.sort(key=lambda profile: (arrangement_distance(primary, profile), rank(profile)), reverse=True)
                family_plans: list[AssistedSetPlan] = []
                for other in alternates:
                    if self.studio.recommendation_pair_neither(primary, other):
                        continue
                    kind = fusion_difference_kind(primary, other)
                    plan = version_fusion_plan_for_pair(primary, other)
                    if plan is None:
                        if kind in skipped:
                            skipped[kind] += 1
                        if kind in {"exact", "tempo_only", "near_duplicate"}:
                            # Fusion has now made the second-stage decision the DJ
                            # requested: this same-song version pair is not useful
                            # enough for Fusion and is also too similar for a Mash.
                            self.studio.pair_dispositions.classify_neither(primary, other, reason=kind)
                        continue
                    family_plans.append(plan)
                    if anchor_path and len(family_plans) >= 6:
                        break
                    if not anchor_path:
                        break
                variants.extend(family_plans)
                if len(variants) >= 8:
                    break
            variants.sort(
                key=lambda plan: (
                    plan.confidence,
                    arrangement_distance(plan.profiles[0], plan.profiles[1]),
                ),
                reverse=True,
            )
            consumed = len(families)
            progress("Version Fusion candidates ready.", 100)
            return AssistedDiscoveryResult(anchor_profile, (), tuple(variants[:8]), len(requested)), consumed, skipped

        def success(payload) -> None:
            owner = weak_self()
            if owner is None or owner._closing_discovery:
                return
            result, consumed, skipped = payload
            if consumed and not anchor_path:
                owner._fusion_family_offset = offset + consumed
            owner.discovery_result = result
            owner.recommendations = result.variants
            owner.cancel_search_button.setVisible(False)
            if hasattr(owner, "ideas_cancel_search_button"):
                owner.ideas_cancel_search_button.setVisible(False)
            if not result.variants:
                owner.scan_status.setText("No materially different same-song arrangements were found in this batch.")
                counts = ", ".join(f"{value} {key.replace('_', ' ')}" for key, value in skipped.items() if value)
                QMessageBox.information(
                    owner, "No Useful Fusion Pair in This Batch",
                    "MIDI Remixer found version families, but none contributed enough different musical material to recommend as a Fusion. "
                    "Exact copies and tempo-only exports are deliberately suppressed; instrumentally different versions remain valid Fusion material."
                    + (f"\n\nSuppressed here: {counts}." if counts else "")
                )
                return
            owner.scan_status.setText(f"{len(result.variants)} materially different Version Fusion candidate(s) ready.")
            owner._populate_ideas()
            owner._show_page(1)

        def failure(exc: BaseException) -> None:
            owner = weak_self()
            if owner is None or owner._closing_discovery:
                return
            owner.cancel_search_button.setVisible(False)
            if "cancel" in str(exc).casefold():
                owner.scan_status.setText("Version Fusion search cancelled.")
                return
            owner._report_error("Version Fusion candidate discovery", exc if isinstance(exc, Exception) else RuntimeError(str(exc)))

        started = self.studio.operation_manager.start(
            self._discovery_operation_name, work, on_success=success, on_failure=failure,
            on_progress=self._recommendation_progress,
        )
        if not started:
            self.scan_status.setText("Version Fusion candidate retrieval is already running.")

    def _find_recommendations(self, *, force: bool = False, different: bool = False) -> None:
        """Open prepared relationships, or same-song families in Version Fusion."""
        workflow_name = "Version Fusion" if bool(getattr(self, "fusion_only", False)) else "Assisted Studio"
        if not self.studio.require_authoritative_family_identity(self, workflow_name):
            self.scan_status.setText("Waiting for MIDI Library Manager to finish composition-family reconciliation…")
            return
        if bool(getattr(self, "fusion_only", False)):
            self._find_fusion_recommendations(force=force, different=different)
            return
        summary = self.studio.assisted_library_summary_cached()
        if summary["eligible"] < 2:
            QMessageBox.information(
                self, getattr(self, "workflow_title", "Assisted Studio"),
                "At least two usable MIDI files are needed. MIDI Library Manager can continue preparing the library in the background.",
            )
            return
        if self.studio.operation_manager.is_active(self._discovery_operation_name):
            return

        anchor_path = self._selected_anchor_path()
        if not force and self._restore_cached_discovery(anchor_path):
            return
        scope = self._discovery_seen_scope_key(anchor_path)
        reservoir_key = anchor_path or "*whole-library*"
        if self._discovery_seen_key != scope:
            self._discovery_seen_key = scope
            self._discovery_seen_paths.clear()
            self._discovery_seen_families.clear()
            self._prepared_reservoir_offsets[reservoir_key] = 0
        if not different:
            self._prepared_reservoir_offsets[reservoir_key] = 0
            self._discovery_seen_paths.clear()
            self._discovery_seen_families.clear()

        offset = int(self._prepared_reservoir_offsets.get(reservoir_key, 0) or 0)
        focus_profile = self.studio.profiles.get(anchor_path) if anchor_path else None
        focus = focus_profile.title if focus_profile is not None else "the prepared library"
        self._discovery_cancel_requested = False
        self.scan_status.setText(
            "Opening the next prepared ideas…" if different else f"Opening prepared mash ideas for {focus}…"
        )
        discovery_started = time.perf_counter()
        weak_self = weakref.ref(self)

        def work(cancel_event, progress):
            if cancel_event.is_set():
                raise InterruptedError("Assisted recommendation retrieval was cancelled.")
            plans: list[AssistedSetPlan] = []
            local_families: set[str] = set()
            consumed = 0
            total_rows = 0
            page_size = 64
            # Prepared rows are intentionally paged a few times in one request.
            # This keeps More Different Ideas fast even when earlier rows are
            # ignored, retired, same-family, or already shown. It never performs
            # a broad 96K discovery pass inside Remixer.
            for page in range(4):
                if cancel_event.is_set():
                    raise InterruptedError("Assisted recommendation retrieval was cancelled.")
                progress("Reading prepared relationship index…", min(55, 15 + page * 12))
                response = self.studio.library_engine_client.command(
                    "recommended_pairs", timeout=4.0, start_if_needed=False, mode="assisted",
                    limit=page_size, offset=offset + consumed,
                    anchor_path=anchor_path or "",
                )
                rows = list(response.get("pairs") or [])
                if not rows:
                    break
                consumed += len(rows)
                total_rows += len(rows)
                paths: list[str] = []
                for row in rows:
                    for key in ("path_a", "path_b"):
                        value = str(row.get(key) or "")
                        if value and value not in paths:
                            paths.append(value)
                progress("Loading only the prepared candidate profiles…", min(75, 40 + page * 8))
                loaded = self.studio.load_deep_profiles(paths)
                for row in rows:
                    if cancel_event.is_set():
                        raise InterruptedError("Assisted recommendation retrieval was cancelled.")
                    path_a = str(row.get("path_a") or "")
                    path_b = str(row.get("path_b") or "")
                    a, b = loaded.get(path_a), loaded.get(path_b)
                    if a is None or b is None:
                        continue
                    if not self.studio._library_path_admission(path_a, mode="assisted")[0] or not self.studio._library_path_admission(path_b, mode="assisted")[0]:
                        continue
                    family_a = str(row.get("family_a") or f"path:{path_a}")
                    family_b = str(row.get("family_b") or f"path:{path_b}")
                    if family_a == family_b:
                        continue
                    partner_families = {family_a, family_b}
                    if anchor_path:
                        anchor_family = family_a if path_a == anchor_path else family_b if path_b == anchor_path else ""
                        partner_family = family_b if path_a == anchor_path else family_a if path_b == anchor_path else ""
                        if not anchor_family or not partner_family:
                            continue
                        if partner_family in self._discovery_seen_families or partner_family in local_families:
                            continue
                    elif partner_families & (self._discovery_seen_families | local_families):
                        # Whole-library pages deliberately rotate composition
                        # families so breadth from a 96K collection is visible.
                        continue
                    if self.studio.recommendation_pair_suppressed(a, b):
                        continue
                    plan = assisted_plan_from_snapshot(row.get("prepared_plan") or {}, (a, b))
                    if plan is None:
                        continue
                    plans.append(plan)
                    if anchor_path:
                        local_families.add(partner_family)
                    else:
                        local_families.update(partner_families)
                    if len(plans) >= 8:
                        break
                if len(plans) >= 8 or len(rows) < page_size:
                    break

            if not plans and anchor_path:
                # Requesting preparation is a cheap priority signal. It does not
                # pause the full background run or perform discovery in Remixer.
                self.studio.library_engine_client.command(
                    "prepare_anchor", timeout=4.0, start_if_needed=False, path=anchor_path
                )

            variants: list[AssistedSetPlan] = []
            if anchor_path and focus_profile is not None:
                family_id = str(getattr(self.studio, "_family_by_path", {}).get(anchor_path) or "")
                family_members = list(self._fusion_family_members().get(family_id, ())) if family_id else []
                alternates = [path for path in family_members if path != anchor_path][:8]
                if alternates:
                    alt_loaded = self.studio.load_deep_profiles([anchor_path, *alternates])
                    anchor_deep = alt_loaded.get(anchor_path)
                    if anchor_deep is not None:
                        for alternate in alternates:
                            other = alt_loaded.get(alternate)
                            if other is None:
                                continue
                            variant = version_fusion_plan_for_pair(anchor_deep, other)
                            if variant is not None:
                                variants.append(variant)
                                break
            progress("Prepared ideas ready.", 100)
            return AssistedDiscoveryResult(
                focus_profile, tuple(plans), tuple(variants[:1]), total_rows
            ), consumed

        def success(payload) -> None:
            owner = weak_self()
            if owner is None or owner._closing_discovery:
                return
            result, consumed = payload
            record_performance(
                owner.studio.paths.logs / "performance.log", "assisted_prepared_retrieval",
                seconds=time.perf_counter() - discovery_started, anchor=bool(anchor_path),
                different=bool(different), rows=int(result.evaluated_candidates or 0),
            )
            if consumed:
                owner._prepared_reservoir_offsets[reservoir_key] = offset + consumed
            result = owner._without_ignored_pairs(result)
            if not result.plans:
                owner.cancel_search_button.setVisible(False)
                if anchor_path:
                    owner.scan_status.setText(
                        "No additional unseen Assisted ideas are available for this anchor in the current prepared pool."
                    )
                    QMessageBox.information(
                        owner, "No More Distinct Assisted Ideas",
                        "The current prepared pool has no additional unseen relationship for this anchor. "
                        "The anchor was prioritized for future Library preparation, but Remixer did not start a full-library search. "
                        "Try a different anchor, broaden the direction, or use Version Fusion for alternate versions.",
                    )
                else:
                    owner.scan_status.setText(
                        "No additional unseen Assisted ideas are available in the current prepared pool."
                    )
                    QMessageBox.information(
                        owner, "No More Distinct Assisted Ideas",
                        "The current prepared Assisted pool has no additional unseen composition families for this session. "
                        "Try a different anchor, broader criteria, Version Fusion, or start a different search. "
                        "No full-library search was started in Remixer.",
                    )
                return
            owner._cached_discovery_key = owner._discovery_cache_key(anchor_path)
            owner._cached_discovery_result = result
            owner._displayed_discovery_result = result
            owner.discovery_result = result
            owner.recommendations = result.plans
            family_lookup = getattr(owner.studio, "_family_by_path", {})
            for plan in result.standard:
                for profile in plan.profiles:
                    if not anchor_path or profile.path != anchor_path:
                        owner._discovery_seen_paths.add(profile.path)
                        family = str(family_lookup.get(profile.path) or f"path:{profile.path}")
                        owner._discovery_seen_families.add(family)
            owner.cancel_search_button.setVisible(False)
            if hasattr(owner, "ideas_cancel_search_button"):
                owner.ideas_cancel_search_button.setVisible(False)
            owner.scan_status.setText(
                f"{len(result.standard)} prepared cross-song idea(s) ready"
                + (" · 1 Version Fusion candidate" if result.variants else "")
            )
            owner._populate_ideas()
            owner._show_page(1)

        def failure(exc: BaseException) -> None:
            owner = weak_self()
            if owner is None or owner._closing_discovery:
                return
            owner.cancel_search_button.setVisible(False)
            if "cancel" in str(exc).casefold():
                owner.scan_status.setText("Mash-idea retrieval cancelled.")
                return
            owner._report_error(
                "Prepared Assisted recommendation retrieval",
                exc if isinstance(exc, Exception) else RuntimeError(str(exc)),
            )

        started = self.studio.operation_manager.start(
            self._discovery_operation_name, work, on_success=success, on_failure=failure,
            on_progress=self._recommendation_progress,
        )
        if not started:
            self.scan_status.setText("Prepared mash ideas are already being opened.")

    @staticmethod
    def _toggle_plan_notes(button: QPushButton, panel: QFrame) -> None:
        visible = not panel.isVisible()
        panel.setVisible(visible)
        button.setText("Hide Full Notes ▴" if visible else "Show Full Notes ▾")

    def _populate_ideas(self) -> None:
        self._clear_layout(self.idea_layout)
        self._preview_buttons = []
        self._refresh_saved_ideas_panel()
        result = self.discovery_result
        if not result.plans:
            if bool(getattr(self, "fusion_only", False)):
                self.idea_summary.setText(
                    "No materially different version pair is ready yet. Exact copies and tempo-only exports are intentionally hidden; instrumentally different versions are valid Fusion material when they contribute a distinct palette. Choose another song or let Library analysis continue."
                )
            else:
                self.idea_summary.setText(
                    "MIDI Remixer found usable files but no distinct set with enough measurable support and previewable arrangements. "
                    "Choose another anchor, add more songs, or open Creative Workbench for manual preparation."
                )
            return
        if bool(getattr(self, "fusion_only", False)):
            anchor_text = f" for {result.anchor.title}" if result.anchor is not None else " across your library"
            self.idea_summary.setText(
                f"Version Fusion found {len(result.variants)} same-song arrangement candidate(s){anchor_text}. "
                "Exact/near duplicates and global tempo-only exports are suppressed. "
                "Instrumentally different versions are valid when they contribute a distinct palette, roles, sections, or arrangement material."
            )
            self._add_idea_section(
                "Version Fusion — complementary arrangements of the same song",
                result.variants, variant=True,
                explanation=(
                    "These files represent the same composition but contain materially different arrangement content. "
                    "Fusion can keep the strongest melody/solo, backing, bass, drums, orchestration, or sections from each without doubling the whole song."
                ),
            )
            self.idea_layout.addStretch(1)
            return
        anchor_text = f" for {result.anchor.title}" if result.anchor is not None else " across your library"
        prepared_rows = max(0, int(result.evaluated_candidates or 0))
        coverage_text = (
            f"MIDI Library Engine had already prepared these relationships{anchor_text}. "
            f"MIDI Remixer opened {prepared_rows} committed relationship row(s) and selected "
            f"{len(result.standard)} unseen, distinct-composition idea(s) without rerunning a full-library search."
        )
        self.idea_summary.setText(
            coverage_text + " Short previews are generated only when requested and remain inside the disposable session workspace."
        )
        grouped: dict[str, list[AssistedSetPlan]] = {}
        for plan in result.standard:
            grouped.setdefault(assisted_plan_group(plan), []).append(plan)
        sections = (
            (ASSISTED_GROUP_ADAPTABLE, "Assisted-only · adaptable sources",
             "These include piano reductions, combined-role tracks, or other sources that Guided Remix excludes. Assisted Studio proposes the preparation before generation."),
            (ASSISTED_GROUP_SECTION, "Assisted-only · section and transformation ideas",
             "These avoid forcing a dense simultaneous mash. The engine uses exchanges, journeys, or transformations where the musical evidence supports them."),
            (ASSISTED_GROUP_MULTI, "Assisted-only · three- and four-song sets",
             "Extra songs enter by role or section; they are not all forced to play at once."),
            (ASSISTED_GROUP_BROADER, "Broader two-song possibilities",
             "These pass the Assisted preparation gate but are intentionally more open than the strict beginner-safe list."),
            (ASSISTED_GROUP_GUIDED, "Dependable Guided-level reference",
             "At most one strict Guided Remix pairing is retained for comparison. It does not occupy the broader Assisted-only result slots."),
        )
        for key, title, explanation in sections:
            plans = tuple(grouped.get(key, ()))
            if plans:
                self._add_idea_section(title, plans, variant=False, explanation=explanation)
        if result.variants:
            self._add_idea_section(
                "Version Fusion candidates — alternate arrangements of the same song",
                result.variants,
                variant=True,
                explanation=(
                    "These are separated from normal mashes. MIDI Remixer found alternate arrangements that may contribute "
                    "stronger melody, bass, drums, chords, or sections to one fused version."
                ),
            )
        self.idea_layout.addStretch(1)

    def _add_idea_section(
        self, title: str, plans: tuple[AssistedSetPlan, ...], *, variant: bool, explanation: str
    ) -> None:
        heading = QLabel(title)
        heading.setObjectName("SectionTitle")
        self.idea_layout.addWidget(heading)
        explanation = QLabel(explanation)
        explanation.setObjectName("Subtitle")
        explanation.setWordWrap(True)
        self.idea_layout.addWidget(explanation)
        for index, plan in enumerate(plans):
            self._add_idea_card(plan, index=index, variant=variant)

    def _add_idea_card(self, plan: AssistedSetPlan, *, index: int, variant: bool) -> None:
        group = assisted_plan_group(plan)
        if variant:
            label = "Version Fusion candidate"
        elif group == ASSISTED_GROUP_GUIDED:
            label = "Guided-level reference"
        elif group == ASSISTED_GROUP_MULTI:
            label = "Multi-song idea"
        elif self.discovery_result.anchor is not None:
            label = "Anchor-based Assisted idea"
        else:
            label = "Assisted idea"
        card = QGroupBox(f"{label}: {plan.headline}")
        layout = QVBoxLayout(card)
        discovery_suffix = ""
        if plan.discovery_score > 0:
            evidence_label = ", ".join(plan.discovery_labels[:2]) or "reference evidence"
            discovery_suffix = f" · Discovery evidence: {evidence_label} ({plan.discovery_score}%)"
        status = QLabel(
            f"{plan.category} · {plan.source_count} source{'s' if plan.source_count != 1 else ''} · "
            f"Preparation confidence: {assisted_score_label(plan.confidence)} ({plan.confidence}%)"
            + discovery_suffix
        )
        status.setObjectName("StatusPill")
        summary_label = QLabel(plan.summary)
        summary_label.setWordWrap(True)
        highlights = QLabel("TOP 3 DECISION FACTS\n" + "\n".join(f"• {item}" for item in plan.reasons[:3]))
        highlights.setObjectName("InfoPanel")
        highlights.setWordWrap(True)
        highlights.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        details = QFrame()
        details.setObjectName("DetailsPanel")
        detail_layout = QVBoxLayout(details)
        detail_layout.setContentsMargins(9, 7, 9, 7)
        discovery_text = ""
        if plan.discovery_score > 0 or plan.discovery_providers:
            discovery_text = (
                "\n\nWHY THIS IDEA ENTERED THE SHORTLIST\n"
                + ("• Evidence: " + ", ".join(plan.discovery_labels) + "\n" if plan.discovery_labels else "")
                + ("• Sources: " + ", ".join(plan.discovery_providers) if plan.discovery_providers else "")
            )
        detail_text = (
            "PROPOSED CONTRIBUTIONS\n" + "\n".join(f"• {item}" for item in plan.contribution_plan)
            + "\n\nSIGNATURE MATERIAL\n" + "\n".join(f"• {item}" for item in plan.signature_plan)
            + discovery_text
            + "\n\nALL TECHNICAL NOTES\n" + "\n".join(f"• {item}" for item in plan.reasons)
        )
        full = QLabel(detail_text)
        full.setWordWrap(True)
        full.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        detail_layout.addWidget(full)
        if plan.warnings:
            warning = QLabel("REVIEW BEFORE GENERATION\n" + "\n".join(f"• {item}" for item in plan.warnings))
            warning.setObjectName("WarningPanel")
            warning.setWordWrap(True)
            detail_layout.addWidget(warning)
        details.setVisible(False)

        source_row = QHBoxLayout()
        for source_index, profile in enumerate(plan.profiles[:4]):
            play = QPushButton(f"Source {source_index + 1}")
            play.setObjectName("PlayButton")
            play.setToolTip(f"Play original MIDI: {profile.title}")
            play.clicked.connect(lambda _checked=False, p=profile: self._play_profile(p))
            source_row.addWidget(play)
        source_row.addStretch(1)

        actions = QHBoxLayout()
        notes = QPushButton("Show Full Notes ▾")
        notes.setObjectName("UtilityButton")
        notes.clicked.connect(lambda _checked=False, b=notes, d=details: self._toggle_plan_notes(b, d))
        preview8 = QPushButton("Preview 8 Bars")
        preview8.setObjectName("PlayButton")
        preview8.clicked.connect(lambda _checked=False, p=plan: self._audition_preview(p, 8))
        preview16 = QPushButton("Preview 16 Bars")
        preview16.setObjectName("PlayButton")
        preview16.clicked.connect(lambda _checked=False, p=plan: self._audition_preview(p, 16))
        ignore = None
        fusion_check = None
        if plan.source_count == 2 and not variant:
            ignore = QPushButton("Ignore This Pair")
            ignore.setObjectName("UtilityButton")
            ignore.setToolTip("Hide this song relationship in future Guided and Assisted recommendations. Original MIDI files are not changed.")
            ignore.clicked.connect(lambda _checked=False, p=plan: self._ignore_plan_pair(p))
            fusion_check = QPushButton("Try as Version Fusion")
            fusion_check.setObjectName("UtilityButton")
            fusion_check.setToolTip(
                "Check whether this apparent mash is really a same-song version pair. Useful alternate arrangements move to Version Fusion; redundant pairs are suppressed."
            )
            fusion_check.clicked.connect(lambda _checked=False, p=plan: self._try_plan_as_fusion(p))
        save = QPushButton()
        save.setObjectName("UtilityButton")
        save.clicked.connect(lambda _checked=False, p=plan, b=save: self._toggle_saved_idea(p, b))
        self._sync_save_button(save, plan)
        choose = QPushButton("Shape Version Fusion" if variant else "Shape This Idea")
        choose.setObjectName("PrimaryButton")
        choose.clicked.connect(lambda _checked=False, p=plan: self._choose_plan(p))
        actions.addWidget(notes)
        actions.addWidget(preview8)
        actions.addWidget(preview16)
        if ignore is not None:
            actions.addWidget(ignore)
        if fusion_check is not None:
            actions.addWidget(fusion_check)
        actions.addWidget(save)
        actions.addStretch(1)
        actions.addWidget(choose)
        self._preview_buttons.extend((preview8, preview16))

        layout.addWidget(status)
        layout.addWidget(summary_label)
        layout.addWidget(highlights)
        if plan.warnings:
            warning_preview = QLabel("Review: " + plan.warnings[0])
            warning_preview.setObjectName("WarningPanel")
            warning_preview.setWordWrap(True)
            layout.addWidget(warning_preview)
        layout.addLayout(source_row)
        layout.addLayout(actions)
        layout.addWidget(details)
        self.idea_layout.addWidget(card)

    def _try_plan_as_fusion(self, plan: AssistedSetPlan) -> None:
        if plan.source_count != 2:
            return
        outcome = self.studio.route_pair_to_fusion(plan.profiles[0], plan.profiles[1])
        if outcome != "neither":
            return
        self.discovery_result = self._without_ignored_pairs(self.discovery_result)
        self.recommendations = self.discovery_result.plans
        self._populate_ideas()

    def _plan_has_ignored_pair(self, plan: AssistedSetPlan) -> bool:
        return any(
            self.studio.recommendation_pair_suppressed(first, second)
            for first, second in combinations(plan.profiles, 2)
        )

    def _without_ignored_pairs(self, result: AssistedDiscoveryResult, limit: int = 8) -> AssistedDiscoveryResult:
        standard_items: list[AssistedSetPlan] = []
        redirected_variants: list[AssistedSetPlan] = []
        for plan in result.standard:
            if self._plan_has_ignored_pair(plan):
                continue
            if plan.source_count == 2 and mash_pair_is_too_similar(plan.profiles[0], plan.profiles[1]):
                # A stale/prepared relationship may have been created before the
                # current stricter identity policy. Never present it as Assisted
                # mash diversity. If it is musically complementary, route it into
                # the explicitly labeled Version Fusion lane instead.
                fusion_plan = version_fusion_plan_for_pair(plan.profiles[0], plan.profiles[1])
                if fusion_plan is not None and not self._plan_has_ignored_pair(fusion_plan):
                    redirected_variants.append(fusion_plan)
                continue
            standard_items.append(plan)
        standard = tuple(standard_items)
        variants = tuple(
            plan for plan in (*result.variants, *redirected_variants)
            if not self._plan_has_ignored_pair(plan)
        )
        if variants and len(standard) >= max(1, limit):
            standard = standard[: max(1, limit) - 1]
            variants = variants[:1]
        else:
            standard = standard[: max(1, limit)]
            variants = variants[: max(0, limit - len(standard))]
        return AssistedDiscoveryResult(result.anchor, standard, variants, result.evaluated_candidates)

    def _ignore_plan_pair(self, plan: AssistedSetPlan) -> None:
        if plan.source_count != 2:
            return
        self.studio.ignore_mash_pair(plan.profiles[0], plan.profiles[1])
        self.discovery_result = self._without_ignored_pairs(self.discovery_result)
        self.recommendations = self.discovery_result.plans
        self._populate_ideas()

    def _saved_ids(self) -> set[str]:
        return {
            str(item.get("idea_id") or "")
            for item in self.studio.session_workspace.saved_discovery_ideas()
        }

    def _sync_save_button(self, button: QPushButton, plan: AssistedSetPlan) -> None:
        saved = discovery_plan_id(plan) in self._saved_ids()
        button.setText("Saved ✓" if saved else "Save Idea")
        button.setProperty("savedIdea", saved)
        button.style().unpolish(button)
        button.style().polish(button)

    def _toggle_saved_idea(self, plan: AssistedSetPlan, button: QPushButton | None = None) -> None:
        idea_id = discovery_plan_id(plan)
        if idea_id in self._saved_ids():
            self.studio.session_workspace.remove_discovery_idea(idea_id)
        else:
            self.studio.session_workspace.save_discovery_idea(
                discovery_idea_payload(plan, preview_bars=self._selected_preview_bars())
            )
            self.studio.record_mash_feedback(plan.profiles, "save")
        if button is not None:
            self._sync_save_button(button, plan)
        self._refresh_saved_ideas_panel()

    def _refresh_saved_ideas_panel(self) -> None:
        if not hasattr(self, "saved_ideas_label"):
            return
        count = len(self.studio.session_workspace.saved_discovery_ideas())
        self.saved_ideas_label.setText(f"Saved ideas: {count}")
        self.compare_saved_button.setEnabled(count > 0)
        self.clear_saved_button.setEnabled(count > 0)

    def _clear_saved_ideas(self) -> None:
        for item in self.studio.session_workspace.saved_discovery_ideas():
            self.studio.session_workspace.remove_discovery_idea(str(item.get("idea_id") or ""))
        self._refresh_saved_ideas_panel()
        self._populate_ideas()

    def _current_plan_map(self) -> dict[str, AssistedSetPlan]:
        return {discovery_plan_id(plan): plan for plan in self.recommendations}

    def _compare_saved_ideas(self) -> None:
        saved = self.studio.session_workspace.saved_discovery_ideas()
        if not saved:
            QMessageBox.information(self, "Saved Ideas", "Save at least one idea before opening the comparison.")
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Compare Saved Mash Ideas")
        dialog.setModal(False)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        dialog.resize(960, min(680, 220 + len(saved) * 70))
        outer = QVBoxLayout(dialog)
        note = QLabel("Saved ideas remain session notes until you choose one and generate a full Trial.")
        note.setObjectName("InfoPanel")
        note.setWordWrap(True)
        outer.addWidget(note)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        container = QWidget()
        grid = QGridLayout(container)
        headers = ("Idea", "Type", "Confidence", "Target", "Recipes", "Actions")
        for column, text in enumerate(headers):
            label = QLabel(text)
            label.setObjectName("CardTitle")
            grid.addWidget(label, 0, column)
        current = self._current_plan_map()
        for row, payload in enumerate(saved, 1):
            idea_id = str(payload.get("idea_id") or "")
            grid.addWidget(QLabel(str(payload.get("headline") or "Saved idea")), row, 0)
            grid.addWidget(QLabel(str(payload.get("category") or "")), row, 1)
            grid.addWidget(QLabel(f"{int(payload.get('confidence') or 0)}%"), row, 2)
            target = f"{payload.get('target_bpm', '?')} BPM · {payload.get('target_mode', '')}"
            grid.addWidget(QLabel(target), row, 3)
            recipes = ", ".join(str(item).replace("-", " ") for item in payload.get("recipe_keys", [])[:3])
            recipe_label = QLabel(recipes)
            recipe_label.setWordWrap(True)
            grid.addWidget(recipe_label, row, 4)
            actions = QHBoxLayout()
            plan = current.get(idea_id)
            preview = QPushButton("Preview")
            preview.setObjectName("PlayButton")
            preview.setEnabled(plan is not None)
            if plan is not None:
                preview.clicked.connect(lambda _checked=False, p=plan, bars=int(payload.get("preview_bars") or 8): self._audition_preview(p, bars))
            remove = QPushButton("Remove")
            remove.setObjectName("UtilityButton")
            remove.clicked.connect(lambda _checked=False, value=idea_id, d=dialog: self._remove_saved_from_compare(value, d))
            actions.addWidget(preview)
            actions.addWidget(remove)
            grid.addLayout(actions, row, 5)
        grid.setColumnStretch(0, 2)
        grid.setColumnStretch(4, 2)
        scroll.setWidget(container)
        outer.addWidget(scroll, 1)
        close = QPushButton("Close")
        close.setObjectName("PrimaryButton")
        close.clicked.connect(dialog.close)
        outer.addWidget(close, 0, Qt.AlignmentFlag.AlignRight)
        dialog.show()
        dialog.raise_()
        self._saved_compare_dialog = dialog

    def _remove_saved_from_compare(self, idea_id: str, dialog: QDialog) -> None:
        self.studio.session_workspace.remove_discovery_idea(idea_id)
        dialog.close()
        self._refresh_saved_ideas_panel()
        self._populate_ideas()
        self._compare_saved_ideas() if self.studio.session_workspace.saved_discovery_ideas() else None

    def _set_preview_buttons_enabled(self, enabled: bool) -> None:
        for button in self._preview_buttons:
            button.setEnabled(enabled)

    def _audition_preview(self, plan: AssistedSetPlan, bars: int) -> None:
        if self.studio.operation_manager.is_active(self._preview_operation_name):
            self.preview_status.setText("A preview is already being prepared. Wait or press Stop after playback begins.")
            return
        bars = 16 if int(bars) >= 16 else 8
        self.preview_status.setText(f"Preparing {bars}-bar preview: {plan.headline}…")
        self._set_preview_buttons_enabled(False)
        preview_root = self.studio.session_workspace.owned_directory("discovery-previews")
        weak_self = weakref.ref(self)

        def work(cancel_event, progress):
            progress(f"Generating {bars}-bar MIDI preview…", 20)
            if cancel_event.is_set():
                raise InterruptedError("Preview generation was cancelled.")
            take = generate_assisted_preview(plan, preview_root, bars=bars)
            progress("Preview ready", 100)
            return take

        def success(take: "RemixTake") -> None:
            owner = weak_self()
            if owner is None or owner._closing_discovery:
                return
            owner._set_preview_buttons_enabled(True)
            owner.preview_status.setText(f"Playing {bars}-bar preview: {plan.headline}")
            owner.studio.record_mash_feedback(plan.profiles, "audition")
            owner.studio._play_path(
                take.path,
                take.name,
                f"discovery-{discovery_plan_id(plan)}-{bars}",
            )

        def failure(exc: BaseException) -> None:
            owner = weak_self()
            if owner is None or owner._closing_discovery:
                return
            owner._set_preview_buttons_enabled(True)
            owner.preview_status.setText("Preview could not be created; the idea remains available for full generation.")
            if isinstance(exc, (InterruptedError, RuntimeError)) and "cancel" in str(exc).casefold():
                return
            owner._report_error("Discovery preview", exc if isinstance(exc, Exception) else RuntimeError(str(exc)))

        started = self.studio.operation_manager.start(
            self._preview_operation_name,
            work,
            on_success=success,
            on_failure=failure,
            on_progress=lambda message, _percent: self.preview_status.setText(message),
        )
        if not started:
            self._set_preview_buttons_enabled(True)
            self.preview_status.setText("Another preview operation is already active.")

    def _shutdown_discovery_operations(self) -> None:
        self._closing_discovery = True
        self.studio.operation_manager.cancel(self._discovery_operation_name)
        self.studio.operation_manager.cancel(self._preview_operation_name)
