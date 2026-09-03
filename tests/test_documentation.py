"""Public documentation pairing, language routing and local-link regressions."""
from pathlib import Path
import re
import unittest
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
NAVIGATION = {
    "README.md", "docs/README.md", "docs/guide.md", "example_workflows/README.md",
    "sidecar/README.md", "sidecar/vendor/nvof/README.md",
}


def documents():
    paths = set(ROOT.glob("README*.md")) | set((ROOT / "sidecar").glob("README*.md"))
    for directory in ("docs", "example_workflows", "sidecar/protocol", "sidecar/vendor/nvof"):
        paths.update((ROOT / directory).rglob("*.md"))
    return sorted(paths)


def anchors(text):
    found = set(re.findall(r'<a\s+id="([^"]+)"', text))
    counts = {}
    for title in re.findall(r"^#{1,6} (.+)$", text, re.MULTILINE):
        slug = re.sub(r"[^\w\- ]", "", title.lower()).replace(" ", "-")
        count = counts.get(slug, 0)
        counts[slug] = count + 1
        found.add(slug + (f"-{count}" if count else ""))
    return found


class DocumentationTests(unittest.TestCase):
    def test_substantive_documents_are_paired(self):
        for path in documents():
            relative = path.relative_to(ROOT).as_posix()
            text = path.read_text(encoding="utf-8")
            if relative in NAVIGATION:
                self.assertLess(len(text.splitlines()), 20, relative)
                self.assertIn(".en.md", text)
                self.assertIn(".zh-CN.md", text)
                continue
            self.assertRegex(path.name, r"\.(en|zh-CN)\.md$", relative)
            other = (path.name.replace(".en.md", ".zh-CN.md") if path.name.endswith(".en.md")
                     else path.name.replace(".zh-CN.md", ".en.md"))
            self.assertTrue(path.with_name(other).is_file(), relative)
            self.assertIn("Audience: public", text)
            self.assertIn(f"]({other})", "\n".join(text.splitlines()[:5]))
            self.assertNotIn("\x00", text)
            self.assertEqual(sum(line.startswith("```") for line in text.splitlines()) % 2, 0, relative)

    def test_links_exist_and_body_keeps_selected_language(self):
        for path in documents():
            text = path.read_text(encoding="utf-8")
            opposite = ".zh-CN.md" if path.name.endswith(".en.md") else ".en.md"
            for number, line in enumerate(text.splitlines(), 1):
                for raw in re.findall(r"\[[^\]]*\]\(([^)]+)\)", line):
                    url = urlsplit(raw.strip("<>"))
                    if url.scheme or url.netloc:
                        continue
                    target = (path.parent / unquote(url.path)).resolve() if url.path else path
                    location = f"{path.relative_to(ROOT)}:{number}: {raw}"
                    self.assertTrue(target.exists(), location)
                    if url.fragment and target.suffix == ".md":
                        self.assertIn(unquote(url.fragment), anchors(target.read_text(encoding="utf-8")), location)
                    if path.relative_to(ROOT).as_posix() not in NAVIGATION and number > 5:
                        self.assertFalse(url.path.endswith(opposite), location)
                        if target in documents():
                            self.assertRegex(target.name, r"\.(en|zh-CN)\.md$", location)
