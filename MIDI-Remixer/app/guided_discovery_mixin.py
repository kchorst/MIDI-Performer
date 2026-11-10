from __future__ import annotations

"""Instant Guided Remix discovery from the Library Engine recommendation reservoir."""

import weakref

from PySide6.QtWidgets import QMessageBox

from library_engine.prepared_plan import guided_plan_from_snapshot
from core.operation_diagnostics import write_diagnostic
from core.song_identity import mash_pair_conflicts_with_seen, mash_pair_is_too_similar, mash_session_identity_keys


class GuidedWizardDiscoveryMixin:
    def _cancel_guided_discovery(self) -> None:
        if self.studio.operation_manager.is_active(self._guided_discovery_operation_name):
            self._guided_discovery_cancel_requested = True
            self.studio.operation_manager.cancel(self._guided_discovery_operation_name)

    def _guided_discovery_progress(self, message: str, percent: int) -> None:
        # Prepared recommendations should normally return before a progress UI is
        # useful. Keep the text hook for diagnostics without presenting Beginner
        # users with a long-running search experience.
        self.scan_status.setText(str(message))

    def _find_recommendations(self) -> None:
        if not self.studio.require_authoritative_family_identity(self, "Guided Remix"):
            self.scan_status.setText("Waiting for MIDI Library Manager to finish composition-family reconciliation…")
            return
        if not self.studio.audio_ready():
            QMessageBox.information(self, "Finish audio setup", self.studio.audio_readiness_message())
            self._refresh_readiness()
            return
        summary = self.studio.guided_library_summary_cached()
        if summary.get("guided_ready_songs", 0) < 2:
            QMessageBox.information(
                self, "Guided Remix",
                "At least two distinct Guided-ready songs are needed. MIDI Library Manager can continue detailed analysis in the background.",
            )
            return
        if self.studio.operation_manager.is_active(self._guided_discovery_operation_name):
            return

        weak_self = weakref.ref(self)
        self._guided_discovery_cancel_requested = False
        offset = int(getattr(self, "_guided_reservoir_offset", 0) or 0)
        prior_paths = tuple(getattr(self, "_guided_seen_profile_paths", ())[-240:])
        prior_family_keys = frozenset(getattr(self, "_guided_seen_family_keys", set()))
        prior_library_families = frozenset(getattr(self, "_guided_seen_library_family_ids", set()))
        self.scan_status.setText("Opening prepared mash ideas…")

        def work(cancel_event, progress):
            if cancel_event.is_set():
                raise InterruptedError("Guided recommendation retrieval was cancelled.")
            plans = []
            used_families: set[str] = set()
            new_library_families: set[str] = set()
            used_profiles = []
            prior_profiles = list(self.studio.load_deep_profiles(list(prior_paths)).values()) if prior_paths else []
            normalized_families = set(prior_family_keys)
            consumed = 0
            page_size = 48
            # A prepared reservoir is cheap to page. Read a few bounded pages in
            # one user action so ignored/retired/duplicate-family rows do not
            # force a Beginner to click More Ideas repeatedly or trigger a new
            # musical search. No pair analysis is performed here.
            for page in range(8):
                progress("Opening prepared mash ideas…", min(70, 15 + page * 15))
                response = self.studio.library_engine_client.command(
                    "recommended_pairs", timeout=4.0, start_if_needed=False, mode="guided",
                    limit=page_size, offset=offset + consumed,
                )
                rows = list(response.get("pairs") or [])
                if not rows:
                    break
                consumed += len(rows)
                paths: list[str] = []
                for row in rows:
                    for key in ("path_a", "path_b"):
                        path = str(row.get(key) or "")
                        if path and path not in paths:
                            paths.append(path)
                profiles = self.studio.load_deep_profiles(paths)

                def identity_keys(profile) -> frozenset[str]:
                    return mash_session_identity_keys(profile)

                for row in rows:
                    if cancel_event.is_set():
                        raise InterruptedError("Guided recommendation retrieval was cancelled.")
                    path_a = str(row.get("path_a") or "")
                    path_b = str(row.get("path_b") or "")
                    a = profiles.get(path_a)
                    b = profiles.get(path_b)
                    if a is None or b is None:
                        continue
                    if not self.studio._library_path_admission(path_a, mode="guided")[0] or not self.studio._library_path_admission(path_b, mode="guided")[0]:
                        continue
                    family_a = str(row.get("family_a") or "")
                    family_b = str(row.get("family_b") or "")
                    if not family_a or not family_b or family_a == family_b:
                        continue
                    if family_a in prior_library_families or family_b in prior_library_families:
                        continue
                    # Family IDs are the fast first guard, but existing 96K
                    # catalogues may contain older/over-literal IDs. Deep-profile
                    # identity is therefore authoritative at presentation time.
                    if mash_pair_is_too_similar(a, b):
                        continue
                    identity_a = identity_keys(a)
                    identity_b = identity_keys(b)
                    if identity_a.intersection(identity_b):
                        continue
                    if identity_a.intersection(normalized_families) or identity_b.intersection(normalized_families):
                        continue
                    # Beginner pages maximize breadth: a composition appears at
                    # most once on a visible page, and "More Different" also
                    # excludes compositions already shown earlier in this wizard.
                    if family_a in used_families or family_b in used_families:
                        continue
                    if mash_pair_conflicts_with_seen(a, b, (*prior_profiles, *used_profiles)):
                        continue
                    if self.studio.recommendation_pair_suppressed(a, b):
                        continue
                    plan = guided_plan_from_snapshot(row.get("prepared_plan") or {}, a, b)
                    if plan is None:
                        # Incomplete legacy rows are skipped; the Engine rebuilds
                        # them. Remixer never recalculates them interactively.
                        continue
                    plans.append(plan)
                    used_families.update((family_a, family_b))
                    new_library_families.update((family_a, family_b))
                    normalized_families.update(identity_a)
                    normalized_families.update(identity_b)
                    used_profiles.extend((a, b))
                    if len(plans) >= 6:
                        break
                if len(plans) >= 6 or len(rows) < page_size:
                    break
            progress("Prepared mash ideas ready.", 100)
            return tuple(plans), consumed, tuple(sorted(normalized_families - set(prior_family_keys))), tuple(sorted(new_library_families))

        def success(result) -> None:
            owner = weak_self()
            if owner is None:
                return
            plans, consumed, new_family_keys, new_library_families = result
            if consumed:
                owner._guided_reservoir_offset = offset + consumed
            owner.recommendations = tuple(plans)
            owner.near_matches = ()
            seen = list(getattr(owner, "_guided_seen_profile_paths", ()))
            for plan in owner.recommendations:
                for profile in (plan.source_a, plan.source_b):
                    if profile.path not in seen:
                        seen.append(profile.path)
            owner._guided_seen_profile_paths = seen[-240:]
            owner._guided_seen_family_keys.update(new_family_keys)
            if not hasattr(owner, "_guided_seen_library_family_ids"):
                owner._guided_seen_library_family_ids = set()
            owner._guided_seen_library_family_ids.update(new_library_families)
            if not owner.recommendations:
                owner.scan_status.setText("No additional distinct Guided ideas are available in the current prepared pool.")
                QMessageBox.information(
                    owner,
                    "No More Distinct Guided Ideas",
                    "The current prepared Guided pool has no additional unseen composition families for this session. "
                    "Try Version Fusion for alternate versions, Assisted for broader criteria, or return and choose a different starting point.\n\n"
                    "MIDI Remixer did not start a full-library search.",
                )
                return
            owner.scan_status.setText(f"{len(owner.recommendations)} prepared mash ideas ready.")
            owner._populate_recommendations()
            owner._show_page(1)

        def failure(exc: BaseException) -> None:
            owner = weak_self()
            if owner is None:
                return
            if "cancel" in str(exc).casefold():
                owner.scan_status.setText("Mash-idea retrieval cancelled.")
                return
            error = exc if isinstance(exc, Exception) else RuntimeError(str(exc))
            log_path = write_diagnostic(
                owner.studio.paths.logs / "guided-remix-error.log", "Guided prepared recommendation retrieval", error
            )
            QMessageBox.critical(
                owner, "Guided Remix",
                f"Prepared mash ideas could not be opened. Your library remains safe.\n\n{type(error).__name__}: {error}\n\nDetails: {log_path}",
            )

        started = self.studio.operation_manager.start(
            self._guided_discovery_operation_name, work,
            on_success=success, on_failure=failure, on_progress=self._guided_discovery_progress,
        )
        if not started:
            self.scan_status.setText("Prepared mash ideas are already being opened.")
