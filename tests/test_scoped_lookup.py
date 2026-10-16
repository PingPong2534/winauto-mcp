"""Explicit toolbar scope preserves uniqueness and strict traversal guards."""
import pathlib
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import input_sim
import uia_inspect as uia


def node(kind='Pane', name='', ident='', children=(), identity=(1,)):
    return SimpleNamespace(element_info=SimpleNamespace(control_type=kind, name=name,
        automation_id=ident, runtime_id=identity), children=Mock(return_value=list(children)))


class ScopedLookupTests(unittest.TestCase):
    def tree(self):
        deep = node('Button', 'New Tab', 'newtab')
        for _ in range(20):
            deep = node(children=[deep])
        document = node('Document', children=[deep])
        button = node('Button', 'New Tab', 'newtab')
        return node(children=[node('ToolBar', children=[button]), document]), button, document

    def test_default_refuses_deep_page_even_when_toolbar_matches(self):
        root, _, _ = self.tree()
        with patch.object(uia, 'root_wrapper', return_value=root), self.assertRaisesRegex(ValueError, 'depth limit'):
            input_sim._find(1, name='New Tab', automation_id='newtab')

    def test_explicit_scope_excludes_document_root_and_never_visits_descendants(self):
        root, button, document = self.tree()
        with patch.object(uia, 'root_wrapper', return_value=root):
            self.assertIs(input_sim._find(1, name='New Tab', automation_id='newtab', scope='non_document'), button)
            with self.assertRaisesRegex(ValueError, 'no matching'):
                input_sim._find(1, name='page-only', scope='non_document')
        document.children.assert_not_called()

    def test_scope_still_refuses_toolbar_duplicates_and_non_document_limits(self):
        root, _, _ = self.tree()
        root.children.return_value.append(node('Button', 'New Tab', 'newtab'))
        with patch.object(uia, 'root_wrapper', return_value=root), self.assertRaisesRegex(ValueError, 'ambiguous'):
            input_sim._find(1, name='New Tab', scope='non_document')
        deep = node()
        for _ in range(20):
            deep = node(children=[deep])
        with self.assertRaisesRegex(ValueError, 'depth limit'):
            list(uia.walk(deep, strict=True, scope='non_document'))
        with self.assertRaisesRegex(ValueError, 'node limit'):
            list(uia.walk(node(children=[node(), node()]), max_nodes=2, strict=True, scope='non_document'))

    def test_unknown_scope_refused(self):
        with self.assertRaisesRegex(ValueError, 'scope'):
            list(uia.walk(node(), scope='first_match'))

    def test_bound_identity_survives_an_unrelated_deep_branch(self):
        root, button, _ = self.tree()
        button.element_info.runtime_id = (42, 7)
        with patch.object(uia, 'root_wrapper', return_value=root):
            self.assertIs(input_sim._find(1, target_id=(42, 7)), button)

    def test_identity_search_skips_incomplete_branches_without_selecting_a_replacement(self):
        root, button, _ = self.tree()
        root.children.return_value.reverse()  # deep unrelated subtree comes first
        button.element_info.runtime_id = (42, 7)
        with patch.object(uia, 'root_wrapper', return_value=root):
            self.assertIs(input_sim._find(1, target_id=(42, 7)), button)
            with self.assertRaisesRegex(ValueError, 'bound control.*not found|bound control disappeared'):
                input_sim._find(1, target_id=(42, 8))

    def test_identity_does_not_bypass_scope_or_conflicting_selectors(self):
        target = node('Edit', 'Duplicate', 'duplicate', identity=(42, 7))
        root = node(children=[node('Document', children=[target])])
        with patch.object(uia, 'root_wrapper', return_value=root):
            self.assertIs(input_sim._find(1, target_id=[42, 7]), target)
            for options in ({'scope': 'non_document'}, {'name': 'wrong'}, {'automation_id': 'wrong'}):
                with self.subTest(options=options), self.assertRaisesRegex(ValueError, 'bound control'):
                    input_sim._find(1, target_id=[42, 7], **options)

    def test_malformed_runtime_id_never_searches_or_mutates(self):
        with patch.object(uia, 'root_wrapper') as root:
            for identity in ([], '42', [True], [1.0], ['1']):
                with self.subTest(identity=identity), self.assertRaisesRegex(ValueError, 'runtime_id'):
                    input_sim._find(1, target_id=identity)
            root.assert_not_called()

    def test_identity_search_still_has_node_and_depth_bounds(self):
        target = node('Button', identity=(42, 7))
        deep = target
        for _ in range(uia.MAX_DEPTH + 1):
            deep = node(children=[deep])
        wide = node(children=[node() for _ in range(uia.MAX_NODES)] + [target])
        for root in (deep, wide):
            with patch.object(uia, 'root_wrapper', return_value=root), self.assertRaisesRegex(ValueError, 'bound control'):
                input_sim._find(1, target_id=[42, 7])


if __name__ == '__main__':
    unittest.main()
