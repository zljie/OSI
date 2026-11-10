# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements. See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership. The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License. You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied. See the License for the
# specific language governing permissions and limitations
# under the License.
"""Reproduce and resolve only the reviewed PR 686 document conflicts."""
import json
import os
from pathlib import Path
import re
import runpy
import subprocess
import sys


def git(*args):
    return subprocess.check_output(["git", *args], text=True).strip()


def replace_once(text, old, new):
    if text.count(old) != 1:
        raise SystemExit(f"Unexpected source shape: {old!r}")
    return text.replace(old, new)


def main():
    original, upstream = os.environ["ORIGINAL"], os.environ["UPSTREAM"]
    if git("rev-parse", "HEAD") != original:
        raise SystemExit("Wrong starting commit")
    merged = subprocess.run(["git", "merge", "--no-commit", "--no-ff", upstream], text=True, capture_output=True)
    conflicts = git("diff", "--name-only", "--diff-filter=U").splitlines()
    print(merged.stdout, merged.stderr)
    if merged.returncode not in (0, 1) or not set(conflicts) <= {"core-spec/spec.md", "docs/index.md"}:
        raise SystemExit(f"Unexpected merge failure: {conflicts}")
    if merged.returncode == 1 and not conflicts:
        raise SystemExit("Merge failed without recognized document conflicts")
    evidence = Path(sys.argv[1])
    evidence.parent.mkdir(parents=True, exist_ok=True)
    (evidence.parent / "merge-before-resolution.txt").write_text(merged.stdout + merged.stderr)
    paragraphs = {
        "core-spec/spec.md": ("## Version History\n", "\nFor important merged changes within the unreleased development version,\nincluding rationale and migration impact for consumers, see the\n[significant specification change history](../docs/spec_changes.md).\n"),
        "docs/index.md": ("## Versioning and Compatibility Policy\n", "\nFor a dated record of important merged Core, Ontology, and Relational Interface\nchanges, including rationale and impact on consumers, see the\n[significant specification change history](spec_changes.md). The current\n`0.3.0.dev0` series is unreleased; this history is not a release announcement.\n"),
    }
    for name, (heading, addition) in paragraphs.items():
        # The original PR changes only these navigation paragraphs in these files.
        # Reapply them to upstream rather than restoring obsolete normative text.
        upstream_text = subprocess.check_output(["git", "show", f"{upstream}:{name}"], text=True)
        Path(name).write_text(replace_once(upstream_text, heading, heading + addition))
    history_path = Path("docs/spec_changes.md")
    history = history_path.read_text()
    history = replace_once(history,
        "The entries below belong to **`0.2.0.dev0`, which is unreleased and mutable**.",
        "The current development version is **`0.3.0.dev0`, which is unreleased and mutable**.\nEarlier entries were merged under the former `0.2.0.dev0` development label;\n[#691](https://github.com/apache/ossie/pull/691) renamed the in-development version.\nThere will be no `0.2.0` release.")
    history = replace_once(history, "## `0.2.0.dev0` (unreleased)", "## `0.3.0.dev0` development history (unreleased)")
    row = "| 2026-11-09 | Core / Ontology [#691](https://github.com/apache/ossie/pull/691) | **Move the in-development version to `0.3.0.dev0`.** The next planned release is `0.3.0-incubating`; no `0.2.0` release is planned. | **Breaking for development documents.** Version constants in the current schemas reject `0.2.0.dev0`; producers must declare `0.3.0.dev0` and coordinate schema/package/converter versions. This is a development-version change, not a release announcement. |\n"
    history = replace_once(history, "\n## Semi-automated discovery and upkeep", row + "\n## Semi-automated discovery and upkeep")
    history = replace_once(history,
        "extracts linked PR numbers when present in commit subjects, and lists those",
        "recognizes squash subjects ending in `(#123)` and `Merge pull request #123`\nsubjects, and lists those")
    history_path.write_text(history)
    script = Path("scripts/audit-spec-changes.py")
    old_module = runpy.run_path(str(script))
    missed_merge = old_module["candidate_prs"]("Merge pull request #42 from contributor/spec") == {}
    if not missed_merge:
        raise SystemExit("Audit parser changed; review the current implementation")
    text = script.read_text()
    text = replace_once(text, 'PR_IN_LOG = re.compile(r"\u005c(#(\u005cd+)\u005c)")',
        'PR_IN_LOG = re.compile(r"\u005c(#(\u005cd+)\u005c)$|^Merge pull request #(\u005cd+)\u005cb")')
    text = replace_once(text, "candidates[match.group(1)] = subject", "candidates[match.group(1) or match.group(2)] = subject")
    text = replace_once(text,
        "missing = sorted((number, subject) for number, subject in candidates.items() if number not in listed)",
        "missing = sorted(\n        ((number, subject) for number, subject in candidates.items() if number not in listed),\n        key=lambda item: int(item[0]),\n    )")
    compile(text, str(script), "exec")
    script.write_text(text)
    test_path = Path("scripts/tests/test_audit_spec_changes.py")
    test_path.parent.mkdir(parents=True, exist_ok=True)
    test_path.write_text(Path(sys.argv[2]).read_text())
    allowed = {".github/PULL_REQUEST_TEMPLATE.md", "CONTRIBUTING.md", "core-spec/spec.md", "docs/index.md", "docs/spec_changes.md", "scripts/audit-spec-changes.py", str(test_path)}
    subprocess.run(["git", "add", *sorted(allowed)], check=True)
    subprocess.run(["git", "diff", "--cached", "--check"], check=True)
    if git("diff", "--name-only", "--diff-filter=U"):
        raise SystemExit("Unresolved conflicts remain")
    files = git("diff", "--cached", "--name-only", upstream).splitlines()
    if not set(files) <= allowed:
        raise SystemExit(f"Unexpected candidate changes: {files}")
    for name in ("core-spec/spec.md", "docs/index.md", "docs/spec_changes.md", "CONTRIBUTING.md"):
        for destination in re.findall(r"\]\(([^)]+)\)", Path(name).read_text()):
            if "://" in destination or destination.startswith(("#", "mailto:")):
                continue
            target = destination.split("#", 1)[0]
            if target and not (Path(name).parent / target).exists():
                raise SystemExit(f"Broken relative link: {name}: {destination}")
    record = {"original": original, "upstream": upstream, "tree": git("write-tree"), "files": files,
              "conflicts_reproduced": conflicts, "audit_merge_subject_bug_reproduced": missed_merge}
    evidence.write_text(json.dumps(record, indent=2) + "\n")
    print(json.dumps(record, indent=2))


if __name__ == "__main__":
    main()
