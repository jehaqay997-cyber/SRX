from __future__ import annotations
import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from .config import settings
from .database import Database, now_iso, row_json
from .schemas import SourceIn, ObservationIn, RuleIn, ScenarioIn, ForecastIn, EvaluationIn, BundleIn, ConceptIn, ConceptRelationIn, ApiTokenCreateIn
from .security import Principal, require_read_scope, require_write_scope
from .request_limits import RequestBodyLimitMiddleware
from .security_headers import SecurityHeadersMiddleware
from .truth_gate import validate_bundle
from .relation_paths import generate_candidates, RelationPathInputError
from .api_tokens import generate_api_token, token_fingerprint

app = FastAPI(
    title='SRX API',
    version='0.25.3',
    description='Evidence-traceable SRX API. Ranking scores are not probabilities.',
    docs_url='/docs' if settings.environment == 'development' else None,
    redoc_url='/redoc' if settings.environment == 'development' else None,
    openapi_url='/openapi.json' if settings.environment == 'development' else None,
)
db = Database(settings.database_path)
app.add_middleware(RequestBodyLimitMiddleware, max_bytes=settings.max_bundle_bytes)
app.add_middleware(SecurityHeadersMiddleware, production=settings.environment.lower() == "production")


def record_audit(conn: sqlite3.Connection, principal: Principal, action: str,
                 resource_type: str, resource_id: str, details: dict[str, Any] | None = None) -> None:
    """Record the authenticated server-verified identity in the same transaction as the mutation."""
    conn.execute(
        'INSERT INTO audit_events(id,actor_subject,actor_issuer,action,resource_type,resource_id,created_at,details_json) VALUES(?,?,?,?,?,?,?,?)',
        (str(uuid.uuid4()), principal.subject, principal.issuer, action, resource_type,
         resource_id, now_iso(), json.dumps(details or {}, ensure_ascii=False, sort_keys=True)),
    )

@app.exception_handler(sqlite3.IntegrityError)
async def integrity_error_handler(request: Request, exc: sqlite3.IntegrityError):
    # Do not expose SQL or database internals to clients.
    return JSONResponse(status_code=409, content={"error":{"code":"DATA_CONFLICT","message":"The request conflicts with stored data or a referential-integrity rule."}})


@app.get('/v1/concepts', dependencies=[Depends(require_read_scope)])
def list_concepts(limit: int = Query(default=20, ge=1, le=100), offset: int = Query(default=0, ge=0, le=9_007_199_254_740_991)) -> dict[str, Any]:
    with db.connection() as conn:
        total = conn.execute('SELECT COUNT(*) FROM concepts').fetchone()[0]
        rows = conn.execute('SELECT id,label,system_id,concept_type,epistemic_status,description,payload_json,created_at FROM concepts ORDER BY created_at DESC,id ASC LIMIT ? OFFSET ?', (limit, offset)).fetchall()
    return {"items":[row_json(r) for r in rows],"page":{"limit":limit,"offset":offset,"total":total}}

@app.post('/v1/concepts', status_code=201)
def create_concept(payload: ConceptIn, principal: Principal = Depends(require_write_scope)) -> dict[str, Any]:
    now = now_iso()
    raw = payload.model_dump()
    with db.transaction() as conn:
        conn.execute('INSERT INTO concepts(id,label,system_id,concept_type,epistemic_status,description,payload_json,created_at) VALUES(?,?,?,?,?,?,?,?)',
                     (payload.id,payload.label,payload.system_id,payload.concept_type,payload.epistemic_status,payload.description,json.dumps(raw,ensure_ascii=False),now))
        record_audit(conn, principal, 'concept.create', 'concept', payload.id, {'system_id': payload.system_id, 'epistemic_status': payload.epistemic_status})
    return {**raw,"created_at":now}

@app.get('/v1/concept-relations', dependencies=[Depends(require_read_scope)])
def list_concept_relations(limit: int = Query(default=20, ge=1, le=100), offset: int = Query(default=0, ge=0, le=9_007_199_254_740_991), system_id: str | None = Query(default=None, max_length=128)) -> dict[str, Any]:
    with db.connection() as conn:
        if system_id is None:
            total = conn.execute('SELECT COUNT(*) FROM concept_relations').fetchone()[0]
            rows = conn.execute('SELECT id,from_concept_id,relation_type,to_concept_id,system_id,evidence_status,source_id,rationale,payload_json,created_at FROM concept_relations ORDER BY created_at DESC,id ASC LIMIT ? OFFSET ?', (limit,offset)).fetchall()
        else:
            total = conn.execute('SELECT COUNT(*) FROM concept_relations WHERE system_id=?',(system_id,)).fetchone()[0]
            rows = conn.execute('SELECT id,from_concept_id,relation_type,to_concept_id,system_id,evidence_status,source_id,rationale,payload_json,created_at FROM concept_relations WHERE system_id=? ORDER BY created_at DESC,id ASC LIMIT ? OFFSET ?', (system_id,limit,offset)).fetchall()
    return {"items":[row_json(r) for r in rows],"page":{"limit":limit,"offset":offset,"total":total}}

@app.post('/v1/concept-relations', status_code=201)
def create_concept_relation(payload: ConceptRelationIn, principal: Principal = Depends(require_write_scope)) -> dict[str, Any]:
    with db.connection() as conn:
        rows = conn.execute('SELECT id,system_id FROM concepts WHERE id IN (?,?)',(payload.from_concept_id,payload.to_concept_id)).fetchall()
        concepts = {r['id']:r['system_id'] for r in rows}
        if payload.from_concept_id not in concepts or payload.to_concept_id not in concepts:
            raise HTTPException(status_code=422, detail={"code":"CONCEPT_NOT_FOUND","message":"Both relation endpoints must reference existing concepts."})
        if concepts[payload.from_concept_id] != payload.system_id or concepts[payload.to_concept_id] != payload.system_id:
            raise HTTPException(status_code=422, detail={"code":"SYSTEM_BOUNDARY_MISMATCH","message":"Both concepts and the relation must share the same system_id."})
        if payload.evidence_status != 'synthetic' and payload.source_id is None:
            raise HTTPException(status_code=422, detail={"code":"SOURCE_REQUIRED","message":"Non-synthetic relations require a source documenting the relation claim."})
        if payload.source_id is not None and conn.execute('SELECT 1 FROM sources WHERE id=?',(payload.source_id,)).fetchone() is None:
            raise HTTPException(status_code=422, detail={"code":"SOURCE_NOT_FOUND","message":"The relation source must exist."})
    now = now_iso()
    raw = payload.model_dump()
    with db.transaction() as conn:
        conn.execute('INSERT INTO concept_relations(id,from_concept_id,relation_type,to_concept_id,system_id,evidence_status,source_id,rationale,payload_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)',
                     (payload.id,payload.from_concept_id,payload.relation_type,payload.to_concept_id,payload.system_id,payload.evidence_status,payload.source_id,payload.rationale,json.dumps(raw,ensure_ascii=False),now))
        record_audit(conn, principal, 'concept_relation.create', 'concept_relation', payload.id, {'system_id':payload.system_id,'evidence_status':payload.evidence_status,'source_id':payload.source_id})
    return {**raw,"created_at":now,"causal_claim_validated":False}


@app.get('/v1/concept-relations/candidates', dependencies=[Depends(require_read_scope)])
def list_relation_candidates(system_id: str = Query(min_length=1, max_length=128), min_hops: int = Query(default=2, ge=2, le=5), max_hops: int = Query(default=3, ge=2, le=5)) -> dict[str, Any]:
    """Generate bounded candidate paths within one explicitly selected system."""
    if min_hops > max_hops:
        raise HTTPException(status_code=422, detail={"code":"INVALID_HOP_RANGE","message":"Require min_hops <= max_hops <= 5."})
    with db.connection() as conn:
        node_rows = conn.execute('SELECT id,label FROM concepts WHERE system_id=? ORDER BY id', (system_id,)).fetchall()
        edge_rows = conn.execute('SELECT id,from_concept_id,relation_type,to_concept_id,system_id,evidence_status,source_id FROM concept_relations WHERE system_id=? ORDER BY id', (system_id,)).fetchall()
    nodes = [dict(row) for row in node_rows]
    edges = [{"id":r['id'],"from":r['from_concept_id'],"relation":r['relation_type'],"to":r['to_concept_id'],
              "system_id":r['system_id'],"source_id":r['source_id'],"evidence_status":r['evidence_status']} for r in edge_rows]
    try:
        candidates = generate_candidates(system_id, nodes, edges, min_hops=min_hops, max_hops=max_hops)
    except RelationPathInputError as exc:
        raise HTTPException(status_code=422, detail={"code":"RELATION_GRAPH_LIMIT","message":str(exc)}) from None
    return {"engine":"SRX-012-integrated","system_id":system_id,"candidate_count":len(candidates),
            "truncated":len(candidates)>=1000,"status":"hypothesis_unvalidated",
            "warning":"Candidate paths are not validated causal relations, rules, or forecasts.","candidates":candidates}

@app.post('/v1/api-tokens', status_code=201)
def create_api_token(payload: ApiTokenCreateIn, principal: Principal = Depends(require_write_scope)) -> dict[str, Any]:
    """Create an owner-bound token. The raw secret is returned once and never persisted."""
    label = payload.label
    expires_in_days = payload.expires_in_days
    pepper = settings.api_token_pepper
    if len(pepper.encode("utf-8")) < 32:
        raise HTTPException(status_code=503, detail={"code":"TOKEN_SERVICE_NOT_CONFIGURED","message":"SRX_API_TOKEN_PEPPER must be configured with at least 32 bytes."})
    token = generate_api_token()
    fingerprint = token_fingerprint(token, pepper)
    token_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    created_at = now.isoformat()
    expires_at = (now + timedelta(days=expires_in_days)).isoformat()
    with db.transaction() as conn:
        conn.execute('INSERT INTO api_tokens(id,owner_subject,owner_issuer,label,fingerprint,token_prefix,created_at,expires_at,revoked_at,last_used_at) VALUES(?,?,?,?,?,?,?,?,NULL,NULL)',
                     (token_id, principal.subject, principal.issuer, label.strip(), fingerprint, token[:12], created_at, expires_at))
        record_audit(conn, principal, 'api_token.create', 'api_token', token_id, {"label":label.strip(),"expires_at":expires_at})
    return {"id":token_id,"token":token,"label":label.strip(),"created_at":created_at,"expires_at":expires_at,"warning":"Copy this secret now; it cannot be retrieved again."}

@app.get('/v1/api-tokens', dependencies=[Depends(require_read_scope)])
def list_api_tokens(principal: Principal = Depends(require_read_scope)) -> dict[str, Any]:
    with db.connection() as conn:
        rows = conn.execute('SELECT id,label,token_prefix,created_at,expires_at,revoked_at,last_used_at FROM api_tokens WHERE owner_issuer=? AND owner_subject=? ORDER BY created_at DESC,id ASC', (principal.issuer, principal.subject)).fetchall()
    now = datetime.now(timezone.utc)
    items = []
    for row in rows:
        item = dict(row)
        item["status"] = "revoked" if item["revoked_at"] else ("expired" if datetime.fromisoformat(item["expires_at"]) <= now else "active")
        items.append(item)
    return {"items":items,"count":len(items)}

@app.post('/v1/api-tokens/{token_id}/revoke')
def revoke_api_token(token_id: str, principal: Principal = Depends(require_write_scope)) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    with db.transaction() as conn:
        row = conn.execute('SELECT id,revoked_at FROM api_tokens WHERE id=? AND owner_issuer=? AND owner_subject=?', (token_id, principal.issuer, principal.subject)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail={"code":"API_TOKEN_NOT_FOUND","message":"API token not found."})
        if row["revoked_at"] is None:
            conn.execute('UPDATE api_tokens SET revoked_at=? WHERE id=?', (now, token_id))
            record_audit(conn, principal, 'api_token.revoke', 'api_token', token_id)
    return {"id":token_id,"status":"revoked","revoked_at":now if row["revoked_at"] is None else row["revoked_at"]}

@app.get('/v1/health')
def health() -> dict[str, str]:
    return {"status":"ok","service":"srx-api","version":app.version}

@app.get('/v1/ready')
def ready() -> JSONResponse:
    if not db.readiness():
        return JSONResponse(status_code=503, content={"status":"not_ready"})
    return JSONResponse(status_code=200, content={"status":"ready","database":"ok"})

@app.get('/v1/sources', dependencies=[Depends(require_read_scope)])
def list_sources(limit: int = Query(default=20, ge=1, le=100), offset: int = Query(default=0, ge=0, le=9_007_199_254_740_991)) -> dict[str, Any]:
    with db.connection() as conn:
        total = conn.execute('SELECT COUNT(*) FROM sources').fetchone()[0]
        rows = conn.execute('SELECT id,title,origin,retrieved_at,epistemic_type,status,fingerprint,created_at FROM sources ORDER BY created_at DESC,id ASC LIMIT ? OFFSET ?', (limit, offset)).fetchall()
    return {"items":[dict(r) for r in rows],"page":{"limit":limit,"offset":offset,"total":total}}

@app.get('/v1/observations', dependencies=[Depends(require_read_scope)])
def list_observations(limit: int = Query(default=20, ge=1, le=100), offset: int = Query(default=0, ge=0, le=9_007_199_254_740_991)) -> dict[str, Any]:
    with db.connection() as conn:
        total = conn.execute('SELECT COUNT(*) FROM observations').fetchone()[0]
        rows = conn.execute('SELECT id,source_id,statement,epistemic_type,payload_json,created_at FROM observations ORDER BY created_at DESC,id ASC LIMIT ? OFFSET ?', (limit, offset)).fetchall()
    return {"items":[row_json(r) for r in rows],"page":{"limit":limit,"offset":offset,"total":total}}

@app.get('/v1/rules', dependencies=[Depends(require_read_scope)])
def list_rules(limit: int = Query(default=20, ge=1, le=100), offset: int = Query(default=0, ge=0, le=9_007_199_254_740_991)) -> dict[str, Any]:
    with db.connection() as conn:
        total = conn.execute('SELECT COUNT(*) FROM rules').fetchone()[0]
        rows = conn.execute('SELECT id,epistemic_type,payload_json,created_at FROM rules ORDER BY created_at DESC,id ASC LIMIT ? OFFSET ?', (limit, offset)).fetchall()
    return {"items":[row_json(r) for r in rows],"page":{"limit":limit,"offset":offset,"total":total}}

@app.get('/v1/scenarios', dependencies=[Depends(require_read_scope)])
def list_scenarios(limit: int = Query(default=20, ge=1, le=100), offset: int = Query(default=0, ge=0, le=9_007_199_254_740_991)) -> dict[str, Any]:
    with db.connection() as conn:
        total = conn.execute('SELECT COUNT(*) FROM scenarios').fetchone()[0]
        rows = conn.execute('SELECT id,description,payload_json,created_at FROM scenarios ORDER BY created_at DESC,id ASC LIMIT ? OFFSET ?', (limit, offset)).fetchall()
    return {"items":[row_json(r) for r in rows],"page":{"limit":limit,"offset":offset,"total":total}}

@app.get('/v1/evaluations', dependencies=[Depends(require_read_scope)])
def list_evaluations(limit: int = Query(default=20, ge=1, le=100), offset: int = Query(default=0, ge=0, le=9_007_199_254_740_991)) -> dict[str, Any]:
    with db.connection() as conn:
        total = conn.execute('SELECT COUNT(*) FROM evaluations').fetchone()[0]
        rows = conn.execute('SELECT id,forecast_id,outcome,observed_at,payload_json,created_at FROM evaluations ORDER BY created_at DESC,id ASC LIMIT ? OFFSET ?', (limit, offset)).fetchall()
    return {"items":[row_json(r) for r in rows],"page":{"limit":limit,"offset":offset,"total":total}}

@app.get('/v1/integrity/report', dependencies=[Depends(require_read_scope)])
def integrity_report() -> dict[str, Any]:
    """Read-only source-of-truth checks; findings are warnings, never automatic proof of falsity."""
    findings: list[dict[str, Any]] = []
    with db.connection() as conn:
        duplicate_fingerprints = conn.execute(
            "SELECT fingerprint, GROUP_CONCAT(id) AS ids, COUNT(*) AS n FROM sources "
            "WHERE fingerprint IS NOT NULL AND trim(fingerprint) <> '' "
            "GROUP BY fingerprint HAVING COUNT(*) > 1 ORDER BY fingerprint"
        ).fetchall()
        for row in duplicate_fingerprints:
            findings.append({"code":"DUPLICATE_SOURCE_FINGERPRINT","severity":"warning",
                "resource_type":"source_group","resource_ids":sorted(row['ids'].split(',')),
                "message":"Multiple source records share a fingerprint; review whether these are duplicate captures or legitimate versions."})

        inactive_references = conn.execute(
            "SELECT DISTINCT s.id, s.status FROM sources s WHERE s.status <> 'active' AND ("
            "EXISTS (SELECT 1 FROM observations o WHERE o.source_id=s.id) OR "
            "EXISTS (SELECT 1 FROM concept_relations cr WHERE cr.source_id=s.id)) ORDER BY s.id"
        ).fetchall()
        for row in inactive_references:
            findings.append({"code":"NON_ACTIVE_SOURCE_IN_USE","severity":"warning",
                "resource_type":"source","resource_id":row['id'],"source_status":row['status'],
                "message":"An observation or concept relation references a source that is not active; downstream consumers should surface this status."})

        duplicate_relations = conn.execute(
            "SELECT from_concept_id, relation_type, to_concept_id, system_id, "
            "GROUP_CONCAT(id) AS ids, COUNT(*) AS n FROM concept_relations "
            "GROUP BY from_concept_id, relation_type, to_concept_id, system_id "
            "HAVING COUNT(*) > 1 ORDER BY system_id, from_concept_id, relation_type, to_concept_id"
        ).fetchall()
        for row in duplicate_relations:
            findings.append({"code":"DUPLICATE_CONCEPT_RELATION","severity":"review",
                "resource_type":"concept_relation_group","resource_ids":sorted(row['ids'].split(',')),
                "system_id":row['system_id'],"message":"Repeated directed relation tuple found; inspect whether records represent independent evidence or accidental duplication."})

    findings.sort(key=lambda item: (item['code'], item.get('resource_id', ''), ','.join(item.get('resource_ids', []))))
    return {"report_version":"1","status":"review_required" if findings else "clean",
            "finding_count":len(findings),"checks":["duplicate_source_fingerprints",
            "non_active_sources_in_use","duplicate_directed_concept_relations"],
            "limitations":["A finding is a review signal, not a determination that a claim is false.",
            "Fingerprint equality depends on the caller's fingerprinting method.",
            "This report does not establish source authenticity or causal validity."],"findings":findings}


@app.post('/v1/sources', status_code=201)
def create_source(payload: SourceIn, principal: Principal = Depends(require_write_scope)) -> dict[str, Any]:
    now = now_iso()
    raw = payload.model_dump()
    with db.transaction() as conn:
        conn.execute('INSERT INTO sources(id,title,origin,retrieved_at,epistemic_type,status,fingerprint,payload_json,created_at) VALUES(?,?,?,?,?,?,?,?,?)',
                     (payload.id,payload.title,payload.origin,payload.retrieved_at,payload.epistemic_type,payload.status,payload.fingerprint,json.dumps(raw,ensure_ascii=False),now))
        record_audit(conn, principal, 'source.create', 'source', payload.id)
    return {**raw,"created_at":now}

@app.post('/v1/observations', status_code=201)
def create_observation(payload: ObservationIn, principal: Principal = Depends(require_write_scope)) -> dict[str, Any]:
    with db.connection() as conn:
        exists = conn.execute('SELECT id FROM sources WHERE id=?', (payload.source_id,)).fetchone()
    if exists is None:
        raise HTTPException(status_code=422, detail={"code":"SOURCE_NOT_FOUND","message":"Observation must reference an existing source."})
    now = now_iso()
    raw = payload.model_dump()
    with db.transaction() as conn:
        conn.execute('INSERT INTO observations(id,source_id,statement,epistemic_type,payload_json,created_at) VALUES(?,?,?,?,?,?)',
                     (payload.id,payload.source_id,payload.statement,payload.epistemic_type,json.dumps(raw,ensure_ascii=False),now))
        record_audit(conn, principal, 'observation.create', 'observation', payload.id, {'source_id': payload.source_id})
    return {**raw,"created_at":now}

@app.post('/v1/rules', status_code=201)
def create_rule(payload: RuleIn, principal: Principal = Depends(require_write_scope)) -> dict[str, Any]:
    with db.connection() as conn:
        known = {r['id'] for r in conn.execute('SELECT id FROM observations').fetchall()}
    missing = sorted(set(payload.observation_ids) - known)
    if missing:
        raise HTTPException(status_code=422, detail={"code":"OBSERVATION_NOT_FOUND","message":"Rule references missing observations.","missing_ids":missing})
    now = now_iso()
    raw = payload.model_dump()
    with db.transaction() as conn:
        conn.execute('INSERT INTO rules(id,epistemic_type,payload_json,created_at) VALUES(?,?,?,?)',
                     (payload.id,payload.epistemic_type,json.dumps(raw,ensure_ascii=False),now))
        record_audit(conn, principal, 'rule.create', 'rule', payload.id, {'observation_ids': payload.observation_ids})
    return {**raw,"created_at":now}

async def read_limited_body(request: Request, max_bytes: int) -> bytes:
    """Read a request body incrementally and stop as soon as the configured limit is crossed."""
    content_length = request.headers.get('content-length')
    if content_length is not None:
        try:
            declared_length = int(content_length)
        except ValueError:
            raise HTTPException(status_code=400, detail={"code":"INVALID_CONTENT_LENGTH","message":"Content-Length must be a non-negative integer."}) from None
        if declared_length < 0:
            raise HTTPException(status_code=400, detail={"code":"INVALID_CONTENT_LENGTH","message":"Content-Length must be a non-negative integer."})
        if declared_length > max_bytes:
            raise HTTPException(status_code=413, detail={"code":"BUNDLE_TOO_LARGE","message":"Bundle exceeds configured size limit."})

    chunks: list[bytes] = []
    total_bytes = 0
    async for chunk in request.stream():
        total_bytes += len(chunk)
        if total_bytes > max_bytes:
            raise HTTPException(status_code=413, detail={"code":"BUNDLE_TOO_LARGE","message":"Bundle exceeds configured size limit."})
        chunks.append(chunk)
    return b''.join(chunks)


@app.post('/v1/validate-bundle', dependencies=[Depends(require_write_scope)])
async def validate_import_bundle(request: Request) -> dict[str, Any]:
    raw = await read_limited_body(request, settings.max_bundle_bytes)
    try:
        data = json.loads(raw)
        BundleIn.model_validate(data)
    except (json.JSONDecodeError, ValidationError, TypeError):
        raise HTTPException(status_code=422, detail={"code":"BUNDLE_SCHEMA_INVALID","message":"Bundle is not valid JSON matching the SRX bundle schema."})
    report = validate_bundle(data).to_dict()
    return {"gate":"srx-021","passed":report['passed'],"summary":report['summary'],"findings":report['findings'],"persisted":False}

@app.post('/v1/scenarios', status_code=201)
def create_scenario(payload: ScenarioIn, principal: Principal = Depends(require_write_scope)) -> dict[str, Any]:
    # Validate this scenario against the current evidence graph; never accept dangling references.
    with db.connection() as conn:
        sources = [dict(r) for r in conn.execute('SELECT id,title,origin,retrieved_at,epistemic_type,status,fingerprint FROM sources').fetchall()]
        observations = [json.loads(r['payload_json']) for r in conn.execute('SELECT payload_json FROM observations').fetchall()]
        rules = [json.loads(r['payload_json']) for r in conn.execute('SELECT payload_json FROM rules').fetchall()]
    bundle = {"sources":sources,"observations":observations,"rules":rules,"scenarios":[payload.model_dump()],"forecasts":[]}
    report = validate_bundle(bundle).to_dict()
    if not report['passed']:
        raise HTTPException(status_code=422, detail={"code":"EVIDENCE_GATE_FAILED","message":"Scenario failed evidence traceability validation.","report":report})
    now = now_iso()
    raw = payload.model_dump()
    with db.transaction() as conn:
        conn.execute('INSERT INTO scenarios(id,description,payload_json,created_at) VALUES(?,?,?,?)', (payload.id,payload.description,json.dumps(raw,ensure_ascii=False),now))
        record_audit(conn, principal, 'scenario.create', 'scenario', payload.id)
    return {**raw,"created_at":now,"gate_passed":True}

@app.post('/v1/forecasts', status_code=201)
def create_forecast(payload: ForecastIn, principal: Principal = Depends(require_write_scope)) -> dict[str, Any]:
    if payload.probability is not None and payload.calibration_status not in {'calibrated','externally_validated'}:
        raise HTTPException(status_code=422, detail={"code":"PROBABILITY_UNCALIBRATED","message":"A probability requires calibrated or externally validated status; otherwise submit a ranking_score only."})
    if payload.probability_type == 'ranking_score' and payload.probability is not None:
        raise HTTPException(status_code=422, detail={"code":"SCORE_MISLABELED","message":"A ranking score cannot be stored as a probability."})
    if payload.probability is not None and payload.probability_type != 'probability':
        raise HTTPException(status_code=422, detail={"code":"PROBABILITY_TYPE_REQUIRED","message":"Set probability_type='probability' when submitting a calibrated probability."})
    with db.connection() as conn:
        row = conn.execute('SELECT payload_json FROM scenarios WHERE id=?',(payload.scenario_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=422, detail={"code":"SCENARIO_NOT_FOUND","message":"Forecast must reference an existing validated scenario."})
    now = now_iso()
    raw = payload.model_dump()
    with db.transaction() as conn:
        conn.execute('INSERT INTO forecasts(id,scenario_id,question,ranking_score,probability,probability_type,calibration_status,payload_json,created_at) VALUES(?,?,?,?,?,?,?,?,?)',
                     (payload.id,payload.scenario_id,payload.question,payload.ranking_score,payload.probability,payload.probability_type,payload.calibration_status,json.dumps(raw,ensure_ascii=False),now))
        record_audit(conn, principal, 'forecast.create', 'forecast', payload.id, {'scenario_id': payload.scenario_id})
    return {**raw,"created_at":now}

@app.post('/v1/evaluations', status_code=201)
def create_evaluation(payload: EvaluationIn, principal: Principal = Depends(require_write_scope)) -> dict[str, Any]:
    with db.connection() as conn:
        row = conn.execute('SELECT id FROM forecasts WHERE id=?',(payload.forecast_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=422, detail={"code":"FORECAST_NOT_FOUND","message":"Evaluation must reference an existing forecast."})
    now = now_iso()
    raw = payload.model_dump()
    with db.transaction() as conn:
        conn.execute('INSERT INTO evaluations(id,forecast_id,outcome,observed_at,payload_json,created_at) VALUES(?,?,?,?,?,?)',
                     (payload.id,payload.forecast_id,payload.outcome,payload.observed_at,json.dumps(raw,ensure_ascii=False),now))
        record_audit(conn, principal, 'evaluation.create', 'evaluation', payload.id, {'forecast_id': payload.forecast_id})
    return {**raw,"created_at":now}


@app.get('/v1/forecast-quality', dependencies=[Depends(require_read_scope)])
def forecast_quality_report() -> dict[str, Any]:
    """Summarize observed Brier score for explicitly stored probability forecasts.

    This is a descriptive score over recorded outcomes, not proof of calibration.
    Ranking scores and forecasts without probabilities are excluded.
    """
    with db.connection() as conn:
        rows = conn.execute(
            "SELECT f.id AS forecast_id, f.probability AS probability, e.outcome AS outcome "
            "FROM forecasts f JOIN evaluations e ON e.forecast_id = f.id "
            "WHERE f.probability_type = 'probability' AND f.probability IS NOT NULL "
            "ORDER BY e.observed_at ASC, e.id ASC"
        ).fetchall()
    if not rows:
        return {
            "status": "insufficient_data", "sample_count": 0, "brier_score": None,
            "interpretation": "No evaluated forecasts with explicitly typed probabilities are available.",
            "limitations": [
                "Ranking scores are excluded and are not probabilities.",
                "A Brier score alone does not establish calibration or predictive skill.",
                "Repeated evaluations of one forecast count as separate recorded outcomes.",
            ],
        }
    score = sum((float(row['probability']) - int(row['outcome'])) ** 2 for row in rows) / len(rows)
    return {
        "status": "descriptive_only", "sample_count": len(rows),
        "distinct_forecast_count": len({row['forecast_id'] for row in rows}),
        "brier_score": round(score, 8),
        "interpretation": "Lower is better on this recorded sample; compare against a justified baseline before claiming skill.",
        "limitations": [
            "This report does not certify calibration or external validity.",
            "Selection bias, outcome quality, and dependence between forecasts are not corrected.",
            "Ranking scores are excluded and are not probabilities.",
        ],
    }

@app.get('/v1/forecasts', dependencies=[Depends(require_read_scope)])
def list_forecasts(limit: int = Query(default=20, ge=1, le=100), offset: int = Query(default=0, ge=0, le=9_007_199_254_740_991)) -> dict[str, Any]:
    with db.connection() as conn:
        total = conn.execute('SELECT COUNT(*) FROM forecasts').fetchone()[0]
        rows = conn.execute('SELECT id,scenario_id,question,ranking_score,probability,probability_type,calibration_status,created_at FROM forecasts ORDER BY created_at DESC,id ASC LIMIT ? OFFSET ?', (limit,offset)).fetchall()
    return {"items":[dict(r) for r in rows],"page":{"limit":limit,"offset":offset,"total":total}}
