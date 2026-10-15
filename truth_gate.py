"""Deterministic validation gate for evidence-backed SRX scenarios.

This module validates traceability and structural consistency. It does not judge
whether a source is true, whether a tradition is empirically valid, or whether
a forecast is likely to occur.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
from typing import Any, Iterable

ALLOWED_EPISTEMIC_TYPES = {
    "historical", "traditional", "research_hypothesis", "empirically_tested"
}
ALLOWED_SOURCE_STATUSES = {"active", "superseded", "retracted", "unverified"}
ALLOWED_EVIDENCE_ROLES = {"supports", "contradicts", "context"}


@dataclass(frozen=True)
class Finding:
    code: str
    severity: str
    entity_type: str
    entity_id: str
    message: str


@dataclass(frozen=True)
class GateReport:
    passed: bool
    summary: dict[str, int]
    findings: tuple[Finding, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "summary": self.summary,
            "findings": [asdict(item) for item in self.findings],
        }


def _as_list(bundle: dict[str, Any], key: str) -> list[dict[str, Any]]:
    value = bundle.get(key, [])
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _index(items: Iterable[dict[str, Any]], entity_type: str,
           findings: list[Finding]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in items:
        entity_id = item.get("id")
        if not isinstance(entity_id, str) or not entity_id.strip():
            findings.append(Finding("ID_MISSING", "error", entity_type, "<missing>",
                                    "كل سجل يحتاج معرّف id نصياً غير فارغ."))
            continue
        if entity_id in result:
            findings.append(Finding("ID_DUPLICATE", "error", entity_type, entity_id,
                                    "المعرّف مكرر داخل المجموعة نفسها."))
            continue
        result[entity_id] = item
    return result


def validate_bundle(bundle: dict[str, Any]) -> GateReport:
    """Validate a JSON-like SRX bundle; no network or external dependencies."""
    findings: list[Finding] = []
    if not isinstance(bundle, dict):
        return GateReport(False, {"errors": 1, "warnings": 0}, (
            Finding("BUNDLE_INVALID", "error", "bundle", "<root>",
                    "الجذر يجب أن يكون كائناً JSON."),
        ))

    sources = _index(_as_list(bundle, "sources"), "source", findings)
    observations = _index(_as_list(bundle, "observations"), "observation", findings)
    rules = _index(_as_list(bundle, "rules"), "rule", findings)
    scenarios = _index(_as_list(bundle, "scenarios"), "scenario", findings)
    forecasts = _index(_as_list(bundle, "forecasts"), "forecast", findings)

    # Source records must expose provenance and an explicit epistemic category.
    for sid, source in sources.items():
        for field in ("title", "origin", "retrieved_at"):
            if not isinstance(source.get(field), str) or not source[field].strip():
                findings.append(Finding("SOURCE_PROVENANCE_INCOMPLETE", "error", "source", sid,
                                        f"المصدر يفتقد الحقل الإلزامي {field}."))
        if source.get("epistemic_type") not in ALLOWED_EPISTEMIC_TYPES:
            findings.append(Finding("EPISTEMIC_TYPE_INVALID", "error", "source", sid,
                                    "تصنيف المصدر المعرفي مفقود أو غير معتمد."))
        if source.get("status", "active") not in ALLOWED_SOURCE_STATUSES:
            findings.append(Finding("SOURCE_STATUS_INVALID", "error", "source", sid,
                                    "حالة المصدر غير معروفة."))
        if source.get("status", "active") in {"retracted", "unverified"}:
            findings.append(Finding("SOURCE_NOT_USABLE_AS_SUPPORT", "warning", "source", sid,
                                    "المصدر مسحوب أو غير متحقق؛ لا يُعامل كدليل داعم صالح."))

    # Every observation must link to a known source and include a statement.
    for oid, obs in observations.items():
        source_id = obs.get("source_id")
        if source_id not in sources:
            findings.append(Finding("OBSERVATION_SOURCE_MISSING", "error", "observation", oid,
                                    "الملاحظة تشير إلى مصدر غير موجود."))
        if not isinstance(obs.get("statement"), str) or not obs["statement"].strip():
            findings.append(Finding("OBSERVATION_STATEMENT_MISSING", "error", "observation", oid,
                                    "الملاحظة تحتاج نصاً أو وصفاً قابلاً للمراجعة."))
        if obs.get("epistemic_type") not in ALLOWED_EPISTEMIC_TYPES:
            findings.append(Finding("OBSERVATION_EPISTEMIC_TYPE_INVALID", "error", "observation", oid,
                                    "الملاحظة تحتاج تصنيفاً معرفياً صريحاً."))

    # Rule inputs must be traceable to observations; rules remain distinct from evidence.
    for rid, rule in rules.items():
        refs = rule.get("observation_ids", [])
        if not isinstance(refs, list) or not refs:
            findings.append(Finding("RULE_WITHOUT_EVIDENCE", "error", "rule", rid,
                                    "القاعدة لا تملك أي مراجع ملاحظة."))
            continue
        for oid in refs:
            if oid not in observations:
                findings.append(Finding("RULE_OBSERVATION_MISSING", "error", "rule", rid,
                                        f"القاعدة تشير إلى ملاحظة غير موجودة: {oid}."))
        if rule.get("epistemic_type") not in ALLOWED_EPISTEMIC_TYPES:
            findings.append(Finding("RULE_EPISTEMIC_TYPE_INVALID", "error", "rule", rid,
                                    "القاعدة تحتاج تصنيفاً معرفياً صريحاً."))

    # Scenario evidence is explicit, typed, and cannot silently cite missing records.
    scenario_support_sources: dict[str, set[str]] = defaultdict(set)
    for sid, scenario in scenarios.items():
        evidence = scenario.get("evidence", [])
        if not isinstance(evidence, list) or not evidence:
            findings.append(Finding("SCENARIO_WITHOUT_EVIDENCE", "error", "scenario", sid,
                                    "السيناريو لا يحتوي قائمة أدلة صريحة."))
            continue
        for entry in evidence:
            if not isinstance(entry, dict):
                findings.append(Finding("EVIDENCE_ENTRY_INVALID", "error", "scenario", sid,
                                        "كل دليل يجب أن يكون كائناً يحتوي النوع والمعرّف والدور."))
                continue
            etype, eid, role = entry.get("type"), entry.get("id"), entry.get("role")
            if role not in ALLOWED_EVIDENCE_ROLES:
                findings.append(Finding("EVIDENCE_ROLE_INVALID", "error", "scenario", sid,
                                        "دور الدليل يجب أن يكون supports أو contradicts أو context."))
                continue
            if etype == "source":
                target = sources.get(eid)
                if target is None:
                    findings.append(Finding("SCENARIO_SOURCE_MISSING", "error", "scenario", sid,
                                            f"السيناريو يشير إلى مصدر غير موجود: {eid}."))
                    continue
                if role == "supports":
                    scenario_support_sources[sid].add(eid)
                    if target.get("status", "active") != "active":
                        findings.append(Finding("INACTIVE_SOURCE_USED_AS_SUPPORT", "error", "scenario", sid,
                                                f"المصدر {eid} ليس active لكنه مستخدم للدعم."))
            elif etype == "observation":
                if eid not in observations:
                    findings.append(Finding("SCENARIO_OBSERVATION_MISSING", "error", "scenario", sid,
                                            f"السيناريو يشير إلى ملاحظة غير موجودة: {eid}."))
            elif etype == "rule":
                if eid not in rules:
                    findings.append(Finding("SCENARIO_RULE_MISSING", "error", "scenario", sid,
                                            f"السيناريو يشير إلى قاعدة غير موجودة: {eid}."))
            else:
                findings.append(Finding("EVIDENCE_TYPE_INVALID", "error", "scenario", sid,
                                        f"نوع الدليل غير مدعوم: {etype}."))
        if not isinstance(scenario.get("description"), str) or not scenario["description"].strip():
            findings.append(Finding("SCENARIO_DESCRIPTION_MISSING", "error", "scenario", sid,
                                    "السيناريو يحتاج وصفاً واضحاً."))

    # Duplicate fingerprints identify likely duplicate source records without merging them.
    fingerprint_groups: dict[str, list[str]] = defaultdict(list)
    for sid, source in sources.items():
        fingerprint = source.get("fingerprint")
        if isinstance(fingerprint, str) and fingerprint.strip():
            fingerprint_groups[fingerprint.strip().casefold()].append(sid)
    for ids in fingerprint_groups.values():
        if len(ids) > 1:
            findings.append(Finding("POSSIBLE_DUPLICATE_SOURCES", "warning", "source", ",".join(sorted(ids)),
                                    "مصادر لها fingerprint متطابق؛ راجعها يدوياً ولا تدمجها تلقائياً."))

    # Conflicts are asserted by explicit normalized claim keys and opposing values.
    claims: dict[tuple[str, str], list[tuple[str, str]]] = defaultdict(list)
    for oid, obs in observations.items():
        key, value = obs.get("claim_key"), obs.get("claim_value")
        if isinstance(key, str) and key.strip() and isinstance(value, str) and value.strip():
            claims[(key.strip().casefold(), str(obs.get("scope", "global")).strip().casefold())].append((oid, value.strip().casefold()))
    for (key, scope), rows in claims.items():
        values = {value for _, value in rows}
        if len(values) > 1:
            ids = ",".join(sorted(oid for oid, _ in rows))
            findings.append(Finding("CONFLICTING_OBSERVATIONS", "warning", "observation", ids,
                                    f"قيم متعارضة للمطالبة {key} ضمن النطاق {scope}; لا تُحسم آلياً."))

    for fid, forecast in forecasts.items():
        scenario_id = forecast.get("scenario_id")
        if scenario_id not in scenarios:
            findings.append(Finding("FORECAST_SCENARIO_MISSING", "error", "forecast", fid,
                                    "التنبؤ يشير إلى سيناريو غير موجود."))
        probability = forecast.get("probability")
        if probability is not None and (not isinstance(probability, (int, float)) or isinstance(probability, bool) or not 0 <= probability <= 1):
            findings.append(Finding("FORECAST_PROBABILITY_INVALID", "error", "forecast", fid,
                                    "الاحتمال يجب أن يكون رقماً بين 0 و1."))
        if forecast.get("probability_type") == "ranking_score":
            findings.append(Finding("SCORE_MISLABELED_AS_PROBABILITY", "error", "forecast", fid,
                                    "درجة الترتيب ليست احتمالاً؛ لا تسمّها probability."))
        if forecast.get("probability") is not None and forecast.get("calibration_status") not in {"calibrated", "externally_validated"}:
            findings.append(Finding("PROBABILITY_NOT_CALIBRATED", "warning", "forecast", fid,
                                    "الاحتمال معروض دون حالة معايرة/تحقق خارجي موثقة."))

    errors = sum(f.severity == "error" for f in findings)
    warnings = sum(f.severity == "warning" for f in findings)
    return GateReport(
        passed=errors == 0,
        summary={"sources": len(sources), "observations": len(observations),
                 "rules": len(rules), "scenarios": len(scenarios),
                 "forecasts": len(forecasts), "errors": errors, "warnings": warnings},
        findings=tuple(findings),
    )
