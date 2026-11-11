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
"""Expected PR sets from real Git graphs, not merge-like single-parent subjects."""
import os
from pathlib import Path
import re
import subprocess
import sys

import pytest

SCRIPT = Path(os.environ.get('AUDIT_SCRIPT', Path(__file__).resolve().parents[1] / 'audit-spec-changes.py')).resolve()


class Repository:
    def __init__(self, path):
        self.path = path
        self.env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
        self.env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM='1',
                        GIT_AUTHOR_DATE='2026-11-01T12:00:00+00:00',
                        GIT_COMMITTER_DATE='2026-11-01T12:00:00+00:00', TZ='UTC')
        self.git('init', '-q', '-b', 'main')
        self.git('config', 'user.name', 'Coverage Test')
        self.git('config', 'user.email', 'coverage@example.invalid')
        self.commit('core-spec/spec.md', 'base\n', 'spec baseline (#20)')
        self.base = self.git('rev-parse', 'HEAD').strip()
        (path / 'ontology').mkdir()
        (path / 'docs').mkdir()
        self.history = path / 'docs/spec_changes.md'
        self.history.write_text('| 2026-11-01 | Core [#20](url) | baseline |\n')

    def git(self, *args, ok=True):
        p = subprocess.run(['git', *args], cwd=self.path, env=self.env, text=True, capture_output=True)
        if ok:
            assert p.returncode == 0, p.stdout + p.stderr
        return p.stdout if ok else p

    def commit(self, path, content, subject):
        target = self.path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        self.git('add', path)
        self.git('commit', '-qm', subject)

    def merge(self, branch, number):
        self.git('merge', '-q', '--no-ff', branch, '-m', f'Merge pull request #{number} from tests/{branch}')
        assert len(self.git('show', '-s', '--format=%P', 'HEAD').split()) == 2

    def audit(self, *, subdirectory=False, since='2026-10-01', ref='HEAD', timezone='UTC'):
        before = (self.git('rev-parse', 'HEAD'), self.git('status', '--porcelain'), self.history.read_bytes())
        result = subprocess.run([sys.executable, str(SCRIPT), '--since', since, '--git-ref', ref],
                                cwd=self.path / 'core-spec' if subdirectory else self.path,
                                env=dict(self.env, TZ=timezone), text=True, capture_output=True)
        assert result.returncode == 0, result.stderr
        assert before == (self.git('rev-parse', 'HEAD'), self.git('status', '--porcelain'), self.history.read_bytes())
        assert 'Review suggestions manually' in result.stdout
        return {int(n) for n in re.findall(r'/pull/(\d+) ', result.stdout)}, result.stdout


def build_graph(repo, kind):
    if kind == 'squash':
        repo.git('switch', '-qc', 'feature')
        repo.commit('core-spec/new.md', 'new spec', 'implementation without PR number')
        repo.git('switch', '-q', 'main')
        repo.git('merge', '--squash', 'feature')
        repo.git('commit', '-qm', 'spec squash (#40)')
        return {40}
    if kind == 'ordinary':
        repo.git('switch', '-qc', 'feature')
        repo.commit('core-spec/new.md', 'new spec', 'implementation without PR number')
        repo.git('switch', '-q', 'main')
        repo.merge('feature', 41)
        return {41}
    if kind == 'diverged_main':
        repo.git('switch', '-qc', 'feature')
        repo.commit('ontology/new.md', 'ontology', 'implementation without PR number')
        repo.git('switch', '-q', 'main')
        repo.commit('README.md', 'readme', 'unrelated docs (#99)')
        repo.merge('feature', 42)
        return {42}
    if kind == 'feature_synced_main':
        repo.git('switch', '-qc', 'feature')
        repo.commit('ontology/new.md', 'ontology', 'implementation without PR number')
        repo.git('switch', '-q', 'main')
        repo.commit('core-spec/new.md', 'main spec', 'main spec (#100)')
        repo.git('switch', '-q', 'feature')
        repo.git('merge', '-q', '--no-ff', 'main', '-m', 'Merge branch main into feature')
        repo.git('switch', '-q', 'main')
        repo.commit('README.md', 'readme', 'unrelated docs (#99)')
        repo.merge('feature', 43)
        return {43, 100}
    if kind == 'nested_merges':
        repo.git('switch', '-qc', 'feature')
        repo.git('switch', '-qc', 'nested')
        repo.commit('ontology/new.md', 'ontology', 'implementation without PR number')
        repo.git('switch', '-q', 'feature')
        repo.merge('nested', 44)
        repo.git('switch', '-q', 'main')
        repo.merge('feature', 45)
        return {44, 45}
    if kind == 'conflict_resolution':
        repo.git('switch', '-qc', 'feature')
        repo.commit('core-spec/spec.md', 'feature\n', 'implementation without PR number')
        repo.git('switch', '-q', 'main')
        repo.commit('core-spec/spec.md', 'main\n', 'main spec (#101)')
        result = repo.git('merge', '--no-ff', 'feature', '-m', 'temporary merge', ok=False)
        assert result.returncode == 1 and 'CONFLICT' in result.stdout
        repo.commit('core-spec/spec.md', 'main and feature\n', 'Merge pull request #46 from tests/feature')
        assert len(repo.git('show', '-s', '--format=%P', 'HEAD').split()) == 2
        return {46, 101}
    if kind == 'unrelated_merge':
        repo.git('switch', '-qc', 'feature')
        repo.commit('README.md', 'readme', 'unrelated docs')
        repo.git('switch', '-q', 'main')
        repo.commit('core-spec/new.md', 'main spec', 'main spec (#100)')
        repo.merge('feature', 47)
        return {100}
    if kind == 'missing_number':
        repo.git('switch', '-qc', 'feature')
        repo.commit('core-spec/new.md', 'spec', 'references issue #777')
        repo.git('switch', '-q', 'main')
        repo.git('merge', '--no-ff', 'feature', '-m', 'Merge branch feature')
        return set()
    raise AssertionError(kind)


@pytest.mark.parametrize('kind', ['squash', 'ordinary', 'diverged_main', 'feature_synced_main',
                                  'nested_merges', 'conflict_resolution', 'unrelated_merge', 'missing_number'])
@pytest.mark.parametrize('subdirectory', [False, True], ids=['root', 'subdirectory'])
def test_expected_pr_sets_from_actual_commit_graphs(tmp_path, kind, subdirectory):
    repo = Repository(tmp_path)
    expected = build_graph(repo, kind)
    found, stdout = repo.audit(subdirectory=subdirectory)
    assert found == expected, f'{kind}: expected {expected}, got {found}\n{stdout}\n{repo.git("log", "--graph", "--all", "--oneline")}'


@pytest.mark.parametrize('timezone', ['UTC', 'Pacific/Honolulu', 'Asia/Tokyo'])
def test_since_midnight_is_inclusive_and_timezone_independent(tmp_path, timezone):
    repo = Repository(tmp_path)
    for number, instant in [(50, '2026-11-01T23:59:59+00:00'), (51, '2026-11-02T00:00:00+00:00'), (52, '2026-11-02T00:00:01+00:00')]:
        repo.env.update(GIT_AUTHOR_DATE=instant, GIT_COMMITTER_DATE=instant)
        repo.commit(f'core-spec/{number}.md', str(number), f'spec boundary (#{number})')
    found, stdout = repo.audit(since='2026-11-02', timezone=timezone)
    assert found == {51, 52}, stdout


def test_recorded_ordinary_merge_is_excluded_and_explicit_ref_is_honored(tmp_path):
    repo = Repository(tmp_path)
    build_graph(repo, 'ordinary')
    assert repo.audit(ref=repo.base)[0] == set()
    repo.history.write_text(repo.history.read_text() + '| 2026-11-01 | Core [#41](url) | recorded merge |\n')
    assert repo.audit()[0] == set()


def test_duplicate_squash_numbers_are_deduplicated_in_numeric_order(tmp_path):
    repo = Repository(tmp_path)
    repo.commit('core-spec/a.md', 'a', 'spec A (#100)')
    repo.commit('core-spec/b.md', 'b', 'spec B (#9)')
    repo.commit('ontology/c.md', 'c', 'same source (#100)')
    found, stdout = repo.audit()
    assert found == {9, 100}
    assert stdout.count('/pull/100 ') == 1
    assert stdout.index('/pull/9 ') < stdout.index('/pull/100 ')
