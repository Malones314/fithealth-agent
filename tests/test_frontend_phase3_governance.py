from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOMAINS = ('session', 'workout', 'uploads', 'chat', 'health', 'checkin', 'data-management')


class FrontendPhase3GovernanceTest(unittest.TestCase):
    def test_each_domain_has_state_controller_view_events_types_and_test(self) -> None:
        source = ROOT / 'frontend' / 'src' / 'domains'
        for domain in DOMAINS:
            with self.subTest(domain=domain):
                directory = source / domain
                for name in ('state.ts', 'controller.ts', 'view.ts', 'events.ts', 'types.ts'):
                    self.assertTrue((directory / name).is_file(), f'{domain}/{name}')
                self.assertTrue(any(directory.glob('*.test.ts')), f'{domain} test')

    def test_default_entry_does_not_load_legacy_script(self) -> None:
        entry = (ROOT / 'frontend' / 'src' / 'main.ts').read_text(encoding='utf-8')
        self.assertNotIn("'/legacy/app.js'", entry)
        self.assertNotIn("'./app/runtime'", entry)
        self.assertFalse((ROOT / 'frontend' / 'src' / 'app' / 'runtime.ts').exists())

    def test_domain_controllers_are_assembled_by_bootstrap(self) -> None:
        bootstrap = (ROOT / 'frontend' / 'src' / 'app' / 'bootstrap.ts').read_text(encoding='utf-8')
        expected = ('Session', 'Workout', 'Uploads', 'Chat', 'Health', 'Checkin', 'DataManagement')
        for name in expected:
            self.assertIn(f'create{name}Controller', bootstrap)

    # 原 test_migration_inventory_has_no_default_legacy_script 在此。它读取
    # frontend/index.html 再做正则匹配，是一条 ARCH-09 源码文本断言。阶段 6 的
    # `npm run check:governance` 已接管回退引用检查，所以不恢复这类源码断言。
    def test_frontend_directories_follow_the_planned_layout(self) -> None:
        source = ROOT / 'frontend' / 'src'
        for relative in ('api', 'app', 'components', 'domains', 'shared', 'styles'):
            with self.subTest(directory=relative):
                self.assertTrue((source / relative).is_dir(), relative)


if __name__ == '__main__':
    unittest.main()
