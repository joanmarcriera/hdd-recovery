"""Guard against committing imports of untracked local modules."""
import ast
import subprocess
import unittest

from _loader import REPO_ROOT


def _git_ls_files():
    try:
        result = subprocess.run(
            ["git", "ls-files"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise unittest.SkipTest(f"git ls-files unavailable: {exc}") from exc
    return set(result.stdout.splitlines())


def _serve_sources(tracked):
    return [
        rel for rel in sorted(tracked)
        if rel == "bin/image-serve.py"
        or (rel.startswith("lib/serve_") and rel.endswith(".py"))
    ]


class TestTrackedImports(unittest.TestCase):
    def test_serve_local_lib_imports_are_tracked(self):
        tracked = _git_ls_files()
        problems = []
        for source in _serve_sources(tracked):
            tree = ast.parse((REPO_ROOT / source).read_text(), filename=source)
            for node in ast.walk(tree):
                if not isinstance(node, ast.ImportFrom) or not node.module:
                    continue
                if not node.module.startswith("lib."):
                    continue
                imported = node.module.replace(".", "/") + ".py"
                path = REPO_ROOT / imported
                if not path.exists():
                    problems.append(f"{source}: imports missing local module {node.module}")
                elif imported not in tracked:
                    problems.append(f"{source}: imports untracked local module {imported}")

        self.assertEqual(
            problems,
            [],
            "Serve UI imports must be present in git so Docker builds include them.",
        )


if __name__ == "__main__":
    unittest.main()
