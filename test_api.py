import json
import sqlite3
from srx_api import main

SOURCE = {
    'id':'src-1','title':'Primary record','origin':'https://example.org/source',
    'retrieved_at':'2026-10-13T00:00:00Z','epistemic_type':'historical','status':'active'
}
SCENARIO = {'id':'sc-1','description':'A traceable scenario','evidence':[{'type':'source','id':'src-1','role':'supports'}]}

def test_health_is_public(client):
    r=client.get('/v1/health')
    assert r.status_code == 200 and r.json()['status']=='ok'

def test_ready_reports_database(client):
    r=client.get('/v1/ready')
    assert r.status_code == 200 and r.json()['status']=='ready'

def test_protected_route_rejects_missing_token(client):
    assert client.get('/v1/sources').status_code == 401

def test_source_create_and_duplicate_conflict(client, auth_headers):
    first=client.post('/v1/sources',json=SOURCE,headers=auth_headers)
    assert first.status_code == 201
    duplicate=client.post('/v1/sources',json=SOURCE,headers=auth_headers)
    assert duplicate.status_code == 409
    listed=client.get('/v1/sources',headers=auth_headers).json()
    assert listed['page']['total']==1 and listed['items'][0]['title']=='Primary record'

def test_scenario_cannot_reference_missing_source(client, auth_headers):
    bad={**SCENARIO,'evidence':[{'type':'source','id':'missing','role':'supports'}]}
    r=client.post('/v1/scenarios',json=bad,headers=auth_headers)
    assert r.status_code == 422
    assert r.json()['detail']['code']=='EVIDENCE_GATE_FAILED'

def test_scenario_and_forecast_flow(client, auth_headers):
    assert client.post('/v1/sources',json=SOURCE,headers=auth_headers).status_code==201
    sc=client.post('/v1/scenarios',json=SCENARIO,headers=auth_headers)
    assert sc.status_code==201 and sc.json()['gate_passed'] is True
    f={'id':'f-1','scenario_id':'sc-1','question':'Will event occur?','ranking_score':0.73,'probability_type':'ranking_score'}
    created=client.post('/v1/forecasts',json=f,headers=auth_headers)
    assert created.status_code==201
    assert created.json()['probability'] is None

def test_unvalidated_probability_rejected(client, auth_headers):
    client.post('/v1/sources',json=SOURCE,headers=auth_headers)
    client.post('/v1/scenarios',json=SCENARIO,headers=auth_headers)
    f={'id':'f-1','scenario_id':'sc-1','question':'Question','probability':0.7,'probability_type':'probability','calibration_status':'uncalibrated'}
    r=client.post('/v1/forecasts',json=f,headers=auth_headers)
    assert r.status_code==422 and r.json()['detail']['code']=='PROBABILITY_UNCALIBRATED'

def test_missing_forecast_for_evaluation_rejected(client, auth_headers):
    e={'id':'e-1','forecast_id':'missing','outcome':1,'observed_at':'2026-10-13T00:00:00Z'}
    r=client.post('/v1/evaluations',json=e,headers=auth_headers)
    assert r.status_code==422

def test_invalid_bundle_returns_gate_findings(client, auth_headers):
    bundle={'sources':[{'id':'s1','title':'Only a title'}]}
    r=client.post('/v1/validate-bundle',content=json.dumps(bundle),headers={**auth_headers,'Content-Type':'application/json'})
    assert r.status_code==200
    assert r.json()['passed'] is False and r.json()['persisted'] is False

def test_database_transaction_rolls_back_all_changes(client):
    with main.db.connection() as conn:
        try:
            conn.execute('BEGIN IMMEDIATE')
            conn.execute("INSERT INTO sources(id,title,origin,retrieved_at,epistemic_type,payload_json,created_at) VALUES('atomic','A','origin','now','historical','{}','now')")
            conn.execute("INSERT INTO sources(id,title,origin,retrieved_at,epistemic_type,payload_json,created_at) VALUES('atomic','B','origin','now','historical','{}','now')")
            conn.commit()
        except sqlite3.IntegrityError:
            conn.rollback()
    with main.db.connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM sources WHERE id='atomic'").fetchone()[0] == 0

def test_observation_requires_existing_source(client, auth_headers):
    obs={'id':'o-1','source_id':'missing','statement':'A documented observation','epistemic_type':'historical'}
    r=client.post('/v1/observations',json=obs,headers=auth_headers)
    assert r.status_code==422 and r.json()['detail']['code']=='SOURCE_NOT_FOUND'

def test_observation_rule_scenario_traceability(client, auth_headers):
    assert client.post('/v1/sources',json=SOURCE,headers=auth_headers).status_code==201
    obs={'id':'o-1','source_id':'src-1','statement':'Observed claim','epistemic_type':'historical','claim_key':'event','claim_value':'occurred'}
    assert client.post('/v1/observations',json=obs,headers=auth_headers).status_code==201
    rule={'id':'r-1','observation_ids':['o-1'],'epistemic_type':'research_hypothesis','description':'Rule grounded in an observation'}
    assert client.post('/v1/rules',json=rule,headers=auth_headers).status_code==201
    scenario={'id':'sc-1','description':'Scenario using observation and rule','evidence':[
        {'type':'observation','id':'o-1','role':'supports'},
        {'type':'rule','id':'r-1','role':'context'}
    ]}
    response=client.post('/v1/scenarios',json=scenario,headers=auth_headers)
    assert response.status_code==201 and response.json()['gate_passed'] is True

def test_rule_rejects_missing_observation(client, auth_headers):
    rule={'id':'r-1','observation_ids':['missing'],'epistemic_type':'historical','description':'Missing evidence'}
    r=client.post('/v1/rules',json=rule,headers=auth_headers)
    assert r.status_code==422 and r.json()['detail']['code']=='OBSERVATION_NOT_FOUND'

def test_srX021_example_bundle_validates(client, auth_headers):
    from pathlib import Path
    bundle_path=Path(__file__).resolve().parents[1] / 'examples' / 'valid_bundle.json'
    response=client.post('/v1/validate-bundle',content=bundle_path.read_text(),headers={**auth_headers,'Content-Type':'application/json'})
    assert response.status_code==200
    assert response.json()['passed'] is True
    assert response.json()['persisted'] is False

def test_all_read_collections_require_auth(client):
    for path in ('/v1/observations','/v1/rules','/v1/scenarios','/v1/evaluations'):
        assert client.get(path).status_code == 401

def test_empty_read_collections_have_stable_pagination_shape(client, auth_headers):
    for path in ('/v1/observations','/v1/rules','/v1/scenarios','/v1/evaluations'):
        response = client.get(path + '?limit=10&offset=0', headers=auth_headers)
        assert response.status_code == 200
        assert response.json() == {'items': [], 'page': {'limit': 10, 'offset': 0, 'total': 0}}

def test_observation_read_returns_payload_and_page(client, auth_headers):
    assert client.post('/v1/sources', json=SOURCE, headers=auth_headers).status_code == 201
    observation = {'id':'obs-read','source_id':'src-1','statement':'A verified observation','epistemic_type':'historical','claim_key':'status','claim_value':'verified'}
    assert client.post('/v1/observations', json=observation, headers=auth_headers).status_code == 201
    response = client.get('/v1/observations?limit=1&offset=0', headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body['page'] == {'limit':1,'offset':0,'total':1}
    assert body['items'][0]['id'] == 'obs-read'
    assert body['items'][0]['payload']['claim_key'] == 'status'


def test_read_requires_read_scope(client, signing_material, token_factory):
    token = token_factory(scopes=("srx:write",))
    response = client.get('/v1/sources', headers={'Authorization': f'Bearer {token}'})
    assert response.status_code == 403
    assert response.json()['detail']['code'] == 'INSUFFICIENT_SCOPE'


def test_invalid_oidc_issuer_is_rejected(client, token_factory):
    token = token_factory(iss='https://attacker.example.test/')
    response = client.get('/v1/sources', headers={'Authorization': f'Bearer {token}'})
    assert response.status_code == 401
    assert response.json()['detail']['code'] == 'AUTH_INVALID'


def test_missing_oidc_configuration_fails_closed(client, monkeypatch):
    from types import SimpleNamespace
    from srx_api import security
    monkeypatch.setattr(security, 'settings', SimpleNamespace(oidc_issuer='', oidc_audience='', oidc_jwks_url=''))
    response = client.get('/v1/sources')
    assert response.status_code == 503
    assert response.json()['detail']['code'] == 'AUTH_NOT_CONFIGURED'


def test_audit_identity_comes_from_verified_token_not_request_body(client, auth_headers):
    forged = {**SOURCE, 'actor_subject': 'admin', 'role': 'admin'}
    rejected = client.post('/v1/sources', json=forged, headers=auth_headers)
    assert rejected.status_code == 422  # strict input model forbids caller-supplied audit identity

    created = client.post('/v1/sources', json=SOURCE, headers=auth_headers)
    assert created.status_code == 201
    with main.db.connection() as conn:
        event = conn.execute(
            "SELECT actor_subject, actor_issuer, action, resource_id FROM audit_events WHERE resource_type='source'"
        ).fetchone()
    assert event is not None
    assert event['actor_subject'] == 'user-123'
    assert event['actor_issuer'] == 'https://identity.example.test/'
    assert event['action'] == 'source.create'
    assert event['resource_id'] == 'src-1'


def test_failed_mutation_does_not_write_audit_event(client, auth_headers):
    assert client.post('/v1/sources', json=SOURCE, headers=auth_headers).status_code == 201
    duplicate = client.post('/v1/sources', json=SOURCE, headers=auth_headers)
    assert duplicate.status_code == 409
    with main.db.connection() as conn:
        count = conn.execute("SELECT COUNT(*) FROM audit_events WHERE resource_type='source'").fetchone()[0]
    assert count == 1


def test_expired_oidc_token_is_rejected(client, token_factory):
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    token = token_factory(iat=now - timedelta(hours=2), exp=now - timedelta(hours=1))
    response = client.get('/v1/sources', headers={'Authorization': f'Bearer {token}'})
    assert response.status_code == 401


def test_wrong_oidc_audience_is_rejected(client, token_factory):
    token = token_factory(aud='another-api')
    response = client.get('/v1/sources', headers={'Authorization': f'Bearer {token}'})
    assert response.status_code == 401


def test_invalid_signature_is_rejected(client, token_factory):
    from datetime import datetime, timedelta, timezone
    import jwt
    from cryptography.hazmat.primitives.asymmetric import rsa
    now = datetime.now(timezone.utc)
    attacker_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    token = jwt.encode({
        'iss': 'https://identity.example.test/',
        'aud': 'srx-api-test',
        'sub': 'user-123',
        'iat': now,
        'exp': now + timedelta(minutes=5),
        'scope': 'srx:read srx:write',
    }, attacker_key, algorithm='RS256', headers={'kid': 'test-key'})
    response = client.get('/v1/sources', headers={'Authorization': f'Bearer {token}'})
    assert response.status_code == 401


def test_bearer_scheme_is_case_insensitive(client, signing_material, token_factory):
    token = token_factory()
    response = client.get('/v1/sources', headers={'Authorization': f'bEaReR {token}'})
    assert response.status_code == 200


def test_malformed_authorization_header_is_rejected(client):
    for value in ('Basic abc', 'Bearer', 'Bearer    '):
        response = client.get('/v1/sources', headers={'Authorization': value})
        assert response.status_code == 401
        assert response.json()['detail']['code'] == 'AUTH_REQUIRED'


def test_empty_subject_is_rejected(client, token_factory):
    token = token_factory(sub='   ')
    response = client.get('/v1/sources', headers={'Authorization': f'Bearer {token}'})
    assert response.status_code == 401
    assert response.json()['detail']['code'] == 'AUTH_INVALID'


def test_scp_claim_can_supply_read_scope(client, token_factory):
    token = token_factory(scope='', scp=['srx:read'])
    response = client.get('/v1/sources', headers={'Authorization': f'Bearer {token}'})
    assert response.status_code == 200


def test_write_route_rejects_read_only_token(client, token_factory):
    token = token_factory(scopes=('srx:read',))
    response = client.post('/v1/sources', json=SOURCE, headers={'Authorization': f'Bearer {token}'})
    assert response.status_code == 403
    assert response.json()['detail']['code'] == 'INSUFFICIENT_SCOPE'


def test_concept_relation_requires_existing_concepts(client, auth_headers):
    relation={'id':'rel-1','from_concept_id':'missing-a','relation_type':'associated_with','to_concept_id':'missing-b','system_id':'sys-1','evidence_status':'synthetic','rationale':'Test relation'}
    response=client.post('/v1/concept-relations',json=relation,headers=auth_headers)
    assert response.status_code==422 and response.json()['detail']['code']=='CONCEPT_NOT_FOUND'

def test_concept_relation_enforces_system_boundary_and_source(client, auth_headers):
    a={'id':'c-a','label':'Concept A','system_id':'sys-1','concept_type':'research_concept','epistemic_status':'inferred'}
    b={'id':'c-b','label':'Concept B','system_id':'sys-2','concept_type':'research_concept','epistemic_status':'inferred'}
    assert client.post('/v1/concepts',json=a,headers=auth_headers).status_code==201
    assert client.post('/v1/concepts',json=b,headers=auth_headers).status_code==201
    relation={'id':'rel-1','from_concept_id':'c-a','relation_type':'associated_with','to_concept_id':'c-b','system_id':'sys-1','evidence_status':'synthetic','rationale':'Test relation'}
    response=client.post('/v1/concept-relations',json=relation,headers=auth_headers)
    assert response.status_code==422 and response.json()['detail']['code']=='SYSTEM_BOUNDARY_MISMATCH'

def test_synthetic_relation_can_be_recorded_but_not_claimed_causal(client, auth_headers):
    for cid in ('c-a','c-b'):
        payload={'id':cid,'label':cid,'system_id':'sys-1','concept_type':'modern_practice','epistemic_status':'synthetic'}
        assert client.post('/v1/concepts',json=payload,headers=auth_headers).status_code==201
    relation={'id':'rel-1','from_concept_id':'c-a','relation_type':'illustrates','to_concept_id':'c-b','system_id':'sys-1','evidence_status':'synthetic','rationale':'Invented taxonomy example'}
    response=client.post('/v1/concept-relations',json=relation,headers=auth_headers)
    assert response.status_code==201 and response.json()['causal_claim_validated'] is False
    assert client.get('/v1/concept-relations?system_id=sys-1',headers=auth_headers).json()['page']['total']==1

def test_non_synthetic_relation_requires_source(client, auth_headers):
    for cid in ('c-a','c-b'):
        payload={'id':cid,'label':cid,'system_id':'sys-1','concept_type':'research_concept','epistemic_status':'inferred'}
        client.post('/v1/concepts',json=payload,headers=auth_headers)
    relation={'id':'rel-1','from_concept_id':'c-a','relation_type':'supports','to_concept_id':'c-b','system_id':'sys-1','evidence_status':'inferred','rationale':'Claim without source'}
    response=client.post('/v1/concept-relations',json=relation,headers=auth_headers)
    assert response.status_code==422 and response.json()['detail']['code']=='SOURCE_REQUIRED'


def test_relation_candidates_generate_only_within_requested_system(client, auth_headers):
    for cid in ('p-a','p-b','p-c','other-a','other-b'):
        system = 'sys-main' if cid.startswith('p-') else 'sys-other'
        r=client.post('/v1/concepts',json={'id':cid,'label':cid,'system_id':system,'concept_type':'research_concept','epistemic_status':'inferred'},headers=auth_headers)
        assert r.status_code==201
    for rid, a, b in (('r-ab','p-a','p-b'),('r-bc','p-b','p-c')):
        r=client.post('/v1/concept-relations',json={'id':rid,'from_concept_id':a,'relation_type':'associated_with','to_concept_id':b,'system_id':'sys-main','evidence_status':'synthetic','rationale':'Test-only synthetic edge'},headers=auth_headers)
        assert r.status_code==201
    response=client.get('/v1/concept-relations/candidates?system_id=sys-main&min_hops=2&max_hops=2',headers=auth_headers)
    assert response.status_code==200
    body=response.json()
    assert body['candidate_count']==1
    assert body['candidates'][0]['status']=='hypothesis_unvalidated'
    assert body['candidates'][0]['node_labels']==['p-a','p-b','p-c']
    assert body['candidates'][0]['path'][0]['source_id'] is None
    assert body['candidates'][0]['interpretation'].startswith('مسار علاقات مرشح')

def test_relation_candidates_reject_invalid_hop_range(client, auth_headers):
    response=client.get('/v1/concept-relations/candidates?system_id=sys-main&min_hops=4&max_hops=2',headers=auth_headers)
    assert response.status_code==422 and response.json()['detail']['code']=='INVALID_HOP_RANGE'

def test_relation_candidates_requires_auth(client):
    response=client.get('/v1/concept-relations/candidates?system_id=sys-main')
    assert response.status_code==401


def test_integrity_report_detects_duplicate_fingerprints(client, auth_headers):
    first = {**SOURCE, 'id':'src-a', 'fingerprint':'sha256:example'}
    second = {**SOURCE, 'id':'src-b', 'fingerprint':'sha256:example'}
    assert client.post('/v1/sources', json=first, headers=auth_headers).status_code == 201
    assert client.post('/v1/sources', json=second, headers=auth_headers).status_code == 201
    response = client.get('/v1/integrity/report', headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body['status'] == 'review_required'
    finding = next(item for item in body['findings'] if item['code'] == 'DUPLICATE_SOURCE_FINGERPRINT')
    assert finding['resource_ids'] == ['src-a', 'src-b']
    assert 'not a determination' in body['limitations'][0]


def test_integrity_report_requires_read_auth(client):
    response = client.get('/v1/integrity/report')
    assert response.status_code == 401


def test_integrity_report_clean_database(client, auth_headers):
    response = client.get('/v1/integrity/report', headers=auth_headers)
    assert response.status_code == 200
    assert response.json()['status'] == 'clean'
    assert response.json()['finding_count'] == 0
