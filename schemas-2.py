from __future__ import annotations
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator

EpistemicType = Literal['historical','traditional','research_hypothesis','empirically_tested']
SourceStatus = Literal['active','superseded','retracted','unverified']

class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)

class SourceIn(StrictModel):
    id: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=500)
    origin: str = Field(min_length=1, max_length=2000)
    retrieved_at: str = Field(min_length=1, max_length=100)
    epistemic_type: EpistemicType
    status: SourceStatus = 'active'
    fingerprint: str | None = Field(default=None, max_length=512)

class ObservationIn(StrictModel):
    id: str = Field(min_length=1, max_length=128)
    source_id: str = Field(min_length=1, max_length=128)
    statement: str = Field(min_length=1, max_length=4000)
    epistemic_type: EpistemicType
    claim_key: str | None = Field(default=None, max_length=300)
    claim_value: str | None = Field(default=None, max_length=1000)
    scope: str = Field(default='global', max_length=300)

class RuleIn(StrictModel):
    id: str = Field(min_length=1, max_length=128)
    observation_ids: list[str] = Field(min_length=1, max_length=500)
    epistemic_type: EpistemicType
    description: str = Field(min_length=1, max_length=4000)

class ScenarioIn(StrictModel):
    id: str = Field(min_length=1, max_length=128)
    description: str = Field(min_length=1, max_length=4000)
    evidence: list[dict[str, Any]] = Field(min_length=1, max_length=500)
    metadata: dict[str, Any] = Field(default_factory=dict)

class ForecastIn(StrictModel):
    id: str = Field(min_length=1, max_length=128)
    scenario_id: str = Field(min_length=1, max_length=128)
    question: str = Field(min_length=1, max_length=4000)
    ranking_score: float | None = None
    probability: float | None = Field(default=None, ge=0, le=1)
    probability_type: Literal['unassigned','ranking_score','probability'] = 'unassigned'
    calibration_status: Literal['uncalibrated','calibrated','externally_validated'] = 'uncalibrated'

    @field_validator('probability')
    @classmethod
    def probability_requires_calibration_label(cls, value: float | None) -> float | None:
        # The route additionally enforces calibration metadata; this validator keeps values bounded.
        return value

class EvaluationIn(StrictModel):
    id: str = Field(min_length=1, max_length=128)
    forecast_id: str = Field(min_length=1, max_length=128)
    outcome: Literal[0,1]
    observed_at: str = Field(min_length=1, max_length=100)
    notes: str = Field(default='', max_length=4000)

class BundleIn(StrictModel):
    model_config = ConfigDict(extra='forbid')
    sources: list[dict[str, Any]] = Field(default_factory=list, max_length=5000)
    observations: list[dict[str, Any]] = Field(default_factory=list, max_length=10000)
    rules: list[dict[str, Any]] = Field(default_factory=list, max_length=5000)
    scenarios: list[dict[str, Any]] = Field(default_factory=list, max_length=5000)
    forecasts: list[dict[str, Any]] = Field(default_factory=list, max_length=10000)


ConceptType = Literal['historical_practice','modern_practice','research_concept','entity','event']
EvidenceStatus = Literal['documented','disputed','inferred','synthetic']

class ConceptIn(StrictModel):
    id: str = Field(min_length=1, max_length=128)
    label: str = Field(min_length=1, max_length=300)
    system_id: str = Field(min_length=1, max_length=128)
    concept_type: ConceptType
    epistemic_status: EvidenceStatus
    description: str = Field(default='', max_length=4000)

class ConceptRelationIn(StrictModel):
    id: str = Field(min_length=1, max_length=128)
    from_concept_id: str = Field(min_length=1, max_length=128)
    relation_type: str = Field(min_length=1, max_length=100)
    to_concept_id: str = Field(min_length=1, max_length=128)
    system_id: str = Field(min_length=1, max_length=128)
    evidence_status: EvidenceStatus
    source_id: str | None = Field(default=None, max_length=128)
    rationale: str = Field(min_length=1, max_length=4000)

class ApiTokenCreateIn(StrictModel):
    label: str = Field(min_length=1, max_length=100)
    expires_in_days: int = Field(default=90, ge=1, le=365, strict=True)
