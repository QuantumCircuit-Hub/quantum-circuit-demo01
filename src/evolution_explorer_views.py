"""Streamlit rendering of version and edge details for the Version Evolution Explorer.

Pure presentation over the dataclasses of `qch.explorer.models`. All database text is untrusted: it is shown through
`st.code` / `st.json` / escaped Markdown, never as HTML, and nothing is executed.

Layout (compact, evolution metrics and research evidence first):
  version:  short id · status · submitter · submission time  ->  official metric cards  ->  collapsed Identifiers
            ->  tabs  Overview | Submission | Techniques | Artifacts | Provenance
  edge:     source -> target  ->  dQ dT dS and score reduction  ->  tabs  Comparison | Techniques | Submission | Provenance

Three kinds of evidence stay in separate places and are never merged:
  1. official platform evaluation      (metric cards, Overview / Comparison)
  2. submitter statements              (Submission tab: not verified, not proof of correctness)
  3. paper-author technique labels     (Techniques tab: an interpretive classification, never inferred from notes)
"""

from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from evolution_explorer_ui import (
    UNAVAILABLE,
    fmt_int,
    fmt_number,
    fmt_percent,
    fmt_signed,
    is_long_note,
    md_escape,
    note_display,
    presence_text,
    verbatim_code_body,
)
from qch.explorer.models import EdgeDetail, OfficialMetrics, SourceDetailInfo, TechniqueLabelInfo, VersionDetail
from qch.explorer.service import RELATION_CAVEAT, RELATION_DESCRIPTIONS, relation_label
from qch.explorer.timefmt import display_bare, format_utc

STATUS_BADGES = {"accepted": "green", "rejected": "red", "failed": "orange", "cancelled": "gray"}
LABEL_HELP = (
    "The paper authors' own primary classification of a commit (an interpretive coding). It is not proof that the technique "
    "caused the whole score change, it is never inferred from submitter notes, and a missing label does not mean that no "
    "technique was used."
)
NO_LABEL = "No author-assigned label recorded."
NL = chr(10)


def _bullets(rows: list[tuple[str, str]]) -> None:
    st.markdown(NL.join(f"- **{label}:** {value}" for label, value in rows))


def _time_table(rows: list[tuple[str, str | None, str]]) -> None:
    """One table, UTC stated once in the heading; the clock each time comes from stays in its own column."""
    lines = ["| Event | Time (UTC) | Clock |", "|---|---|---|"]
    lines += [f"| {event} | {md_escape(display_bare(value))} | {clock} |" for event, value, clock in rows]
    st.markdown(NL.join(lines))


def status_markdown(status: str | None) -> str:
    """A status badge when the status is one the platform defines; plain escaped text otherwise."""
    if not status:
        return ":gray-badge[status n/a]"
    color = STATUS_BADGES.get(status)
    return f":{color}-badge[{status}]" if color else md_escape(status)


def metric_cards(official: OfficialMetrics) -> None:
    """Q, T and S as exact values with thousands separators; "unavailable" when a value is not recorded."""
    q, t, s = st.columns(3)
    q.metric("Q · peak qubits", fmt_int(official.peak_qubits), border=True, help="Official platform metric: peak qubits of the evaluated circuit.")
    t.metric("T · avg executed Toffoli", fmt_int(official.avg_executed_toffoli), border=True,
             help="Official platform metric: rounded average executed Toffoli count. Not the structural Toffoli count.")
    s.metric("S · official score", fmt_int(official.score), border=True, help="Official platform score, Q × T. Lower is better.")


def _recorded_metric_cards(detail: VersionDetail) -> None:
    """Datasets without the ECDSA.Fail official metrics: show what IS recorded, under its own name and definition."""
    cards = [(m.name + (f" ({m.unit})" if m.unit else ""), fmt_number(m.value), m.method) for m in detail.structural_metrics]
    cards += [(f"{b.benchmark_name}: {name}", fmt_int(value), f"benchmark run ({b.status})")
              for b in detail.benchmark_runs for name, value in (("Toffoli", b.toffoli_count), ("peak qubits", b.peak_qubits), ("score", b.score)) if value is not None]
    if not cards:
        st.caption("No metrics are recorded for this version.")
        return
    shown = cards[:6]
    for start in range(0, len(shown), 3):
        columns = st.columns(3)
        for column, (label, value, method) in zip(columns, shown[start:start + 3]):
            column.metric(label, value, border=True, help=method or None)
    if len(cards) > len(shown):
        st.caption(f"{len(cards) - len(shown)} more recorded metric(s) are in the Overview tab.")


# -- submitter statements (schema v8) -------------------------------------------------------------


def render_note(note: str | None, presence: str, *, key: str) -> None:
    """The submitter's note as untrusted plain text, newlines preserved. A long note starts collapsed to a preview;
    the truncation is stated, and one toggle shows every character."""
    if note is None:
        st.markdown(f"**Note:** {presence_text(presence)}")
        return
    if note == "":
        st.markdown("**Note:** empty (present but blank)")
        return
    long = is_long_note(note)
    with st.expander(f"Submitter note — {len(note):,} characters", expanded=not long):
        show_full = st.toggle("Show full text", value=False, key=f"{key}_full", disabled=not long) if long else True
        text, notice = note_display(note, show_full=show_full)
        if notice:
            st.caption(notice)
        st.code(verbatim_code_body(text), language=None, wrap_lines=True, height=480 if (long and show_full) else "content")  # the full text scrolls inside the box


def _claimed_score_text(detail: SourceDetailInfo) -> str:
    if detail.claimed_score_presence == "value":
        return f"{md_escape(detail.claimed_score_json)} (the submitter's claim, not an official score)"
    if detail.claimed_score_presence == "null":
        return "Not provided (explicit null)"
    return "Not provided (field absent in this snapshot)"


def _coauthors_text(detail: SourceDetailInfo) -> str:
    if detail.coauthors_presence == "value":
        try:
            names = json.loads(detail.coauthors_json or "[]")
        except ValueError:
            names = []
        shown = ", ".join(md_escape(n) if isinstance(n, str) else md_escape(json.dumps(n, ensure_ascii=False)) for n in names)
        return f"{shown} (as declared; not linked to any account)"
    if detail.coauthors_presence == "empty":
        return "No coauthors declared"
    if detail.coauthors_presence == "null":
        return "No coauthors declared (explicit null)"
    return "Not provided (field absent in this snapshot)"


def render_source_detail(detail: SourceDetailInfo, *, key: str) -> None:
    acquisition = {"qch_fetch": "fetched by QCH", "third_party_pinned_copy": "third-party copy pinned by SHA-256"}.get(detail.acquisition or "", detail.acquisition or UNAVAILABLE)
    st.caption(
        f"Snapshot {md_escape(detail.detail_source_id[:8])}… · {md_escape(acquisition)} · acquired {md_escape(format_utc(detail.source_fetched_at).text)} · "
        f"{'latest for this submission' if detail.is_latest else 'an older snapshot'}"
    )
    render_note(detail.note, detail.note_presence, key=f"{key}_note")
    if detail.note_sha256:
        st.caption(f"Note SHA-256 (UTF-8): {detail.note_sha256}")
    _bullets([
        ("claimedScore", _claimed_score_text(detail)),
        ("Declared coauthors", _coauthors_text(detail)),
        ("createdAt as written in the source", md_escape(detail.platform_created_at_raw) if detail.platform_created_at_raw else UNAVAILABLE),
        ("updatedAt as written in the source", md_escape(detail.platform_updated_at_raw) if detail.platform_updated_at_raw else UNAVAILABLE),
    ])
    with st.expander("Source fields (raw states)", expanded=False):
        st.json({
            "note": {"state": detail.note_presence, "characters": detail.note_chars},
            "claimedScore": {"state": detail.claimed_score_presence, "value": json.loads(detail.claimed_score_json) if detail.claimed_score_json else None},
            "coauthors": {"state": detail.coauthors_presence, "value": json.loads(detail.coauthors_json) if detail.coauthors_json else None},
        }, expanded=True)


def render_source_details(details: tuple[SourceDetailInfo, ...], unavailable: dict[str, str], *, key: str, about: str = "this version's submission") -> None:
    st.markdown("**Submitter statements**", help=(
        f"Written by the submitter of {about}. They are the submitter's own words and claims: not official metrics, "
        "not the paper authors' classification, and not evidence that the circuit is correct."))
    if not details:
        st.caption(unavailable.get("source_details", "No submitter statements were imported."))
        return
    index = 0
    if len(details) > 1:
        labels = [f"{'latest · ' if d.is_latest else ''}snapshot acquired {format_utc(d.source_fetched_at).text} · {d.detail_source_id[:8]}" for d in details]
        chosen = st.selectbox("Source snapshot (each is shown on its own; fields are never merged)", labels, index=0, key=f"{key}_snapshot")
        index = labels.index(chosen)
    render_source_detail(details[index], key=f"{key}_{details[index].detail_source_id[:8]}")


# -- technique labels -----------------------------------------------------------------------------


def render_labels(labels: tuple[TechniqueLabelInfo, ...], *, empty_message: str = NO_LABEL, unavailable: dict[str, str] | None = None) -> None:
    st.markdown("**Author classification**", help=LABEL_HELP)
    if not labels:
        missing_tables = (unavailable or {}).get("technique_labels", "")
        st.caption(missing_tables if "no technique-label tables" in missing_tables else empty_message)
        return
    for label in labels:
        change = f" — {md_escape(label.change_type)}" if label.change_type else ""
        st.markdown(f"**{md_escape(label.family)}**{change}")
        edge_link = label.incoming_edge_id[:12] + "…" if label.incoming_edge_id else "(no edge link)"
        st.caption(f"{md_escape(label.classification_status)} · labels the incoming edge {edge_link}")
        st.caption(
            f"Source: {md_escape(label.source_file_path)}, row {label.source_row_number} · repository commit {md_escape(label.source_repository_commit[:12])} · "
            f"file SHA-256 {md_escape(label.source_sha256[:12])}… · contestant / date in the paper table: "
            f"{md_escape(label.source_contestant or UNAVAILABLE)} / {md_escape(label.source_date or UNAVAILABLE)}"
        )


# -- version ---------------------------------------------------------------------------------------


def render_version_detail(detail: VersionDetail, *, key: str) -> None:
    summary, evaluation = detail.summary, detail.evaluation
    st.markdown(f"#### {md_escape(summary.short_label)}")
    status = summary.platform_status or (evaluation.platform_status if evaluation else None)
    bits = [status_markdown(status)]
    if detail.submitter and detail.submitter.handle:
        bits.append(f"submitter **{md_escape(detail.submitter.handle)}**")
    if evaluation and evaluation.platform_created_at:
        bits.append(f"submitted {md_escape(display_bare(evaluation.platform_created_at))} UTC (platform time)")
    st.markdown(" · ".join(bits))
    if summary.official.available:
        metric_cards(summary.official)
    else:
        _recorded_metric_cards(detail)

    with st.expander("Identifiers", expanded=False):
        _bullets([
            ("Internal version ID", md_escape(summary.version_id)),
            ("External key", md_escape(summary.external_version_key) if summary.external_version_key else UNAVAILABLE),
            ("Label", md_escape(summary.version_label) if summary.version_label else UNAVAILABLE),
            ("QCH submission ID", md_escape(summary.submission_id) if summary.submission_id else UNAVAILABLE),
            ("Platform submission key", md_escape(detail.submission_key) if detail.submission_key else UNAVAILABLE),
        ])
        if "submission" in detail.unavailable:
            st.caption(detail.unavailable["submission"])

    overview, submission, techniques, artifacts, provenance = st.tabs(["Overview", "Submission", "Techniques", "Artifacts", "Provenance"])

    with overview:
        rows = [
            ("Logical circuit", md_escape(summary.logical_circuit_name or summary.logical_circuit_id)),
            ("Platform status", md_escape(evaluation.platform_status) if evaluation else (md_escape(summary.platform_status) if summary.platform_status else UNAVAILABLE)),
        ]
        if evaluation:
            promotion = md_escape(evaluation.promotion_status) if evaluation.promotion_status else "none recorded"
            if evaluation.promotion_status and evaluation.promotion_reason:
                promotion += f" — {md_escape(evaluation.promotion_reason)}"
            rows += [
                ("Stored rejection reason", md_escape(evaluation.rejection_reason) if evaluation.rejection_reason else "none recorded"),
                ("Promotion", promotion),
            ]
        _bullets(rows)
        if not evaluation:
            st.caption(detail.unavailable.get("evaluation") or detail.unavailable.get("submission") or "No platform evaluation is recorded.")
        st.markdown("**Times**", help=(
            "Platform times come from the platform's evaluation record, the Git date from the realization commit (second precision), "
            "and the ingestion time from when QCH stored the version. They are different clocks and are never substituted for one another."))
        _time_table([
            ("Platform submission time", evaluation.platform_created_at if evaluation else None, "platform"),
            ("Platform last update", evaluation.platform_updated_at if evaluation else None, "platform"),
            ("Promotion completed", evaluation.promotion_finished_at if evaluation else None, "platform"),
            ("Git author date of the realization commit", summary.historical_time, "Git"),
            ("QCH stored this version", detail.discovered_at, "QCH ingestion"),
        ])
        if detail.structural_metrics or detail.benchmark_runs:
            with st.expander("Other recorded metrics", expanded=False):
                st.caption("Structural metrics describe the circuit's structure; they are not the platform's official metrics.")
                if detail.structural_metrics:
                    st.dataframe(pd.DataFrame([{"Metric": m.name, "Value": fmt_number(m.value), "Unit": m.unit or "", "Method": m.method or ""} for m in detail.structural_metrics]),
                                 hide_index=True, width="stretch")
                if detail.benchmark_runs:
                    st.dataframe(pd.DataFrame([{
                        "Benchmark": b.benchmark_name, "Status": b.status, "Toffoli": fmt_int(b.toffoli_count), "Peak qubits": fmt_int(b.peak_qubits), "Score": fmt_int(b.score),
                    } for b in detail.benchmark_runs]), hide_index=True, width="stretch")

    with submission:
        submitter = detail.submitter
        if submitter is None:
            st.caption(detail.unavailable.get("submitter", "No submitter is recorded."))
        else:
            st.markdown(f"**Submitter account:** {md_escape(submitter.handle) if submitter.handle else UNAVAILABLE}",
                        help="A platform account label: not necessarily a real name, and not proof of sole authorship.")
            others = f" Other handles recorded for this account: {', '.join(submitter.former_handles)}." if submitter.former_handles else ""
            st.caption(
                f"Evidence: {md_escape(submitter.evidence_class)} link from the platform field “{md_escape(submitter.source_field)}”; handle from "
                f"{md_escape(submitter.handle_evidence_source or UNAVAILABLE)}; snapshot acquired {md_escape(format_utc(submitter.snapshot_fetched_at).text)}.{md_escape(others)}"
            )
        render_source_details(detail.source_details, detail.unavailable, key=f"{key}_details")

    with techniques:
        render_labels(detail.technique_labels, unavailable=detail.unavailable)

    with artifacts:
        if detail.artifacts:
            st.caption("“Recorded status” is what QCH stored. The explorer does not open, hash or locate these files, so it cannot say whether a file exists.")
            st.dataframe(pd.DataFrame([{
                "Format": a.format or UNAVAILABLE, "Type": a.artifact_type, "Recorded status": a.recorded_status, "Size (bytes)": fmt_int(a.size_bytes),
                "SHA-256 (recorded)": a.sha256 or UNAVAILABLE, "URI (recorded)": a.uri, "Recorded at (UTC)": display_bare(a.created_at),
            } for a in detail.artifacts]), hide_index=True, width="stretch")
        else:
            st.caption(detail.unavailable.get("artifacts", "No artifact records are stored for this version."))

    with provenance:
        if evaluation:
            _bullets([
                ("Evaluation snapshot", f"{md_escape(evaluation.snapshot_id[:12])}… acquired {md_escape(display_bare(evaluation.snapshot_fetched_at))} UTC"),
                ("Submission commit", md_escape(evaluation.submission_commit_sha) if evaluation.submission_commit_sha else UNAVAILABLE),
                ("Promoted source ref", md_escape(evaluation.promoted_source_ref) if evaluation.promoted_source_ref else UNAVAILABLE),
            ])
        if detail.source_details:
            st.markdown("**Submitter-statement snapshots**")
            st.dataframe(pd.DataFrame([{
                "Snapshot": d.detail_source_id[:12], "Acquisition": d.acquisition or UNAVAILABLE, "Acquired (UTC)": display_bare(d.source_fetched_at), "Latest": "yes" if d.is_latest else "no",
            } for d in detail.source_details]), hide_index=True, width="stretch")
        if detail.git_commits:
            st.markdown("**Git commits**", help="The history importer stores the Git author date as the commit time.")
            st.dataframe(pd.DataFrame([{
                "Repository": g.repository, "Commit": g.commit_sha, "Linked as": ", ".join(g.roles), "Stored commit time (Git author date, UTC)": display_bare(g.stored_commit_time),
            } for g in detail.git_commits]), hide_index=True, width="stretch")
        _bullets([
            ("QCH record status", md_escape(detail.record_status) if detail.record_status else UNAVAILABLE),
            ("QCH stored this version (ingestion)", md_escape(display_bare(detail.discovered_at)) + " UTC"),
        ])


# -- edge ------------------------------------------------------------------------------------------


def _edge_summary_cards(detail: EdgeDetail) -> None:
    official = detail.official
    if official is None:
        st.caption(detail.unavailable.get("official", "Official metrics are not recorded for both endpoints, so no change is computed."))
        return
    first = st.columns(2)
    first[0].metric("ΔQ · peak qubits", fmt_signed(official.q_delta), border=True, help="Target minus source. A negative ΔQ means fewer qubits.")
    first[1].metric("ΔT · avg executed Toffoli", fmt_signed(official.t_delta), border=True, help="Target minus source. A negative ΔT means fewer Toffoli gates.")
    second = st.columns(2)
    second[0].metric("ΔS · official score", fmt_signed(official.s_delta), border=True, help="Target minus source. A negative ΔS means a lower score.")
    second[1].metric("Score reduction", fmt_percent(official.score_reduction_percent), border=True, help=(
        "100 × (source S − target S) ÷ source S. Positive means the score went down, which is an improvement when a lower S is better. "
        "Changes are not colored as good or bad on their own: a lower T may come with more qubits."))


def render_edge_detail(detail: EdgeDetail, *, key: str) -> None:
    edge, source, target = detail.edge, detail.source, detail.target
    st.markdown(f"#### {md_escape(source.short_label)} → {md_escape(target.short_label)}")
    meaning = RELATION_DESCRIPTIONS.get(edge.relation_type, "No description is registered for this relation type.")
    st.markdown(
        f"**{md_escape(relation_label(edge.relation_type))}** · {status_markdown(source.platform_status)} → {status_markdown(target.platform_status)}",
        help=f"Relation type: {edge.relation_type}. {meaning} {RELATION_CAVEAT}",
    )
    _edge_summary_cards(detail)

    comparison, techniques, submission, provenance = st.tabs(["Comparison", "Techniques", "Submission", "Provenance"])

    with comparison:
        official = detail.official
        if official is not None:
            s, t = source.official, target.official
            st.markdown(
                "| | Source | Target | Change |" + NL + "|---|---:|---:|---:|" + NL
                + f"| **Q** qubits | {fmt_int(s.peak_qubits)} | {fmt_int(t.peak_qubits)} | {fmt_signed(official.q_delta)} |" + NL
                + f"| **T** Toffoli | {fmt_int(s.avg_executed_toffoli)} | {fmt_int(t.avg_executed_toffoli)} | {fmt_signed(official.t_delta)} |" + NL
                + f"| **S** score | {fmt_int(s.score)} | {fmt_int(t.score)} | {fmt_signed(official.s_delta)} |"
            )
            st.caption("Official Q, T and S only (Change = target − source); structural Toffoli counts are never substituted.")
        if detail.other_metrics:
            st.markdown("**Other recorded metrics present on both endpoints**", help="Structural metrics; not the platform's official metrics.")
            st.dataframe(pd.DataFrame([{
                "Metric": m.name, "Source": fmt_number(m.source_value), "Target": fmt_number(m.target_value), "Change": fmt_signed(m.delta),
                "Percent change": fmt_percent(m.percent_change), "Unit": m.unit or "",
            } for m in detail.other_metrics]), hide_index=True, width="stretch")
        st.caption(meaning + " " + RELATION_CAVEAT)

    with techniques:
        render_labels(detail.linked_labels, empty_message="No author-assigned label recorded for this edge.", unavailable=detail.unavailable)

    target_detail = detail.target_detail
    with submission:
        if target_detail is None:
            st.caption("Target version details are unavailable.")
        else:
            if target_detail.submitter is None:
                st.caption(target_detail.unavailable.get("submitter", "No submitter is recorded for the target version."))
            else:
                handle = md_escape(target_detail.submitter.handle) if target_detail.submitter.handle else UNAVAILABLE
                st.markdown(f"**Target submitter account:** {handle}", help="A platform account label: not necessarily a real name, and not proof of sole authorship.")
            render_source_details(target_detail.source_details, target_detail.unavailable, key=f"{key}_target", about="the TARGET version of this edge")

    with provenance:
        _bullets([
            ("Relation type (raw)", md_escape(edge.relation_type)),
            ("Edge ID", md_escape(edge.edge_id)),
            ("Source version", md_escape(source.display_key) + f" — {md_escape(source.platform_status or 'status n/a')}"),
            ("Target version", md_escape(target.display_key) + f" — {md_escape(target.platform_status or 'status n/a')}"),
            ("Transformation name", md_escape(edge.transformation_name) if edge.transformation_name else "none stored"),
            ("Stored description", md_escape(edge.description) if edge.description else "none stored"),
            ("Stored confidence", f"{edge.confidence:g}" if edge.confidence is not None else "none stored"),
            ("Edge stored by QCH at (UTC)", md_escape(display_bare(edge.created_at)) + " — ingestion time, not when the change happened"),
        ])
        st.markdown("**Stored evidence**")
        if detail.evidence in ({}, [], "", None):
            st.caption("No evidence stored on this edge.")
        elif isinstance(detail.evidence, (dict, list)):
            st.json(detail.evidence, expanded=False)
        else:
            st.code(verbatim_code_body(str(detail.evidence)), language=None, wrap_lines=True)
