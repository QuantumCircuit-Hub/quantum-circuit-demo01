# QCH Phase 2D.6-R0 — Contributor Source & Identity Audit

**Strictly read-only investigation.** Date: 2026-09-24. No QCH source, test, DB, importer,
query engine, NL layer, UI or ECDSA.Fail repository was modified. This report is the only
persistent change. Contributor data was **not** ingested. Personal data in this report is
limited to public GitHub logins that are needed as forensic evidence. No emails, avatar URLs,
numeric GitHub IDs or account UUIDs are reproduced.

---

## A. Executive Summary

* The official ECDSA.Fail API has a **first-party, machine-readable, per-submission
  contributor link**: `solverAccountId` (a platform account UUID) and `solverUsername`
  (a GitHub login). They are present on **1248/1248** API submissions and on **999/1003** QCH
  submissions (100% of the QCH submissions that have an API record).
* The API has **no display-name / real-name field**. The platform identifies contributors by
  **account plus GitHub login**, not by person name.
* **"Alan Li" occurs in no first-party source** (API: 0 whole-word hits; Git history, commit
  trailers and GitHub PRs: 0). **"Show me all submissions by Alan Li" cannot be answered
  from first-party data: NO.** No platform account can be deterministically connected to that
  paper name.
* The relationship is **1:N (platform solver account → submission)** for ownership. A thin,
  optional co-authorship layer (API `coauthors[]`: 15 submissions; Git `Co-authored-by`
  trailers) makes *contribution* **M:N**.
* **GitHub login is mutable.** One account demonstrably changed its login (`welttowelt` →
  `odinfree`; proven by an identical GitHub numeric user ID, §O). The stable key is the platform
  `solverAccountId`, and the GitHub numeric user ID is an equally stable secondary key.
* **Appendix A is not authoritative.** Of its 93 "GitHub ID" entries, 90 match API logins
  exactly, 1 matches after repairing a PDF line wrap, and 2 are *Git co-author names*
  that are not platform accounts. 45 of the 136 platform accounts (771 submissions) are absent
  from that list. Of the 29 named people, 1 matches exactly (a handle-like name), 4 are ambiguous, and 24 are unresolved.
* **Go/No-Go:** **GO for account-/login-based contributor queries. NO-GO for person-name
  queries** (such as "Alan Li") without a first-party name→account assertion.

## B. Scope and Read-Only Guarantees

| guarantee | before | after |
|---|---|---|
| QCH DB SHA-256 | `391a4316f77430f13560dd928592c01d15991a051321a7acaa2027e508263b18` | identical |
| DB size / mtime | 3,248,128 B / 2026-09-24 10:43:50 | identical |
| WAL/SHM files | none | none |
| QCH git status (126 pre-existing lines) and all src/tests/scripts/docs file hashes | recorded | identical |
| ECDSA.Fail clones (challenge, v2–v5): HEAD + status | recorded | identical |

The DB was opened only with `file:…?immutable=1`. Git was used only with `log`,
`for-each-ref`, `remote -v` and `status`. No checkout or fetch was made. Network use:
one anonymous GET to the first-party benchmark endpoint and four anonymous GETs to the
public GitHub PR listing of `Layr-Labs/ecdsafail-challenge`. No profile pages were
fetched.

## C. Sources Investigated

| id | source | notes |
|---|---|---|
| S1 | Official API snapshot (frozen, Phase 2D.5) `…/benchmarks/1ffb695a…/submissions` | 1248 records; body SHA `a4dbc0f2…04af`; fetched 2026-09-24T07:21:24Z; ETag `"a4dbc0f22aa15ca0cfd49f41eb5fddab"` |
| S1b | `GET /api/benchmarks/1ffb695a…` (live, anonymous) | benchmark metadata only: `setterAccountId` (the benchmark owner), no solver list |
| S2 | ECDSA.Fail Git (`Layr-Labs/ecdsafail-challenge` clone, 2005 commits, 686 refs) | authors, committers, trailers, branch names |
| S3 | GitHub PR metadata of the same repo (384 PRs) | PR author, head ref, title |
| S4 | Paper arXiv 2609.09582v2, Appendix A | 29 named contributors and 93 GitHub IDs; clue only |
| S5 | QCH DB (immutable) | 1003 submissions, 781 CircuitVersions, 999 submission_evaluation rows |

## D. Contributor-Related API Field Inventory

Complete JSON record keys (S1):
`benchmarkId, claimedScore, coauthors[], createdAt, id, improved, note, officialMetrics{qubits,toffoli}, officialScore, promotedSourceRef, promotionFinishedAt, promotionReason, promotionSnapshotRef, promotionStatus, rejectionReason, solverAccountId, solverAvatarUrl, solverProfileUrl, solverUsername, status, submissionCommitSha, updatedAt`.

| Field | Type / null | Coverage | Unique | Meaning (evidence) | Stable? | Personal? | Needed for provenance? | Classification |
|---|---|---|---|---|---|---|---|---|
| `solverAccountId` | str UUID, never null | 1248/1248 | 136 | platform account that submitted | yes (1:1 with the numeric GitHub ID; survived the login rename) | pseudonymous ID | **yes** | **AUTHORITATIVE_IDENTITY_KEY** |
| `solverUsername` | str, never null | 1248/1248 | 136 | the account's GitHub login (`solverProfileUrl` = `github.com/<solverUsername>` in 1248/1248) | **no**: current login, mutable (§O) | public handle | yes (lookup/display) | DISPLAY_METADATA / lookup alias |
| `solverProfileUrl` | str, never null | 1248/1248 | 136 | `https://github.com/<login>`, fully derivable from `solverUsername` | as login | profile link | no | OPTIONAL_PROFILE_METADATA (derivable, don't ingest) |
| `solverAvatarUrl` | str, never null | 1248/1248 | 136 | `avatars.githubusercontent.com/u/<numeric id>`; embeds the stable GitHub numeric user ID | image URL: no; embedded ID: yes | image/profile | no (the numeric ID would be at most an optional secondary key) | PERSONAL_DATA_NOT_NEEDED |
| `coauthors[]` | list[str], never null, usually empty | 15/1248 non-empty (20 entries, ≤2 per submission) | 4 strings | self-declared co-authors (handle-like strings); 3 of 4 are platform logins, 1 is not | as strings only | handles | optional (M:N contribution) | PROVENANCE_LINK (weak, self-declared) / AMBIGUOUS |
| `note` | str (16 null) | 1232 | — | free-text submission description; names **other** people too (§O) | no | may contain names/affiliations | no | UNSAFE_TO_INGEST for identity (free text) |

There is no display-name, email, team, organisation or wallet field in the API.

## E. Contributor Identity Taxonomy

| Identity | Where it lives | Proven relations |
|---|---|---|
| **Platform account** | `solverAccountId` | owns submissions (1:N), always present |
| **GitHub account** (stable numeric ID) | embedded in `solverAvatarUrl`, and in `<id>+login@users.noreply.github.com` Git trailers | 136 IDs ↔ 136 accounts, bijective |
| **GitHub login** | `solverUsername`, PR titles `@login:`, Git trailers | mutable; one proven rename |
| **Git author/committer** | commit metadata | only organiser/bot identities (§Q); **not** contributors |
| **Git co-author name** | `Co-authored-by: <name> <email>` | free-text `user.name`; may equal the login, a different handle, a real name or an AI agent |
| **Paper contributor name** | Appendix A | person names / handles; **no first-party link** except one exact handle string |
| **Person** | none | never asserted by any first-party source |

## F. Contributor ↔ Submission Relationship

Actual first-party structure:

```
Submission (API id = submission UUID)
   ├── solverAccountId  (exactly one; ownership)          [AUTHORITATIVE]
   │      └── solverUsername = GitHub login (current)     [display/lookup, mutable]
   └── coauthors[]      (0–2 self-declared handle strings) [optional, weak]

Git: branch submissions/<uuid>; commits "Submission|Validate|Accept submission <uuid>";
     Co-authored-by trailers (agree with solver on most commits, §Q)
GitHub PRs: bot-authored; title "@<login at PR time>: Validate submission <uuid>"
```

## G. Alan Li End-to-End Forensic Case

1. Does "Alan Li" appear in the official API data? **No.** 0 whole-word "Alan", 0 "Alan Li"
   in any field. All 62 raw substring hits for "alan" are inside words like
   "balance"/"rebalance" in `note`.
2. Where? Nowhere, and also absent from Git (commit messages, trailers, authors) and GitHub PRs.
3. Stable account ID? **None found.**
4. GitHub username? **None found.** No login contains "alan", and no login contains a
   standalone "li" token.
5. Role? **Undeterminable.** The paper lists Alan Li as a named leaderboard contributor
   and an author (affiliation 3). Affiliation or colleague relationships were **not** used for
   inference.
6. Connected submissions: **0 deterministically.**
7. Submission UUIDs: none.
8. Per-submission details: not applicable.
9. Provenance chain: `Alan Li (paper) → ? → platform account` is **broken at the first link**.
   The rest (`account → submission UUID → QCH submission → realized_from_submission_id →
   CircuitVersion → external_version_key`) exists and is deterministic (§T).
10. **"Show me all submissions by Alan Li." → NO.** No first-party source asserts which
    account (if any) belongs to Alan Li. Answering would need guessing (for example picking one
    of the 45 accounts not listed as GitHub IDs in Appendix A), which is forbidden.

## H. Other Named Contributor Cases

Paper names were compared with API/Git strings **exactly**. A lowercase no-space
normalisation is reported but **not** treated as proof.

| Paper name | First-party identity? | Evidence | Classification | Submissions (if the candidate were accepted) |
|---|---|---|---|---|
| **BitWonka** | platform login `BitWonka` (exact, case-sensitive); also an exact Git trailer name | exact string equality of a handle-like name | **EXACT** (string); person unproven | 53 subs (42 accepted, 43 with official eval), 45 in QCH (41 PROMOTED), 41 with CircuitVersion |
| **Jieyi Long** | login `jieyilong` equals the name lowercased without spaces | normalised equality only; 6 notes mention the name, but written by *other* accounts | **AMBIGUOUS** (strong candidate) | 56 subs, 50 in QCH, 28 with CV |
| **Vasily Gnuchev** | login `gnuchev` (surname only) | the name appears in 23 notes of `gnuchev`'s own submissions (self-attribution in free text) and in 3 notes of *another* account | **AMBIGUOUS** (supporting evidence only) | 75 subs, 70 in QCH, 52 with CV; 6 co-authored |
| **Theodore Pender** | none exact; login `teddyjfpender` exists | only fuzzy (nickname) similarity, which is not allowed as evidence | **UNRESOLVED** | — |
| **Gajesh Naik** (also an organiser) | none exact; login `Gajesh2007` exists | first-name similarity only | **UNRESOLVED** | — |
| **Bartosz Naskręcki** (Unicode ę) | none | no exact string; no handle derivable | **UNRESOLVED** | — |
| **Anto Joseph**, **Robert Kodra** | logins `antojoseph`, `robertkodra` | normalised equality only | **AMBIGUOUS** | 8 / 13 subs |
| Zhao Huang, Michael Dong, Alan Li | none | — | **UNRESOLVED** | — |

These cases exercise several situations:

* many submissions: `gnuchev` 75, `nasqret` 85 (not linked to any paper name);
* few submissions: `antojoseph` 8;
* display name ≠ login: every named case;
* co-authorship: `gnuchev`;
* uncertain mapping: Theodore Pender / Gajesh Naik.

## I. GitHub-ID Contributor Cases

| Appendix GitHub ID | Platform account | Subs | Platform status | In QCH | Lifecycle | With CV | Class |
|---|---|---|---|---|---|---|---|
| gopikannappan | 1 | 25 | 20 acc / 3 fail / 2 rej | 25 | 20 P / 2 V / 3 S | 22 | EXACT |
| Lulu-Zhou-EigenLabs | 1 | 1 | rejected | 0 | — | 0 | EXACT |
| yudongcao | 1 | 2 | rejected (no metrics) | 0 | — | 0 | EXACT |
| zeeshan8281 | 1 | 1 | rejected (with metrics) | 1 | SUBMITTED | 0 | EXACT |
| 0xfoobar | 1 | 1 | rejected | 0 | — | 0 | EXACT |
| hydro-genbond007 (PDF wrap) | 1 (`hydrogenbond007`) | 1 | rejected | 0 | — | 0 | DETERMINISTIC_ALIAS (line-wrap hyphen repair) |
| 0xpg | **none** | — | — | — | — | — | UNRESOLVED as an account: appears only as a Git `Co-authored-by` name on 3 Accept commits whose platform solver is `pratikgx` (not in Appendix A) |
| philuponcrypto | **none** | — | — | — | — | — | UNRESOLVED as an account: 1 co-author trailer on a submission whose solver is `philburgess1` (not in Appendix A) |

GitHub login works as a **lookup alias**, but it is not a primary key. It is mutable (§O), and
the Appendix mixes logins with Git `user.name` strings (0xpg, philuponcrypto).

## J. Contributor ↔ Submission Cardinality

| measure | value |
|---|---|
| platform accounts (solvers) | 136 |
| API submissions | 1248 |
| submissions with exactly one solver | 1248 (100%) |
| submissions with ≥1 API co-author | 15 (10 with 1, 5 with 2) |
| max co-authors per submission (API) | 2 (max contributors per submission = 3) |
| accounts with 1 submission | 51 |
| accounts with 2–5 / 6–20 / >20 | 43 / 26 / 16 |
| max submissions by one account | 110 |
| Git submission commits with ≥2 distinct human co-author trailers | 9 |

**Ownership: 1:N. Contribution: M:N**, but sparsely and weakly encoded. API `coauthors` are
self-declared free strings, and 1 of 4 is not a platform account. Git trailers also include AI
agents (41 "Claude…" trailers from anthropic.com/noreply).

## K. Coverage Across the 1003 QCH Submissions

| | count |
|---|---|
| total QCH submissions | 1003 |
| with a resolvable official platform contributor (`solverAccountId`) | **999** |
| with a GitHub login (`solverUsername`) | 999 |
| with a display name | 0 (the field doesn't exist) |
| with API co-authors | 14 |
| no contributor information (no API record) | 4 (all PROMOTED; the same 4 lack official evaluation in 2D.5) |
| distinct accounts across QCH submissions | 105 of 136 |

## L. PROMOTED / VALIDATED / SUBMITTED Coverage

| lifecycle | total | solver account | co-authored | no contributor info |
|---|---|---|---|---|
| PROMOTED | 520 | 516 | 0 | 4 |
| VALIDATED | 261 | 261 | 14 | 0 |
| SUBMITTED | 222 | 222 | 0 | 0 |

## M. Contributor ↔ CircuitVersion Coverage

| | count |
|---|---|
| contributor-linked submissions **with** a CircuitVersion | **777** (516 PROMOTED + 261 VALIDATED) |
| contributor-linked submissions **without** a CircuitVersion | 222 (all SUBMITTED) |
| CircuitVersions whose submission has **no** contributor | 4 (PROMOTED, no API record) |

So 777/781 CircuitVersions can be attributed to a platform account.

## N. Appendix A Cross-Check

| | count |
|---|---|
| Named leaderboard contributors | 29 |
| matched EXACT to a platform identity | 1 (`BitWonka`, handle-like name) |
| DETERMINISTIC_ALIAS | 0 |
| AMBIGUOUS (normalised string or note evidence only) | 4 (Jieyi Long, Vasily Gnuchev, Anto Joseph, Robert Kodra) |
| UNRESOLVED / not found (including **Alan Li**; nickname/first-name look-alikes such as Theodore Pender, Gajesh Naik) | 24 |
| GitHub-ID contributors | 93 |
| matched EXACT to platform logins | 90 |
| DETERMINISTIC_ALIAS (PDF line-wrap repair) | 1 |
| unmatched as platform accounts (Git co-author names only) | 2 |
| platform accounts **not** in the GitHub-ID list | 45 accounts / 771 submissions |

Evidence that Appendix A is not authoritative:

* it lists two Git `user.name` strings as "GitHub IDs";
* it omits 45 accounts;
* it lists people by name without any first-party key;
* its cut-off (26 July 2026) predates the snapshot (24 Sep 2026).

## O. Name / Handle Collision Analysis

* **Login rename (proven):** 50 PR titles `@welttowelt: …` belong to submissions whose
  platform `solverUsername` is `odinfree`. Git noreply trailers carry the `welttowelt`
  login with the **same GitHub numeric user ID** as `odinfree`'s avatar. The same GitHub
  account therefore has two logins over time. Of 50 numeric IDs seen in Git noreply
  trailers, 48 have the same login as the platform, 1 differs (this case) and 1 is not a
  platform account.
* No two accounts share a login. No account has two logins **in the snapshot**. There are
  0 case-folded or punctuation-normalised login collisions.
* Logins: 29 of 136 contain upper case (case must be preserved for display and folded for
  lookup), 20 contain hyphens, and none contain spaces or non-ASCII characters.
* The paper name "Bartosz Naskręcki" has Unicode, and PDF extraction yields "Naskrcki".
  Names cannot be matched byte-exactly across sources.
* Names occur in `note` as **credit to other people** ("BitWonka" in notes of 8 different
  accounts, "Jieyi Long" only in notes of other accounts). **Free-text name mentions are not
  authorship.**
* "Alan Li" in NL: it could match only a paper name. There's no login, note or trailer
  candidate, so a resolver must answer UNKNOWN, not guess.

## P. Person vs Account vs Handle Analysis

| link | status |
|---|---|
| Submission → platform account | **deterministic** (API `solverAccountId`) |
| Platform account ↔ GitHub numeric user ID | **deterministic** (bijective 136↔136) |
| Platform account → current GitHub login | deterministic *at snapshot time*; mutable |
| GitHub login (historical) → account | deterministic only through the numeric ID (Git noreply email / avatar) |
| Git co-author name → account | **inferred** unless a noreply email carries the ID |
| Paper name → account | **not established** (except one exact handle-like string) |
| Account → person | **never asserted** by any first-party source |

These five concepts must stay distinct.

## Q. Git Author vs Platform Submitter Analysis

* Git **authors/committers** are only organiser/pipeline identities: 6 distinct author names
  (7 name/email-domain combinations): the organiser (two email forms), an organiser handle,
  2 bots, 1 pipeline agent and 1 implementer agent. There are 3 distinct non-null `source_commit.author` values in QCH (bots and
  the pipeline), plus 6 nulls. **Git author ≠ submitter.**
* **Co-author trailers** vs the platform solver, on commits that name a submission UUID:

  | commit kind | agrees | differs | no trailer |
  |---|---|---|---|
  | Submission | 83 | 1 | 242 |
  | Validate | 282 | 71 | 0 |
  | Accept | 396 | 18 | 10 |

  The disagreements consist of 90 non-platform handles (other humans, `claude`,
  renamed logins) and 14 other platform users. Trailers are **supporting evidence**.
* **GitHub PRs:** 373 of 384 are authored by `yukon-autoresearch[bot]`, so the PR author is
  not the submitter. Title handles agree with the platform solver in 173 of 223 parsable
  titles, and all 50 differences are the proven rename.

## R. Coauthor / Team Submission Analysis

* API `coauthors[]`: 15 submissions (from only 2 solver accounts), 20 entries, 4 distinct
  strings (3 are platform logins, 1 is not). There is no team or organisation entity.
* Git: 9 submission commits carry ≥2 distinct human co-author trailers, and AI agents appear
  as co-authors.
* Co-authorship exists but is **sparse, self-declared and not machine-verified**. It can
  support "contributed to", never ownership.

## S. Official Evaluation Joinability

The contributor fields share the same API record as the Phase 2D.5 evaluation (the key is
the submission UUID). The join `account → submission → submission_evaluation` is therefore
exact.

* Of the 999 contributor-linked QCH submissions, 855 have official metrics.
* By platform status: accepted/rejected/failed/cancelled, as imported in 2D.5.

## T. Existing QCH Identity Joinability

`solverAccountId` → API `id` = submission UUID → QCH `submission.external_submission_key =
"ecdsafail:<uuid>"` → `circuit_version.realized_from_submission_id` →
`external_version_key`. Every hop is deterministic and already exists in QCH, except the
first (contributor fields are **not** stored; the 2D.5 importer explicitly excludes
`solver*`, `coauthors` and `note`). QCH currently stores no contributor data.
`source_commit.author` contains only bot/pipeline values, and stored messages contain no
co-author trailers.

## U. Privacy and Data-Minimization Findings

| class | fields |
|---|---|
| A. Necessary provenance identity | `solverAccountId`; the submission↔account relation; source snapshot SHA |
| B. Useful public display metadata | `solverUsername` (current GitHub login), with the rename caveat |
| C. Unnecessary personal/profile info | `solverAvatarUrl`, `solverProfileUrl` (derivable), Git author/co-author **emails**, paper affiliations |
| D. Potentially sensitive | `note` free text (names third parties); non-noreply co-author emails (410 trailers use personal/company domains) |

The minimum for "submissions by <login>" is `platform_account_id`, `current_login`,
`submission→account` and snapshot provenance. The GitHub numeric ID is optional, only to
survive renames, and should be stored as an identifier, never as a URL.

## V. Source Authority Classification

| source | classification |
|---|---|
| API `solverAccountId` (ownership) | **AUTHORITATIVE** |
| API `solverUsername` | **DISPLAY_ONLY** (authoritative only for "current login at snapshot time") |
| GitHub numeric user ID (avatar / noreply email) | **DETERMINISTICALLY_DERIVABLE** (alias bridge for renames) |
| API `coauthors[]` | **SUPPORTING_EVIDENCE** (self-declared) |
| Git `Co-authored-by` trailers | **SUPPORTING_EVIDENCE** |
| Git author/committer | **UNSAFE** for contributor identity (bots/organisers) |
| GitHub PR author | **UNSAFE** (bot); PR title handle **SUPPORTING_EVIDENCE** (historical login) |
| Paper Appendix A | **AMBIGUOUS** clue (not authoritative; inconsistent key types, incomplete) |
| `note` name mentions | **UNSAFE** (free text, credits third parties) |

The derived hierarchy is: platform account ID > submission ownership relation >
GitHub numeric ID bridge > GitHub login / trailers > paper > free text.

## W. Query Feasibility Matrix

| Q | question | verdict | requirement |
|---|---|---|---|
| Q1 | "Show me all submissions by **Alan Li**" | **NOT_SUPPORTED_BY_SOURCE** | a first-party name→account assertion (doesn't exist). By *login* it is READY_FROM_SOURCE. |
| Q2 | "Which CircuitVersions came from Alan Li?" | **NOT_SUPPORTED_BY_SOURCE** by name; READY by account/login | as Q1, plus the existing `realized_from_submission_id` join |
| Q3 | "Who submitted CircuitVersion X?" | **READY_FROM_SOURCE** (777/781) | ingest account ID and login; answer "platform account `<login>`", not a person; 4 CVs have no API record |
| Q4 | "Which contributors submitted circuits below 1000 qubits?" | **READY_FROM_SOURCE** (as accounts/logins) | account link plus existing `evaluation.peak_qubits` |
| Q5 | "Compare official evaluation of Alan Li and Y" | **POSSIBLE_WITH_LIMITATIONS**: only when both are given as logins/accounts; not for "Alan Li" | aggregation over submissions per account |
| Q6 | "Evolution of one contributor's submissions over time" | **POSSIBLE_WITH_LIMITATIONS** | `createdAt`, lifecycle and evaluation per account; evolution edges exist only for the 777 with a CV |

## X. Alan Li Query Demonstration

```
"Show me all submissions by Alan Li."
  paper name "Alan Li"
    -> API solverUsername / coauthors / note : no match
    -> Git authors / trailers / messages      : no match
    -> GitHub PR titles / bodies              : no match
  => correct answer today: UNKNOWN contributor (no first-party identity), 0 submissions.
     Must NOT map to any account by affiliation, co-authorship or elimination.
```

## Y. Example Future Contributor Queries (feasible once account + login are ingested)

* "Show me all submissions by gopikannappan" → 25 submissions (22 with CV).
* "Who submitted ecdsafail:<key>?" → platform account `<login>`, with provenance.
* "Which accounts have an accepted submission with official peak qubits < 1000?"
* "Submissions by odinfree" and "submissions by welttowelt" → the same account (via the
  numeric-ID alias), if historical logins are recorded.

## Z. Evidence for Possible Future Conceptual Entities (hypotheses, not a schema)

* **PlatformAccount**: key `solverAccountId`.
* **AccountHandle / identity alias**: login, source and first/last seen; the optional stable
  GitHub numeric ID. Justified by the proven rename.
* **SubmissionContributor**: role `solver` (exactly 1) or `declared_coauthor` (0–2); the
  source is API `coauthors` or Git trailers. Justified by the M:N evidence.
* **No Person entity:** no source asserts person identity.

## AA. What Must NOT Be Ingested

`solverAvatarUrl`, `solverProfileUrl`, any Git author/co-author **email**, `note` text as
identity, paper names and affiliations as identity, GitHub profile data (names, bios),
AI-agent co-author strings as contributors, and any inferred name→account mapping.

## AB. What Remains Unknown

* Which accounts (if any) belong to the 28 named people without an exact first-party link
  (4 ambiguous, 24 unresolved), including Alan Li.
* Whether a platform (authenticated) endpoint exposes display names. None is public in S1/S1b.
* Direction and date of the `welttowelt`/`odinfree` rename, and whether other renames happened
  before the snapshot but are invisible.
* Whether 0xpg ↔ pratikgx and philuponcrypto ↔ philburgess1 are the same people. Their
  trailers use non-noreply emails, so this can't be proven.
* How Appendix A was compiled.

## AC. Data Quality Issues

* The Appendix mixes GitHub logins with Git `user.name` strings, and a PDF line wrap splits
  one ID.
* PR titles carry historical logins; the API carries the current one.
* `coauthors` are unvalidated strings (1 of 4 is not a platform account).
* Git trailers include AI agents and differ from the solver on 90+ commits.
* 4 PROMOTED QCH submissions have no API record (known from 2D.5).

## AD. Reproducibility Information

| item | value |
|---|---|
| QCH Git HEAD | `aeabc6ec1aa4682b15f5d8a455a11897c5863835` (working tree has 126 pre-existing uncommitted entries, untouched) |
| ECDSA.Fail HEAD (`ecdsafail-challenge` clone) | `6f7c159b3cc0ce57e9561cf95b35117e0b012ef6`, 2005 commits reachable, remote `Layr-Labs/ecdsafail-challenge` |
| QCH DB | SHA `391a4316…3b18`, 3,248,128 B, mtime 2026-09-24 10:43:50 |
| Row counts | submission 1003, circuit_version 781, submission_evaluation 999, evaluation_snapshot 1, transformation_edge 784, source_commit 1107, schema_migrations 4 |
| API snapshot | body SHA-256 `a4dbc0f22aa15ca0cfd49f41eb5fddabf8b028dd6df76cbc57eca350807104af`, 6,032,713 B, fetched 2026-09-24T07:21:24Z, ETag `"a4dbc0f22aa15ca0cfd49f41eb5fddab"`, endpoint `https://api.ecdsa.fail/api/benchmarks/1ffb695a-309b-46b6-a728-2f97d8c7be74/submissions` |
| GitHub PR listing | `api.github.com/repos/Layr-Labs/ecdsafail-challenge/pulls?state=all`, 4 pages, 384 PRs, fetched 2026-09-24 |
| Paper | `2609.09582v2.pdf`, Appendix A (text via `pdftotext -enc UTF-8`) |

## AE. Integrity Verification

Before/after checks: the DB SHA/size/mtime are identical and there are no `-wal`/`-shm`
files. QCH `git status --porcelain` is identical, and SHA-256 over all src/tests/scripts/docs
files is identical (before this report was written). All five ECDSA.Fail clones have
identical HEAD and status. The snapshot file is unchanged.

## AF. Recommended Next Step

Phase 2D.6 (design, then implementation) should model **platform accounts, not persons**:

* ingest only `solverAccountId`, the current `solverUsername` and the submission→account
  relation, with snapshot provenance;
* optionally record `coauthors[]` as `declared_coauthor`;
* optionally record the GitHub numeric ID as a rename-proof alias;
* make NL contributor resolution login/account-based with exact (case-folded) matching;
* answer person names without a first-party link with UNKNOWN / clarification, never with
  a guess.

## AG. Final Go / No-Go Assessment for Contributor Support

* **GO**: account/login-based contributor queries (Q3, Q4; Q1/Q2/Q5/Q6 when the contributor
  is given as a login). The source is authoritative, complete for 999/1003 QCH submissions
  and 777/781 CircuitVersions, and bijective with GitHub accounts.
* **NO-GO**: person-name queries such as "Alan Li". No first-party name→account link exists,
  and Appendix A cannot substitute for one.
