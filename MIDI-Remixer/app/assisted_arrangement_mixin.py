from __future__ import annotations

"""Assisted Studio source intelligence and editable section blueprint UI."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QFrame, QLabel, QPushButton, QVBoxLayout

from core.arrangement_intelligence import (
    ArrangementBlueprint,
    blueprint_with_source_order,
    build_arrangement_blueprint,
    detect_arrangement_conflicts,
    source_intelligence_for_plan,
)


class AssistedWizardArrangementMixin:
    section_blueprint: ArrangementBlueprint | None

    @staticmethod
    def _clear_widget_layout(layout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            child = item.layout()
            if child is not None:
                AssistedWizardArrangementMixin._clear_widget_layout(child)
            if widget is not None:
                widget.deleteLater()

    def _default_blueprint(self) -> ArrangementBlueprint | None:
        if not self.selected_plan:
            return None
        return build_arrangement_blueprint(self.selected_plan, self.engine_plan_key or self.direction_key)

    def _section_source_order(self) -> tuple[int, ...]:
        if not self.selected_plan:
            return ()
        if getattr(self, "section_focus_combos", None):
            return tuple(max(0, combo.currentIndex()) for combo in self.section_focus_combos)
        blueprint = self.section_blueprint or self._default_blueprint()
        return blueprint.source_order if blueprint else ()

    def _current_blueprint(self) -> ArrangementBlueprint | None:
        if not self.selected_plan:
            return None
        base = self.section_blueprint or self._default_blueprint()
        if base is None:
            return None
        order = self._section_source_order()
        return blueprint_with_source_order(base, self.selected_plan, order)

    def _reset_section_blueprint(self, *, invalidate: bool = True) -> None:
        self.section_blueprint = self._default_blueprint()
        self._populate_section_blueprint(invalidate=invalidate)

    def _populate_section_blueprint(self, *, invalidate: bool = False) -> None:
        if not hasattr(self, "section_plan_grid"):
            return
        self._clear_widget_layout(self.section_plan_grid)
        self.section_focus_combos = []
        plan = self.selected_plan
        blueprint = self.section_blueprint or self._default_blueprint()
        if plan is None or blueprint is None:
            empty = QLabel("Choose a recommended song set to build a section plan.")
            empty.setObjectName("Subtitle")
            self.section_plan_grid.addWidget(empty, 0, 0, 1, 4)
            return
        self.section_blueprint = blueprint
        for row, section in enumerate(blueprint.sections):
            name = QLabel(section.name)
            name.setObjectName("StatusPill")
            focus = QComboBox()
            for source_index, profile in enumerate(plan.profiles):
                role = plan.source_roles[source_index]
                focus.addItem(f"{profile.title} — {role}", source_index)
            focus.setCurrentIndex(max(0, min(len(plan.profiles) - 1, section.source_index)))
            focus.currentIndexChanged.connect(self._section_plan_changed)
            purpose = QLabel(section.purpose)
            purpose.setWordWrap(True)
            density = QLabel(section.density)
            density.setObjectName("PlanFacts")
            self.section_plan_grid.addWidget(name, row, 0)
            self.section_plan_grid.addWidget(focus, row, 1)
            self.section_plan_grid.addWidget(purpose, row, 2)
            self.section_plan_grid.addWidget(density, row, 3)
            self.section_focus_combos.append(focus)
        if hasattr(self, "section_plan_summary"):
            self._refresh_section_plan_summary()
        if invalidate:
            self._generation_input_changed()

    def _section_plan_changed(self, *_args) -> None:
        if not self.selected_plan:
            return
        blueprint = self._current_blueprint()
        if blueprint is not None:
            self.section_blueprint = blueprint
        self._refresh_section_plan_summary()
        self._generation_input_changed()

    def _refresh_section_plan_summary(self) -> None:
        if not hasattr(self, "section_plan_summary"):
            return
        blueprint = self._current_blueprint()
        if blueprint is None:
            self.section_plan_summary.setText("No section plan is active.")
            return
        order = " → ".join(section.source_title for section in blueprint.sections)
        self.section_plan_summary.setText(
            "Audible lead path: " + order
            + "\nThe selected recipes use this path for section rotation, melody relay, staged entrances, role reversal, and the finale lead."
        )

    def _blueprint_decision_lines(self) -> tuple[str, ...]:
        blueprint = self._current_blueprint()
        return blueprint.decision_lines() if blueprint else ()

    def _preferred_pair_lead_index(self) -> int:
        order = self._section_source_order()
        if not order:
            return 0
        counts = {0: 0, 1: 0}
        for index in order:
            if index in counts:
                counts[index] += 1
        return 0 if counts[0] >= counts[1] else 1

    def _refresh_arrangement_intelligence(self) -> None:
        if not hasattr(self, "source_intelligence_layout"):
            return
        self._clear_widget_layout(self.source_intelligence_layout)
        plan = self.selected_plan
        if plan is None:
            label = QLabel("Choose a recommended set to inspect source roles and instruments.")
            label.setObjectName("Subtitle")
            self.source_intelligence_layout.addWidget(label, 0, 0)
            return

        for index, source in enumerate(source_intelligence_for_plan(plan)):
            card = QFrame()
            card.setObjectName("Card")
            layout = QVBoxLayout(card)
            layout.setContentsMargins(9, 8, 9, 8)
            title = QLabel(f"{source.title} · {source.suggested_role}")
            title.setObjectName("EnginePlanTitle")
            title.setWordWrap(True)
            instruments = QLabel("Detected instruments: " + ", ".join(source.instruments))
            instruments.setObjectName("PlanFacts")
            instruments.setWordWrap(True)
            summary = QLabel(source.summary)
            summary.setWordWrap(True)
            layout.addWidget(title)
            layout.addWidget(instruments)
            layout.addWidget(summary)
            self.source_intelligence_layout.addWidget(card, index // 2, index % 2)

        conflicts = detect_arrangement_conflicts(plan)
        top = conflicts[:3]
        self.conflict_summary.setText(
            "TOP ARRANGEMENT WARNINGS\n" + "\n".join(
                f"• {item.severity}: {item.title} — {item.mitigation}" for item in top
            )
        )
        details = "\n\n".join(
            f"{item.severity.upper()} · {item.title}\n{item.summary}\nRecommended response: {item.mitigation}"
            for item in conflicts
        )
        self.conflict_details.setText(details)
        self.conflict_details.setVisible(False)
        self.conflict_toggle.setText("Show All Conflict Details ▾")

    def _toggle_conflict_details(self) -> None:
        visible = not self.conflict_details.isVisible()
        self.conflict_details.setVisible(visible)
        self.conflict_toggle.setText("Hide Conflict Details ▴" if visible else "Show All Conflict Details ▾")

    def _apply_section_plan_to_studio(self) -> None:
        blueprint = self._current_blueprint()
        self.studio.assisted_section_source_order = blueprint.source_order if blueprint else ()
        self.studio.assisted_section_labels = tuple(section.name for section in blueprint.sections) if blueprint else ()
        self.studio.assisted_section_decisions = blueprint.decision_lines() if blueprint else ()
