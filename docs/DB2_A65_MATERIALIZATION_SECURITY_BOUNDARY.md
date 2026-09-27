# DB-2 Phase A6.5: Materialization Security Boundary

**Status: research and design only.** No source, test, schema, or
migration changed. This document exists to say precisely what DB-2
Phase A6 protects against today, what it does not, and what a future
phase would need to add before QCH could safely materialize source it
does not already trust — before any work begins on canonical circuit
representation or structural analysis.

## 1. Executive summary

**DB-2 Phase A6 is Security Level S1: isolated workspace + timeout —
not a sandbox.** `ECDSAFailMaterializer` runs `cargo build`/
`build_circuit` as ordinary subprocesses with the calling process's own
OS privileges. Workspace isolation (a fresh temp directory, source
extracted via `git archive` so the original repository is never
touched) is real and verified; **process sandboxing is not present at
all** — no container, VM, seccomp filter, or bubblewrap/sandbox-exec
equivalent. This was already stated in
[DB2_A6_MATERIALIZATION_ENGINE_V01.md](DB2_A6_MATERIALIZATION_ENGINE_V01.md);
this document makes it precise and actionable rather than a single
prose warning.

**This is acceptable today** because QCH's only registered materializer
targets commits that DB-2 Phases A1-A4 already established as
legitimate historical provenance (a fixed, known repository, specific
already-identified commits) — not arbitrary or adversarial input.
**It stops being acceptable the moment QCH is asked to materialize
source it has not itself vetted** — a public submission portal, an
untrusted upload, or any generator whose source QCH did not establish
the provenance of itself.

**Recommendation:** introduce an `ExecutionBackend` abstraction between
`Materializer` (describes WHAT to run) and the actual OS-level
execution (controls HOW/WHERE it runs) — proposed here, **not
implemented**. `MaterializationsService`'s existing responsibility
split already anticipates exactly this seam; no A6 architecture needs
to be undone to add it later.

## 2. Current A6 execution model

```
QCH.open(materializers=[ECDSAFailMaterializer(repo_path)])
        |
        v
hub.materializations.materialize(version_id)
        |
        v
MaterializationsService  -- resolves provenance, creates the
        |                   MaterializationJob row, owns all DB writes
        v
ECDSAFailMaterializer.materialize(context)
        |
        v
git archive <sha> | tarfile.extractall(workspace)   -- pure read of repo_path's object DB
        |
        v
subprocess.run(["cargo", "build", ...], cwd=workspace, timeout=900)
        |
        v
subprocess.run([build_circuit_binary], cwd=workspace, timeout=900)
        |
        v
ops.bin  ->  ArtifactStore.put()  ->  Artifact row  ->  MaterializationJob.status = SUCCESS
```

Every step from `git archive` onward executes **directly on the host
running QCH**, as the same OS user, with the same filesystem, network,
and process-table visibility as QCH itself. Nothing in this chain
drops privileges, changes UID, enters a namespace, or restricts what
`cargo`/`build_circuit` (or anything `cargo` decides to compile and
run, including build scripts and proc-macros) can do.

## 3. Trust boundary

| Component | Trust level |
|---|---|
| QCH core (`hub.py`, `services/*.py`, `Storage` protocol) | **Trusted** — no third-party code ever runs here |
| `MaterializationsService` | **Trusted** — orchestration and DB writes only, never executes generated/external code itself |
| `ArtifactStore` | **Trusted** — pure byte storage, never interprets or executes artifact content |
| `Materializer.can_materialize()` / `.environment_fingerprint()` | **Trusted** — by contract, cheap and side-effect-free (DB2_A6 §"Materializer protocol"); nothing stops a plugin from doing otherwise, but the one shipped plugin does not |
| The `ecdsafail-challenge` repository's tracked source at a specific, already-identified commit | **Partially trusted** — DB-2 Phases A1-A4 established these ARE legitimate historical realizations; the code within them is still third-party Rust that gets compiled and executed |
| `cargo build`'s own dependency resolution (crates.io, build scripts, proc-macros) | **Untrusted in principle** — `Cargo.lock` pins versions, but nothing prevents a locked dependency's build script from doing anything the OS user can do; A6 does not audit this |
| `build_circuit`'s actual execution (the untrusted stage even in ECDSA.Fail's own CI design) | **Untrusted** — this is exactly the stage ECDSA.Fail's own `benchmark.sh` bubblewrap-sandboxes (DB2_A5 §5); A6 runs it unsandboxed |

**Workspace isolation is not process sandboxing.** Workspace isolation
means *the files QCH already controls* (the source repository, other
materialization workspaces, the QCH database) cannot be *accidentally*
clobbered by a well-behaved but buggy generator, because each attempt
gets its own fresh directory and the source repository is never
checked out into or shared with it. Process sandboxing means *the code
being run* cannot exceed a defined resource/access envelope even if it
is actively hostile. A6 provides the first. It provides none of the
second except a wall-clock timeout.

## 4. Threat model (scoped to QCH materialization specifically)

| Risk | A6 today |
|---|---|
| Arbitrary code execution (the generator does something unrelated to circuit generation) | **Not mitigated** — `cargo build`/the binary run with full process privileges |
| Filesystem access beyond the workspace | **Not mitigated** — no filesystem restriction; a malicious build script could read/write anywhere the OS user can |
| Network access (exfiltration, fetching unexpected payloads) | **Not mitigated** — `cargo build --offline` is used, which prevents *cargo itself* from reaching the network for dependency resolution, but does **not** prevent a build script or the compiled binary from opening sockets directly |
| Excessive CPU | **Not mitigated** |
| Excessive memory | **Not mitigated** — this project's own A5/A6 studies observed one real submission's `build_circuit` consume 6+ GB and keep climbing before being manually killed |
| Excessive disk/output | **Partially mitigated** — a post-hoc `max_output_bytes` check exists, but only *after* the file is fully written; nothing caps space consumed *during* generation |
| Process spawning (fork bombs, orphaned children) | **Not mitigated** |
| Timeout abuse (a hang that never produces output) | **Mitigated** — `subprocess.run(..., timeout=...)` is enforced and raises `MaterializationTimeoutError`, correctly recorded as `MaterializationJob.status = FAILED` |
| Dependency/build-script execution (`cargo build` runs arbitrary `build.rs`) | **Not mitigated** — inherent to `cargo build` itself; `--offline`/`--locked` constrain *which* dependencies but not *what their build scripts do* |
| Artifact tampering (bytes changed after being stored) | **Mitigated** — `LocalArtifactStore` `chmod`s objects read-only after write, and `verify()` recomputes the hash on demand |
| Access to secrets/environment variables | **Not mitigated** — the subprocess inherits QCH's full process environment (any credentials, tokens, or paths present in it) |

## 5. Security levels

| Level | Definition |
|---|---|
| **S0** | Trusted local execution — no isolation at all; the generator could run directly in QCH's own working directory. (A6 does not do this; see S1 below.) |
| **S1** | Isolated workspace + timeout — a fresh, disposable directory per attempt; wall-clock timeout enforced; no resource/access restriction beyond that. **This is what DB-2 Phase A6 provides today.** |
| **S2** | Sandbox/container with resource and filesystem restrictions — CPU/memory/disk quotas, a restricted or absent filesystem view outside the workspace, network denied by default, enforced via an OS-level mechanism (container runtime, `bwrap`/`sandbox-exec`-equivalent, or a VM). Suitable for running *known* generators whose *output* is still worth not fully trusting. |
| **S3** | Isolated worker/VM suitable for untrusted public submissions — S2's controls plus strong identity separation from the host running QCH itself (a genuinely separate machine/VM/microVM), so a full sandbox escape does not reach QCH's own database, credentials, or other tenants' data. Required before accepting arbitrary third-party generator code. |

**A6 provides S1.** Reaching S2 does not require redesigning
`Materializer`/`MaterializationsService` — it requires an
`ExecutionBackend` under `Materializer` that actually enforces limits
(§6). Reaching S3 additionally requires that backend to run
someplace other than the host process itself.

## 6. ExecutionBackend proposal (design only — not implemented)

```
Materializer            -- describes WHAT to execute (a command, its
    |                       working directory, its resource asks)
    v
ExecutionBackend         -- controls HOW/WHERE it executes
    |
    +-- LocalExecutionBackend        (today's behavior, S1: subprocess.run + timeout)
    +-- ContainerExecutionBackend    (S2: e.g. a rootless container runtime,
    |                                 cgroup CPU/memory limits, read-only
    |                                 bind mount of the workspace, network
    |                                 namespace with no default route)
    +-- RemoteWorkerExecutionBackend (S3: dispatches to an isolated
                                       worker/VM; ExecutionBackend's own
                                       interface stays the same, only
                                       "where" changes)
```

This fits the existing A6 architecture directly: `Materializer.materialize()`
already only *describes* what needs to run before handing control to
subprocess execution (`ECDSAFailMaterializer._run()`) — that one method
is exactly the seam an `ExecutionBackend` would replace.
`MaterializationsService` would be unaffected; it never calls
`subprocess` itself today and would not need to under this proposal
either. A `Materializer` would be given (or configured with) an
`ExecutionBackend` and call `backend.run(spec)` instead of
`subprocess.run(...)` directly — the separation the current single
plugin does not yet have because V0.1 deliberately kept everything in
one file for a first working version (DB2_A6 §"Recommended A7 scope").

**Decision: yes, this separation fits and is recommended for a future
phase.** It is not recommended for immediate implementation in A6.5
(explicitly out of scope, §9 of this phase's own brief) or even
necessarily all of A7 — see §11.

## 7. Minimum contract of a secure ExecutionBackend

What it must be able to **control**:

- Timeout (already present at S1; must remain present at every level)
- CPU (time or share limit)
- Memory (hard cap, process killed on exceed — not just an assertion after the fact)
- Disk/output size (enforced *during* writing, not only checked after — the gap in today's post-hoc check)
- Network access (default deny; explicit allow only if a materializer genuinely needs it, which none does today)
- Filesystem mounts (the workspace read-write; everything else read-only or absent, never the QCH database file or other workspaces)
- Environment variables (an explicit, minimal allowlist — never the full inherited process environment)
- Subprocess/process limits (cap on process/thread count, to bound fork-bomb-style abuse)
- Working directory (always the isolated workspace, never QCH's own)

What it must **return** to `MaterializationsService` for provenance/
debugging (conceptually — no schema change proposed here; these would
fit inside the already-existing `MaterializationJob.metadata`/
`command`/`parameters` JSON fields without a new column):

- Exit code / termination reason (normal exit, killed-by-timeout,
  killed-by-memory-limit, killed-by-signal)
- Peak resource usage observed (CPU time, peak memory, bytes written)
- Which specific limits were configured for this attempt (so a later
  reader can tell "this ran under a 900s/2GB envelope" from "this ran
  under a 60s/512MB envelope")
- Backend identity (`LocalExecutionBackend` vs. `ContainerExecutionBackend`,
  analogous to how `materializer_name`/`materializer_version` already
  identify which plugin ran)

## 8. Failure semantics

**Confirmed: a security-driven failure must fail the
`MaterializationJob`, never touch the `CircuitVersion`.** This is
already how A6 behaves for every failure category tested (generation
failure, timeout, missing output, unexpected exception) — nothing
about a future `TIMEOUT`/`MEMORY_LIMIT`/`DISK_LIMIT`/`NETWORK_POLICY`/
`SANDBOX_FAILURE` error changes that guarantee; they would simply be
new values of the already-existing, already-free-text
`MaterializationJob.error_type` field.

**Should these become structured (enum-like) failure types rather than
only free-text strings?** `error_type` is already NOT schema-CHECK-
constrained (verified directly against `003_materialization.sql` this
phase — it is plain `TEXT`), by deliberate design: different
materializers legitimately need different error vocabularies (compare
`ECDSAFailMaterializer`'s own `BUILD_FAILED`/`GENERATION_FAILED`/
`OUTPUT_MISSING`/`OUTPUT_TOO_LARGE`/`SOURCE_PREPARATION_FAILED`/
`PROVENANCE_UNRESOLVED` to whatever a QASM materializer would use).
**Recommendation:** define a small, *documented* (not
database-enforced) set of security-specific error types —
`TIMEOUT`, `MEMORY_LIMIT`, `DISK_LIMIT`, `NETWORK_POLICY`,
`SANDBOX_FAILURE` — as a Python-level convention any `ExecutionBackend`
implementation should use, the same way `MaterializationTimeoutError`
already exists as a distinguished subclass of `MaterializationError`
today. **No schema change is needed** — the existing free-text column
already accommodates this; only application-level discipline does.

## 9. Local research vs. public service

**Acceptable today, for a trusted researcher materializing known,
already-identified repositories locally (S1):** exactly A6's current
scope. The operator already trusts the source (it is the same
ECDSA.Fail history DB-2 Phases A1-A4 spent four phases establishing
provenance for), runs QCH on their own machine, and accepts that a
misbehaving build could consume significant local resources (as
directly observed and manually terminated during both the A5 and A6
research phases) — a nuisance, not a security incident, in that
context.

**Mandatory before accepting arbitrary third-party generators (moving
toward S2, and S3 for anything public-facing):**

1. Real OS-level or container-based sandboxing (§5, §6) — not optional,
   not "best effort."
2. Enforced (not post-hoc) resource limits — CPU, memory, and disk
   caps that terminate an attempt *during* execution, not after.
3. Default-deny network policy at the sandbox level, not merely
   `cargo build --offline` (which restricts cargo's own dependency
   fetching, not what a compiled binary does with a socket).
4. An explicit, minimal environment-variable allowlist — never the
   inherited host process environment.
5. For a genuinely public/multi-tenant service specifically: identity
   separation strong enough that a full sandbox escape cannot reach
   QCH's own database, other tenants' workspaces, or host credentials
   (S3) — a container alone (S2) is not sufficient for that threat
   model, only for a single trusted operator running somewhat-less-
   trusted generators.

## 10. Recommended future implementation

Not scheduled for A6.5 or necessarily all of A7 (see §11). In priority
order once undertaken: (1) `ExecutionBackend` protocol +
`LocalExecutionBackend` (a refactor of today's `subprocess.run` call,
behaviorally identical, S1, but with the seam in place); (2)
`ContainerExecutionBackend` reaching S2 — the first backend that
actually changes the security posture; (3) structured security error
types per §8; (4) only once S2 exists and is the default for any
non-fully-trusted source: consider S3 for a genuinely public service.

## 11. Implications for A7

**Yes — A7 (canonical circuit representation / structural analysis)
can proceed without the full sandbox being implemented first.**
Structural analysis operates on `Artifact` bytes that already exist in
the `ArtifactStore` — it does not execute third-party generator code
at all (DB2_A5 §18's own boundary: `Artifact -> Parse -> Canonical
Representation -> StructuralMetric -> structural_fingerprint`, all
QCH-trusted operations on data QCH already holds). The security gap
documented here is specific to the *materialization* step (running a
generator to produce bytes in the first place), not to anything A7
would add on top of bytes that already exist. **The one constraint
this document places on A7:** do not use A7's work as an occasion to
quietly start materializing a wider set of sources than A6 already
covers (e.g. "let's also support arbitrary uploaded generators while
we're in here") without first raising the security level accordingly
— A7 should keep operating on the same S1-appropriate trusted-source
scope A6 already established.
