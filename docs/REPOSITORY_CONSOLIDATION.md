# QCH Repository Consolidation

**Status:** the local branch `consolidate/qch-canonical` has been prepared (2026-09-27). Nothing has been
pushed, no repository has been renamed, archived or deleted, and no GitHub or Streamlit setting has been
changed.

**Goal:** one long-term QCH repository that keeps the full Git history of both repositories and contains
the QCH database, the query system, the demo app, the documentation and the tests.

---

## 1. Inventory

### 1.1 Repositories

| | `QuantumCircuit-Hub/quantum-circuit-hub` ("hub") | `QuantumCircuit-Hub/quantum-circuit-demo01` ("demo") |
|---|---|---|
| Created / last push | 2026-09-08 / 2026-09-09 | 2026-09-11 / 2026-09-26 |
| Size | 17 KB | 2,995 KB |
| Branches | `main` `eb8b18f` | `main` `6ff7788`, `ui-improvement` `c605445` |
| Tags | – | `v0.1-demo` (annotated; points to commit `00993b8`) |
| Commits (all refs) | 4 | 10 |
| Authors in history | JiahengLu-CS / Jiaheng `<jiahenglu@gmail.com>` | JiahengLu-CS `<jiahenglu@gmail.com>` (3 commits); Jiaheng Lu `<machine-local address>` (7 commits) |
| PRs / issues | none / none | PR #1 and #2 (both merged by JiahengLu-CS); no issues |
| Forks / stars | 0 / 0 | 0 / 0 |
| License | Apache-2.0 (`LICENSE`) | **none** |
| Content | `LICENSE`, `README.md` (vision), `.gitignore` (generic Python), `docs/qch-core-conceptual-schema-v0.1.md`, and `.gitkeep` files in 6 directories | Streamlit app (`app.py`, `pages/`), `src/qch/` (114 files), `data/` (111 files, including the 4.2 MB QCH store `data/qch_ecdsa_p17.sqlite3`), 12 scripts, 10 tests, `.streamlit/`, `.devcontainer/` |
| CI / Pages / deployments | none / none / none | none / none / none (GitHub API) |

**No student contributions were found.** Every commit in every branch, tag and PR ref of both
repositories is by one person, under two identities. Neither repository has forks. If students work in
forks, in other repositories or in local clones not yet pushed, identify that work before anything is
archived (see §6, decision D1).

### 1.2 Local clone (`C:\Users\jilu\Project\QCDB\Demo01\quantum-circuit-hub`, origin = demo)

**Branches:**
- `main` = `e9b9af1`. It is stale: it lacks `3eaf2b2` (dev container) and `6ff7788` (merge of PR #2).
  It was not modified.
- `ui-improvement` = `c605445` = `origin/ui-improvement`, so it is fully pushed.

**Work that existed only locally before this branch** (179 paths, backed up in §5.1):
- 53 test files and `pytest.ini`;
- 49 scripts, 6 examples and 5 audits;
- 49 docs;
- the research-supplement code (migrations 006/007, importer, service);
- small README and `requirements-dev.txt` additions;
- a modified `data/qch_ecdsa_p17.sqlite3`.

The published `origin` already had the source code, but **most of the test suite, the scripts and the
docs had never been committed.**

**Git-ignored and local-only (not migrated, by design):**
- `data/external/`: third-party raw snapshots, some containing personal data or no license;
- `data/backups/`, `results/`, `.venv/`, `data/*.sqlite3.artifacts/`;
- `examples/my-qch.sqlite3`.

**Stray untracked file:** `h origin ui-improvement` (8 KB). It holds saved `git show c605445` output,
probably from a mistyped push command. It is left untouched and was not committed.

### 1.3 Dependencies and deployment settings

| Item | hub | demo / branch |
|---|---|---|
| `requirements.txt` (deploy) | – | streamlit 1.63.0, networkx 3.6.1, pandas 3.0.5, requests 2.34.2 |
| `requirements-dev.txt` | – | qiskit 2.5.2, mqt-bench, pytest …; the branch adds `zstandard==0.25.0` (optional, DB-3 ops reader only) |
| `.streamlit/config.toml` | – | `showSidebarNavigation = false` |
| `.devcontainer/devcontainer.json` | – | Python 3.11 image; runs `streamlit run app.py --server.enableCORS false --server.enableXsrfProtection false` (Codespaces preview) |
| Streamlit Community Cloud | – | The app binding (repo, branch, main file, secrets such as `QCH_LLM_ENABLED`) is **not visible from Git**; check it in the Streamlit dashboard before renaming (D4) |
| Runtime DB access | – | `pages/Ask_QCH.py` opens the store with `QCH.open`, which applies pending migrations on first open |

### 1.4 Licenses and data terms

- **Code.** The hub has Apache-2.0; the demo had no license. After the merge, `LICENSE` (Apache-2.0)
  covers the combined repository's code (D2).
- **Bundled data with its own terms:**
  - ECDSA.Fail submission data and metrics in `data/` and the QCH store. The upstream repository's
    `NOTICE` covers reused Rust files (CC BY 4.0), not the submission API data.
  - MQT Bench-derived circuits under `data/mqtbench/`: MQT Bench is MIT-licensed.
- **Research-supplement rows** (schema v6/v7: Note C labels and the paper populations) come from
  `jieyilong/ecdsafail-supplemental-materials`, **which declares no license**. They exist only in the
  local working-copy DB. The DB committed on the branch is still the already-public schema-v5 store
  (D3).

---

## 2. Recommendation

**Make `quantum-circuit-demo01` the canonical repository and rename it `quantum-circuit-hub`. First
rename the nearly empty hub repository out of the way and archive it.**

Reasons:
- The demo holds all code, data, PR history, the release tag and the deployment binding.
- The hub holds 4 commits (a README, a license, one design document) and nothing else.
- Merging the hub's history into the demo (`--allow-unrelated-histories`, as done on
  `consolidate/qch-canonical`) keeps both histories intact without a force-push.
- GitHub redirects the demo's old URL after the rename, so existing clones and links keep working.

The alternative, pushing the demo history into the hub repository, would leave PRs #1/#2 and the tag
in the old repository, and would still require re-pointing the Streamlit app.

---

## 3. Migration map

| Path(s) in the consolidated branch | Origin | Action |
|---|---|---|
| Entire demo tree (`app.py`, `pages/`, `src/`, `data/`, `.streamlit/`, `.devcontainer/`, requirements, tests, scripts) | demo `main` `6ff7788` | kept as is (fast-forward from `c605445`) |
| `src/qch/importers/ecdsafail_research_supplement.py`, `src/qch/services/research.py`, migrations `006_*.sql` and `007_*.sql`, edits to `hub.py`, `models.py`, `repositories/interfaces.py`, `services/__init__.py`, `storage/sqlite/backend.py` | local only | commit `51785c8` |
| `scripts/*` (49), `examples/*` (5 .py), `audits/*` | local only | commit `e571b96` |
| `tests/*` (53 + fixtures), `pytest.ini`, `requirements-dev.txt` | local only | commit `fee7fe1` |
| `docs/*` (49 + `investigations/`), `README.md` API section | local only | commit `f808ceb` |
| `LICENSE` | hub | added by the merge |
| `docs/qch-core-conceptual-schema-v0.1.md` | hub | added by the merge |
| `benchmarks/.gitkeep`, `examples/.gitkeep`, `src/qch/.gitkeep` | hub | added by the merge (harmless placeholders) |
| `tests/.gitkeep` | both | identical empty file; no conflict |
| `.gitignore` | both | **conflict**: demo version plus a curated hub subset (`.env`, `.envrc`, `.streamlit/secrets.toml`, venv/build/coverage/tool caches) |
| `README.md` | both | **conflict**: demo version plus the hub's Vision and License sections, and a pointer to this document |
| `.mailmap` | new | maps the machine-local author address and the "Jiaheng" identity to `Jiaheng Lu <jiahenglu@gmail.com>` for display only; history is not rewritten |
| `docs/REPOSITORY_CONSOLIDATION.md` | new | this document |
| `data/qch_ecdsa_p17.sqlite3` | demo (v5) | **unchanged on the branch**; the local v7 working copy stays uncommitted (D3) |
| `data/external/`, `data/backups/`, `results/`, `.venv/` | local | not migrated (ignored) |

---

## 4. Conflicts and issues

| # | Item | Status |
|---|---|---|
| C1 | `.gitignore` add/add | resolved as in §3; no tracked file becomes ignored (`git ls-files -ci --exclude-standard` is empty) |
| C2 | `README.md` add/add | resolved as in §3 |
| C3 | Repository name: `quantum-circuit-hub` is taken by the hub repository | the hub must be renamed before the demo can take the name (§5.3) |
| C4 | License: the demo had none; the merge brings in Apache-2.0 | D2 |
| C5 | Research-supplement rows (no source license) in the local DB | not committed; D3 |
| C6 | Local `main` is 3 commits behind `origin/main` | untouched; fast-forward it after the PR is merged |
| C7 | Hard-coded Windows paths (`C:\Users\jilu\Project\QCDB\Data\ECDSA\...`) in a few tests, examples and docs | tests skip when the path is absent; D5 (move to environment variables) |
| C8 | 7 historic commits carry a machine-local e-mail address, already public | not rewritten (that would need a force-push); `.mailmap` fixes the display; D6 |
| C9 | The dev container disables Streamlit's CORS and XSRF protection | D7 |
| C10 | The Streamlit Cloud app's repository binding is unknown | D4 |
| C11 | Stray file `h origin ui-improvement` | left untracked; delete it locally if unneeded |

---

## 5. Commands

### 5.1 What was run locally (reversible)

Backup, outside the repository, in `C:\Users\jilu\Project\QCDB\Demo01\qch-consolidation-backup-20260927\`:
- `local-refs.bundle`: every local ref; checked with `git bundle verify`.
- `uncommitted-worktree.tar`: all 179 modified or untracked paths.
- `HEAD.txt`, `refs.txt`, `status.txt`, `local-config.txt`.

```bash
git fetch origin && git fetch origin --tags
git remote add qch-hub https://github.com/QuantumCircuit-Hub/quantum-circuit-hub.git
git fetch qch-hub
git switch -c consolidate/qch-canonical          # from ui-improvement (c605445), keeping local changes
git merge --ff-only origin/main                   # -> 6ff7788 (adds .devcontainer)
# four commits of the local-only work (author: Jiaheng Lu <jiahenglu@gmail.com>, set per command)
git merge --no-commit --allow-unrelated-histories qch-hub/main
# resolve .gitignore / README.md; add .mailmap and this document; run tests; commit the merge
```

### 5.2 Rollback of the local changes

```bash
git switch ui-improvement                          # the local-only files vanish from the tree here...
tar -xf ../qch-consolidation-backup-20260927/uncommitted-worktree.tar   # ...and are restored here
git branch -D consolidate/qch-canonical
git remote remove qch-hub
git status --short                                 # compare with ../qch-consolidation-backup-20260927/status.txt
```

If anything else went wrong, `git clone ../qch-consolidation-backup-20260927/local-refs.bundle` restores
every ref as it was.

### 5.3 Remote steps, not run. Each needs your go-ahead; the order matters.

```bash
# 1. Publish the branch and open a PR into demo main (normal push, no force)
git push -u origin consolidate/qch-canonical
# 2. Review and merge the PR on GitHub; then locally:
git switch main && git pull --ff-only
# 3. Commit a pointer README to the hub repo while it is still writable
#    ("moved to QuantumCircuit-Hub/quantum-circuit-hub, formerly quantum-circuit-demo01")
# 4. GitHub > hub repo > Settings > rename to  quantum-circuit-hub-legacy
# 5. GitHub > demo repo > Settings > rename to quantum-circuit-hub
#    (the old demo URL redirects; once a repo uses the name quantum-circuit-hub,
#     the hub repo is reachable only as quantum-circuit-hub-legacy)
# 6. Streamlit Community Cloud: confirm the app still builds from
#    QuantumCircuit-Hub/quantum-circuit-hub @ main, app.py; re-link it if needed
# 7. GitHub > quantum-circuit-hub-legacy > Settings > Archive
# 8. Locally:
git remote set-url origin https://github.com/QuantumCircuit-Hub/quantum-circuit-hub.git
git remote remove qch-hub
```

### 5.4 Rollback of the remote steps

| Step | Rollback |
|---|---|
| 1 | delete the pushed branch or close the PR (nothing else is affected) |
| 2 | `git revert -m 1 <merge-commit>` and push normally (no force-push) |
| 7 | unarchive in Settings |
| 5, then 4 | rename back in reverse order: demo `quantum-circuit-hub` → `quantum-circuit-demo01`, then hub `quantum-circuit-hub-legacy` → `quantum-circuit-hub` |
| 6 | re-link the Streamlit app to the original repo and branch |

Nothing is deleted at any step, so every step can be undone.

---

## 6. Decisions needed

| # | Decision |
|---|---|
| D1 | Confirm there are no student contributions outside these two repositories (forks, other repos, unpushed clones) before archiving. None are visible in Git or on GitHub. |
| D2 | Adopt Apache-2.0 for the combined code. It is inherited from the hub repo; the demo code had no license. |
| D3 | Whether to commit the schema-v7 working-copy DB. It adds rows derived from an unlicensed source, so ask the authors or keep it private. Until then the branch keeps the public schema-v5 store. |
| D4 | Streamlit Cloud: which repository and branch the app is deployed from, and whether to re-link it after the rename. |
| D5 | Replace the hard-coded local paths with environment variables so other contributors can run the real-data tests. |
| D6 | Keep the `.mailmap` display mapping (history is never rewritten). |
| D7 | Keep or remove `--server.enableCORS false --server.enableXsrfProtection false` in the dev container. |
| D8 | Canonical names: `quantum-circuit-hub` for the renamed demo and `quantum-circuit-hub-legacy` for the old hub. |
