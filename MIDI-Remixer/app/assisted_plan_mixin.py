from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING
import traceback

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QComboBox, QDialog, QFrame, QGridLayout,
    QGroupBox, QHBoxLayout, QLabel, QMessageBox, QPlainTextEdit, QProgressBar,
    QPushButton, QRadioButton, QScrollArea, QSizePolicy, QSlider, QSpinBox,
    QStackedWidget, QVBoxLayout, QWidget,
)

PAIR_PREFIX = "pair:"
MULTI_PREFIX = "multi:"

ENGINE_PLAN_PRESETS = (
    {
        "key": "balanced",
        "title": "Balanced Plan",
        "best_for": "Best first comparison",
        "description": "Keeps the shared pulse and each song's character, then generates the safest broad mix of arrangements.",
        "energy": "Balanced target pulse",
        "harmony": "Preserve each song's character",
    },
    {
        "key": "melody",
        "title": "Melody Spotlight",
        "best_for": "Best when one hook should lead",
        "description": "Prioritizes melody-over-backing and melody-relay ideas, with automatic repair only where a clash is severe.",
        "energy": "Balanced target pulse",
        "harmony": "Adapt severe clashes",
    },
    {
        "key": "sections",
        "title": "Section Journey",
        "best_for": "Best when simultaneous layering is risky",
        "description": "Uses cleaner section exchanges and transformations so each source can keep more of its own harmony.",
        "energy": "More relaxed",
        "harmony": "Prefer section-safe arrangements",
    },
    {
        "key": "collage",
        "title": "Build & Finale",
        "best_for": "Best for a rising multi-source arrangement",
        "description": "Uses a more energetic frame and prioritizes layered, progressive, and finale-style recipe families.",
        "energy": "More energetic",
        "harmony": "Prefer section-safe arrangements",
    },
)
from core.operation_diagnostics import write_diagnostic
from core.arrangement_intelligence import build_arrangement_blueprint
from core.assisted_studio import (
    AssistedSetPlan, assisted_library_summary, assisted_score_label,
    assisted_set_recommendations,
)
from core.multi_source import SourceSpec
from core.remix_profile import key_name
from core.remix_engine import take_identity_key
from app.controllers.workflow_state import WorkflowBranchKey
from app.theme import load_stylesheet
from app.button_policy import install_button_policy, set_action_enabled

from app.assisted_pages_mixin import AssistedWizardPagesMixin

if TYPE_CHECKING:
    from app.decision_workflow import DecisionWorkflowStudio
    from core.remix_engine import RemixTake




class AssistedWizardPlanMixin:
    @staticmethod
    def _engine_plan_preset(key: str) -> dict[str, str]:
        return next((preset for preset in ENGINE_PLAN_PRESETS if preset["key"] == key), ENGINE_PLAN_PRESETS[0])

    @staticmethod
    def _recipe_display(keys: tuple[str, ...]) -> str:
        return ", ".join(key.replace("-", " ") for key in keys) or "No eligible recipe"

    def _recipes_for_direction(self, direction_key: str) -> tuple[str, ...]:
        plan = self.selected_plan
        if not plan:
            return ()
        available = list(plan.recipe_keys)
        if direction_key == "balanced":
            return tuple(available[: min(3, len(available))])
        if plan.source_count == 2:
            lead_recipe = "melody-a-accompaniment-b" if self._preferred_pair_lead_index() == 0 else "melody-b-accompaniment-a"
            other_recipe = "melody-b-accompaniment-a" if lead_recipe.endswith("-b") else "melody-a-accompaniment-b"
            families = {
                "melody": (lead_recipe, "conversation", "foundation-swap", other_recipe),
                "sections": ("section-exchange", "transformation"),
                "collage": ("layered-blend", "conversation", "foundation-swap"),
            }
        else:
            families = {
                "melody": ("melody-relay", "anchor-transformations"),
                "sections": ("rotating-sections", "source-journey"),
                "collage": ("progressive-build", "role-collage", "finale-stack"),
            }
        preferred = [key for key in families.get(direction_key, ()) if key in available]
        return tuple(preferred[:3] or available[:2])

    def _selected_recipes(self) -> tuple[str, ...]:
        return self._recipes_for_direction(self.direction_key)

    def _resolved_frame_for(
        self,
        energy: str,
        harmony: str,
        length_text: str,
    ) -> tuple[int, int | None, str, int, int, str, bool]:
        plan = self.selected_plan
        if not plan:
            return 110, None, "major", 64, 8, "Preserve source mode", True
        bpm = plan.target_bpm
        if energy == "More relaxed":
            bpm = max(40, bpm - 12)
        elif energy == "More energetic":
            bpm = min(240, bpm + 12)
        elif energy in {"Use first source pulse", "Use first song's pulse"}:
            bpm = int(round(float(plan.profiles[0].felt_bpm or bpm)))
        if length_text == "64-bar comparison":
            bars = 64
        elif length_text == "96-bar development":
            bars = 96
        else:
            bars = max(32, min(256, min(int(profile.bars) for profile in plan.profiles)))
        mode_handling = "Adapt diatonic notes to target mode" if harmony == "Adapt severe clashes" else "Preserve source mode"
        return bpm, plan.target_key_pc, plan.target_mode, bars, plan.section_bars, mode_handling, True

    def _resolved_frame(self) -> tuple[int, int | None, str, int, int, str, bool]:
        return self._resolved_frame_for(
            self.energy_combo.currentText(),
            self.harmony_combo.currentText(),
            self.length_combo.currentText(),
        )

    def _plan_snapshot(
        self,
        *,
        direction_key: str | None = None,
        energy: str | None = None,
        harmony: str | None = None,
    ) -> dict[str, object]:
        if not self.selected_plan:
            return {}
        direction_key = direction_key or self.direction_key
        energy = energy or self.energy_combo.currentText()
        harmony = harmony or self.harmony_combo.currentText()
        bpm, target_pc, target_mode, bars, section_bars, mode_handling, auto_balance = self._resolved_frame_for(
            energy,
            harmony,
            self.length_combo.currentText(),
        )
        preset = self._engine_plan_preset(direction_key)
        blueprint = build_arrangement_blueprint(self.selected_plan, direction_key)
        return {
            "key": direction_key,
            "name": preset["title"],
            "direction": preset["title"],
            "energy": energy,
            "harmony_choice": harmony,
            "harmony": mode_handling,
            "bpm": bpm,
            "target_key": key_name(target_pc, target_mode),
            "bars": bars,
            "section_bars": section_bars,
            "recipes": self._recipes_for_direction(direction_key),
            "section_order": blueprint.source_order,
            "section_titles": tuple(section.source_title for section in blueprint.sections),
            "signature": self.signature_combo.currentText(),
            "auto_balance": auto_balance,
        }

    @staticmethod
    def _snapshot_changes(before: dict[str, object], after: dict[str, object]) -> list[str]:
        if not before or not after:
            return []
        changes: list[str] = []
        comparisons = (
            ("direction", "Direction"),
            ("bpm", "Target pulse"),
            ("harmony_choice", "Harmony"),
            ("bars", "Length"),
        )
        for key, label in comparisons:
            if before.get(key) != after.get(key):
                suffix = " BPM" if key == "bpm" else (" bars" if key == "bars" else "")
                changes.append(f"{label}: {before.get(key)}{suffix} → {after.get(key)}{suffix}")
        if before.get("recipes") != after.get("recipes"):
            old = AssistedStudioWizard._recipe_display(tuple(before.get("recipes") or ()))
            new = AssistedStudioWizard._recipe_display(tuple(after.get("recipes") or ()))
            changes.append(f"Recipes: {old} → {new}")
        if before.get("section_titles") != after.get("section_titles"):
            old_path = " → ".join(str(item) for item in tuple(before.get("section_titles") or ())) or "recipe default"
            new_path = " → ".join(str(item) for item in tuple(after.get("section_titles") or ())) or "recipe default"
            changes.append(f"Section lead path: {old_path} → {new_path}")
        return changes

    def _toggle_plan_details(self) -> None:
        visible = not self.plan_text.isVisible()
        self.plan_text.setVisible(visible)
        self.plan_detail_toggle.setText("Hide Full Technical Plan ▴" if visible else "Show Full Technical Plan ▾")

    def _populate_engine_plan_choices(self) -> None:
        self._clear_layout(self.plan_choice_grid)
        current = self._plan_snapshot()
        for index, preset in enumerate(ENGINE_PLAN_PRESETS):
            key = preset["key"]
            preview = self._plan_snapshot(
                direction_key=key,
                energy=preset["energy"],
                harmony=preset["harmony"],
            )
            changes = self._snapshot_changes(current, preview)
            selected = key == self.engine_plan_key and key == self.direction_key and not changes
            card = QFrame()
            card.setObjectName("EnginePlanCard")
            card.setProperty("selected", selected)
            layout = QVBoxLayout(card)
            layout.setContentsMargins(10, 9, 10, 9)
            title = QLabel(preset["title"] + (" · SELECTED" if selected else ""))
            title.setObjectName("EnginePlanTitle")
            title.setWordWrap(True)
            best_for = QLabel(preset["best_for"])
            best_for.setObjectName("StatusPill")
            best_for.setWordWrap(True)
            description = QLabel(preset["description"])
            description.setWordWrap(True)
            facts = QLabel(
                f"Target: {preview.get('target_key')} at {preview.get('bpm')} felt BPM\n"
                f"Harmony: {preview.get('harmony_choice')}\n"
                f"Recipes: {self._recipe_display(tuple(preview.get('recipes') or ()))}"
            )
            facts.setObjectName("PlanFacts")
            facts.setWordWrap(True)
            difference = QLabel(
                "Current plan — no changes" if not changes else "Changes from current:\n• " + "\n• ".join(changes[:4])
            )
            difference.setObjectName("PlanDifference")
            difference.setWordWrap(True)
            choose = QPushButton("Selected ✓" if selected else "Use This Plan")
            choose.setObjectName("PrimaryButton" if not selected else "SuccessButton")
            choose.setEnabled(not selected)
            choose.clicked.connect(lambda _checked=False, plan_key=key: self._apply_engine_plan_preset(plan_key))
            layout.addWidget(title)
            layout.addWidget(best_for)
            layout.addWidget(description)
            layout.addWidget(facts)
            layout.addWidget(difference)
            layout.addStretch(1)
            layout.addWidget(choose, 0, Qt.AlignmentFlag.AlignLeft)
            self.plan_choice_grid.addWidget(card, index // 2, index % 2)
        self.plan_choice_grid.setColumnStretch(0, 1)
        self.plan_choice_grid.setColumnStretch(1, 1)

    def _apply_engine_plan_preset(self, key: str) -> None:
        preset = self._engine_plan_preset(key)
        before = self._plan_snapshot()
        self._suspend_branch_invalidation = True
        try:
            self.engine_plan_key = key
            self._choose_direction(key)
            energy_index = self.energy_combo.findText(preset["energy"])
            if energy_index >= 0:
                self.energy_combo.setCurrentIndex(energy_index)
            harmony_index = self.harmony_combo.findText(preset["harmony"])
            if harmony_index >= 0:
                self.harmony_combo.setCurrentIndex(harmony_index)
        finally:
            self._suspend_branch_invalidation = False
        after = self._plan_snapshot()
        if before != after and (self.generated_takes or self.selected_take is not None):
            self._invalidate_active_branch()
        self._reset_section_blueprint(invalidate=False)
        self._render_plan_review(previous_snapshot=before)

    def _render_plan_review(self, *, previous_snapshot: dict[str, object] | None = None) -> None:
        plan = self.selected_plan
        if not plan:
            return
        snapshot = self._plan_snapshot()
        bpm = int(snapshot["bpm"])
        bars = int(snapshot["bars"])
        section_bars = int(snapshot["section_bars"])
        recipes = tuple(snapshot["recipes"])
        auto_balance = bool(snapshot["auto_balance"])
        lines = [
            "WHAT MIDI REMIXER FILLED IN",
            f"• Songs: {', '.join(plan.source_titles)}",
            f"• Selected engine plan: {snapshot['name']}",
            f"• Target key: {snapshot['target_key']}",
            f"• Target pulse: {bpm} felt BPM",
            f"• Working length: {bars} bars in {section_bars}-bar blocks",
            f"• Harmony treatment: {snapshot['harmony']}",
            f"• Signature treatment: {snapshot['signature']}",
            f"• Broad dynamics normalization: {'on' if auto_balance else 'off'}",
            f"• Recipes to generate: {self._recipe_display(recipes)}",
            f"• Variations per recipe: {self.variation_spin.value()}",
            "",
            "SOURCE ROLES",
        ]
        lines.extend(f"• {profile.title}: {role}" for profile, role in zip(plan.profiles, plan.source_roles))
        lines.extend(("", "SOURCE CONTRIBUTIONS"))
        lines.extend(f"• {item}" for item in plan.contribution_plan)
        lines.extend(("", "SECTION LEAD PLAN"))
        lines.extend(f"• {item}" for item in self._blueprint_decision_lines())
        lines.extend(("", "SIGNATURE MATERIAL"))
        lines.extend(f"• {item}" for item in plan.signature_plan)
        lines.extend(("", "WHY THIS PLAN"))
        lines.extend(f"• {item}" for item in plan.reasons)
        if plan.warnings:
            lines.extend(("", "WHAT NEEDS REVIEW"))
            lines.extend(f"• {item}" for item in plan.warnings)
        lines.extend((
            "",
            "WHAT MIDI REMIXER WILL DO",
            "• Transpose generated pitched parts non-destructively into the displayed target frame.",
            "• Align every selected song to the displayed felt pulse while preserving the original MIDI files.",
            "• Apply role-appropriate dynamics normalization before the final mixer.",
            "• Skip an unavailable recipe without canceling the rest of the Trial.",
            "• Keep every generated Trial so Back/Redo and comparison remain possible.",
        ))
        self.plan_title.setText(f"{snapshot['name']} — {plan.headline}")
        self.plan_summary.setText(
            f"YOU WILL HEAR\n"
            f"• {snapshot['name']}: {self._engine_plan_preset(str(snapshot['key']))['description']}\n"
            f"• {snapshot['target_key']} at {bpm} felt BPM for {bars} bars\n"
            f"• {self._recipe_display(recipes)}\n"
            f"• Source roles: " + "; ".join(
                f"{profile.title} — {role}" for profile, role in zip(plan.profiles, plan.source_roles)
            )
            + "\n• Section lead path: " + " → ".join(
                section.source_title for section in (self._current_blueprint().sections if self._current_blueprint() else ())
            )
        )
        changes = self._snapshot_changes(previous_snapshot or {}, snapshot)
        if changes:
            self.plan_change_summary.setText("PLAN CHANGED\n• " + "\n• ".join(changes))
            self.plan_change_summary.setVisible(True)
        else:
            self.plan_change_summary.setVisible(False)
        self.plan_text.setPlainText("\n".join(lines))
        self._populate_engine_plan_choices()
        self._populate_section_blueprint(invalidate=False)
        self._populate_plan_source_buttons()
        self._last_plan_snapshot = snapshot

    def _review_plan(self) -> None:
        if not self.selected_plan:
            return
        self.engine_plan_key = self.direction_key
        if self.section_blueprint is None or self.section_blueprint.preset_key != self.engine_plan_key:
            self._reset_section_blueprint(invalidate=False)
        self._render_plan_review()
        self._show_page(3)

    def _populate_plan_source_buttons(self) -> None:
        self._clear_layout(self.plan_source_buttons)
        if not self.selected_plan:
            return
        for source_index, profile in enumerate(self.selected_plan.profiles):
            button = QPushButton(f"Play Source {source_index + 1} Raw")
            button.setObjectName("PlayButton")
            button.setToolTip(profile.title)
            button.clicked.connect(lambda _checked=False, p=profile: self._play_profile(p))
            self.plan_source_buttons.addWidget(button)
        self.plan_source_buttons.addStretch(1)
