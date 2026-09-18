from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / 'frontend' / 'src'


class FrontendPhase2GovernanceTest(unittest.TestCase):
    def test_required_phase2_modules_exist(self) -> None:
        required = {
            'api/client.ts', 'api/chat.ts', 'api/uploads.ts', 'api/workout.ts',
            'api/health.ts', 'api/records.ts', 'api/plans.ts', 'api/memories.ts',
            'api/settings.ts', 'api/maintenance.ts', 'shared/types.ts',
            'shared/dom.ts', 'shared/formatters.ts', 'shared/sanitize.ts',
            'shared/validation.ts',
        }
        self.assertEqual({p.relative_to(SRC).as_posix() for p in SRC.rglob('*.ts')} & required, required)

    def test_direct_fetch_only_exists_in_client(self) -> None:
        offenders = []
        for path in SRC.rglob('*.ts'):
            if path.name == 'client.ts':
                continue
            if 'fetch(' in path.read_text(encoding='utf-8'):
                offenders.append(path.relative_to(ROOT).as_posix())
        self.assertEqual(offenders, [])

    def test_ci_runs_boundary_and_phase2_security_tests(self) -> None:
        workflow = (ROOT / '.github' / 'workflows' / 'ci.yml').read_text(encoding='utf-8')
        self.assertIn('npm run check:boundaries', workflow)
        self.assertIn('python -m pytest -q', workflow)


if __name__ == '__main__':
    unittest.main()
