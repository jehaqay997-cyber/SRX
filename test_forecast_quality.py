from srx_api import main


def seed_quality_rows():
    with main.db.transaction() as conn:
        conn.execute("INSERT INTO scenarios(id,description,payload_json,created_at) VALUES(?,?,?,?)", ("quality-scenario", "Test scenario", '{"evidence":[{"kind":"test"}]}', "2026-01-01T00:00:00+00:00"))
        forecasts = [
            ("quality-f1", "probability", 0.8, "calibrated"),
            ("quality-f2", "probability", 0.3, "externally_validated"),
            ("quality-score", "ranking_score", None, "uncalibrated"),
        ]
        for fid, ptype, probability, status in forecasts:
            conn.execute("INSERT INTO forecasts(id,scenario_id,question,ranking_score,probability,probability_type,calibration_status,payload_json,created_at) VALUES(?,?,?,?,?,?,?,?,?)", (fid, "quality-scenario", "Test?", 0.5, probability, ptype, status, "{}", "2026-01-01T00:00:00+00:00"))
        for eid, fid, outcome in [("quality-e1", "quality-f1", 1), ("quality-e2", "quality-f2", 0)]:
            conn.execute("INSERT INTO evaluations(id,forecast_id,outcome,observed_at,payload_json,created_at) VALUES(?,?,?,?,?,?)", (eid, fid, outcome, "2026-01-02T00:00:00+00:00", "{}", "2026-01-02T00:00:00+00:00"))


def test_forecast_quality_uses_only_explicit_probabilities(client, auth_headers):
    seed_quality_rows()
    response = client.get('/v1/forecast-quality', headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body['status'] == 'descriptive_only'
    assert body['sample_count'] == 2
    assert body['distinct_forecast_count'] == 2
    assert body['brier_score'] == 0.065
    assert any('does not certify' in item for item in body['limitations'])


def test_forecast_quality_empty_sample_is_explicit(client, auth_headers):
    response = client.get('/v1/forecast-quality', headers=auth_headers)
    assert response.status_code == 200
    assert response.json()['status'] == 'insufficient_data'
    assert response.json()['brier_score'] is None


def test_forecast_quality_requires_read_auth(client):
    response = client.get('/v1/forecast-quality')
    assert response.status_code == 401
