from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from src.roles.registry import RoleRegistryError, load_roles


class RolesTest(unittest.TestCase):
    def test_load_roles_literal_prompt(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp)
            (path / "planner.yaml").write_text(
                "prompt: |\n"
                "  First line.\n"
                "  Second line.\n",
                encoding="utf-8",
            )
            roles = load_roles(path)

        self.assertEqual(roles["planner"].prompt, "First line.\nSecond line.")

    def test_missing_prompt_raises(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp)
            (path / "planner.yaml").write_text("", encoding="utf-8")
            with self.assertRaises(RoleRegistryError):
                load_roles(path)

    def test_load_roles_from_legacy_single_file(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "roles.yaml"
            path.write_text(
                "planner:\n"
                "  prompt: 'Plan carefully.'\n",
                encoding="utf-8",
            )
            roles = load_roles(path)

        self.assertEqual(roles["planner"].prompt, "Plan carefully.")


if __name__ == "__main__":
    unittest.main()
