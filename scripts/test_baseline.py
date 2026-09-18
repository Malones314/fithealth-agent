"""测试结果基线的记录与比对（main.py 拆分：阶段 0）。

拆分的验收标准是"逐个用例"与基线一致，而不是"总数还是 848"——总数一致完全可能
是一条挂了、另一条新增补上了。所以这里从 JUnit XML 里抽出每个用例的
``classname::name`` 与 outcome（含 skip 原因），落成一份可 diff 的文本。

记录基线（**只在确实要改基线时**）::

    python -m pytest -q --junitxml=.baseline/junit.xml
    python scripts/test_baseline.py --write .baseline/junit.xml

每阶段结束比对::

    python -m pytest -q --junitxml=.baseline/junit.xml
    python scripts/test_baseline.py .baseline/junit.xml

比对规则：**基线里的每个用例都必须还在，且 outcome 不变**。新增用例单独列出但不
算失败——阶段 0 本身就要新增测试基础设施。用例消失、outcome 变化、skip 原因变化
都算失败。
"""

from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
BASELINE_PATH = REPO_ROOT / "tests" / "baseline" / "test_outcomes.txt"


def outcomes_from_junit(xml_path: Path) -> dict[str, str]:
    """``classname::name`` -> outcome。skip 带上原因，否则"悄悄跳过"查不出来。"""
    root = ET.parse(xml_path).getroot()
    results: dict[str, str] = {}
    for case in root.iter("testcase"):
        test_id = f"{case.get('classname', '')}::{case.get('name', '')}"
        outcome = "passed"
        for child in case:
            if child.tag == "failure":
                outcome = "failed"
                break
            if child.tag == "error":
                outcome = "error"
                break
            if child.tag == "skipped":
                reason = (child.get("message") or child.get("type") or "").strip()
                outcome = f"skipped:{reason}" if reason else "skipped"
                break
        if test_id in results:
            raise SystemExit(f"JUnit XML 里有重复用例 id：{test_id}")
        results[test_id] = outcome
    if not results:
        raise SystemExit(f"{xml_path} 里没有任何用例，是不是路径写错了？")
    return results


def render(outcomes: dict[str, str]) -> str:
    return "".join(f"{test_id}\t{outcome}\n" for test_id, outcome in sorted(outcomes.items()))


def load_baseline() -> dict[str, str]:
    if not BASELINE_PATH.is_file():
        raise SystemExit(f"基线不存在：{BASELINE_PATH}；先跑 --write 生成")
    outcomes: dict[str, str] = {}
    for line in BASELINE_PATH.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        test_id, _, outcome = line.partition("\t")
        outcomes[test_id] = outcome
    return outcomes


def compare(current: dict[str, str], baseline: dict[str, str]) -> int:
    missing = sorted(set(baseline) - set(current))
    added = sorted(set(current) - set(baseline))
    changed = sorted(
        f"{test_id}: {baseline[test_id]} -> {current[test_id]}"
        for test_id in set(baseline) & set(current)
        if baseline[test_id] != current[test_id]
    )

    if added:
        print(f"新增 {len(added)} 个用例（不算失败）：")
        for test_id in added:
            print(f"  + {test_id}")
    failures = 0
    if missing:
        failures += 1
        print(f"\n基线里的 {len(missing)} 个用例不见了：")
        for test_id in missing:
            print(f"  - {test_id}")
    if changed:
        failures += 1
        print(f"\n{len(changed)} 个用例的 outcome 变了：")
        for line in changed:
            print(f"  ! {line}")
    if failures:
        return 1
    print(f"\n基线一致：{len(baseline)} 个用例 outcome 未变（当前共 {len(current)} 个）")
    return 0


def main(argv: list[str]) -> int:
    write = "--write" in argv
    paths = [item for item in argv if not item.startswith("--")]
    if len(paths) != 1:
        print(__doc__)
        return 2
    current = outcomes_from_junit(Path(paths[0]))
    if write:
        BASELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
        BASELINE_PATH.write_text(render(current), encoding="utf-8")
        print(f"已写入 {BASELINE_PATH}（{len(current)} 个用例）")
        return 0
    return compare(current, load_baseline())


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
