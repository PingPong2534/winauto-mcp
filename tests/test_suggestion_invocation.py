"""Suggestion invocation must commit instead of merely selecting a row."""
import pathlib
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import input_sim
import window_manager


class SuggestionInvocationTest(unittest.TestCase):
    def activate(self, kind, has_invoke=True):
        selection, invoke, toggle = Mock(), Mock(), Mock()
        row = SimpleNamespace(element_info=SimpleNamespace(control_type=kind),
                              is_enabled=lambda: True, invoke=invoke)
        patterns = {"selection_item": selection, "invoke": invoke if has_invoke else None,
                    "toggle": toggle if kind == "CheckBox" else None}
        with patch.object(input_sim, "_interaction_rect", return_value=None), \
             patch.object(input_sim.uia, "pattern", side_effect=lambda w, p: patterns.get(p)):
            action = input_sim._activate(1, row)
        return action, selection, invoke, toggle

    def test_invokable_suggestion_commits_instead_of_only_selecting(self):
        action, selection, invoke, _ = self.activate("ListItem")
        self.assertEqual("invoked", action)
        invoke.assert_called_once_with()
        selection.Select.assert_not_called()

    def test_selection_only_row_and_tab_keep_selection(self):
        for kind, has_invoke in (("ListItem", False), ("TabItem", True)):
            with self.subTest(kind=kind):
                action, selection, invoke, _ = self.activate(kind, has_invoke)
                self.assertEqual("selected", action)
                selection.Select.assert_called_once_with()
                invoke.assert_not_called()

    def test_checkbox_keeps_toggle(self):
        action, _, invoke, toggle = self.activate("CheckBox")
        self.assertEqual("toggled", action)
        toggle.Toggle.assert_called_once_with()
        invoke.assert_not_called()

    def test_non_actionable_container_does_not_hide_expander(self):
        """A ListItem without activation patterns must not intercept its expander."""
        def wrapper(kind, bounds):
            return SimpleNamespace(element_info=SimpleNamespace(control_type=kind),
                rectangle=lambda: SimpleNamespace(left=bounds[0], top=bounds[1],
                    right=bounds[2], bottom=bounds[3]))
        expander = wrapper("Button", (110, 110, 310, 170))
        container = wrapper("ListItem", (111, 111, 309, 169))
        with patch.object(input_sim.uia, "root_wrapper"), \
             patch.object(input_sim.uia, "walk", return_value=iter([expander, container])), \
             patch.object(input_sim.uia, "pattern", side_effect=lambda w, p:
                          object() if w is expander and p == "expand_collapse" else None), \
             patch.object(input_sim, "get_client_size", return_value=(300, 300)), \
             patch.object(window_manager, "get_client_rect_screen", return_value=(100, 100, 400, 400)):
            self.assertIs(expander, input_sim._at_point(1, 30, 30, interactive=True))

    def test_equal_bounds_choose_child_expander_over_selection_container(self):
        """Preorder parent/child peers share bounds; activate the nested control."""
        def wrapper(kind):
            return SimpleNamespace(element_info=SimpleNamespace(control_type=kind),
                rectangle=lambda: SimpleNamespace(left=110, top=110, right=310, bottom=170))
        container, expander = wrapper("ListItem"), wrapper("Button")
        with patch.object(input_sim.uia, "root_wrapper"), \
             patch.object(input_sim.uia, "walk", return_value=iter([container, expander])), \
             patch.object(input_sim.uia, "pattern", side_effect=lambda w, p:
                          object() if w is container and p == "selection_item" else None), \
             patch.object(input_sim, "get_client_size", return_value=(300, 300)), \
             patch.object(window_manager, "get_client_rect_screen", return_value=(100, 100, 400, 400)):
            self.assertIs(expander, input_sim._at_point(1, 30, 30, interactive=True))


if __name__ == "__main__":
    unittest.main()
