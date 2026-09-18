from __future__ import annotations

import ast
import unittest
from pathlib import Path

from tests.module_map import IMPLEMENTATION_MODULES
from tests.source_tools import module_source, module_tree


REPO_ROOT = Path(__file__).resolve().parents[1]


class PlanMemoMemorySafetyTest(unittest.TestCase):
    def test_plan_memos_are_not_automatically_promoted_to_memories(self) -> None:
        # 缺席断言必须扫过**全部**实现模块：main.py 拆分之后只看一个文件的话，
        # 这条会静默变成永真（函数搬到 routes/ 里也照样"不存在于 main.py"）。
        for path in IMPLEMENTATION_MODULES:
            with self.subTest(module=path.name):
                source = module_source(path)
                tree = module_tree(path)
                self.assertFalse(any(
                    isinstance(node, ast.FunctionDef) and node.name == "save_plan_memo_memory"
                    for node in tree.body
                ))
                self.assertNotIn("save_plan_memo_memory(", source)


if __name__ == "__main__":
    unittest.main()
