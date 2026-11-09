"""Strict public contracts. Opaque UUIDs prevent accidental names/phones in this store."""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

STATES = set(
    "AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY".split()
)
Ref = Annotated[str, Field(min_length=1, max_length=160, pattern=r"^[a-zA-Z0-9_.:/-]+$")]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Jurisdictions(Strict):
    caller_location: str | None
    caller_residence: str | None
    agent_location: str | None
    additional_states: list[str] = Field(default_factory=list, max_length=51)
    location_confirmed: Literal[True]

    def states(self) -> set[str]:
        return {
            x
            for x in [
                self.caller_location,
                self.caller_residence,
                self.agent_location,
                *self.additional_states,
            ]
            if x
        }


class Context(Strict):
    request_id: UUID
    subject_id: UUID
    campaign_id: UUID
    consent_id: UUID
    operation: Literal["enroll", "verify", "search"]
    purpose: Literal["fraud_prevention"]
    investigation: Literal["impersonation", "duplicate_identity", "synthetic_or_replay"]
    non_litigation_use: Literal[True]
    single_speaker: Literal[True]
    jurisdictions: Jurisdictions
    target_subject_id: UUID | None = None
    caller_channel: Literal[0, 1] | None = None


class AudioRequest(Context):
    audio_b64: str = Field(max_length=5_600_000)


class Switch(Strict):
    enabled: bool = False
    review_reference: Ref


class StatePolicy(Strict):
    enabled: bool = False
    mode: Literal["consent_required"] = "consent_required"
    version: Ref
    review_reference: Ref
    valid_until: datetime
    retention_days: int = Field(default=30, ge=1, le=90)
    allow_search: bool = False
    # No built-in decision threshold. A locally evaluated calibration is mandatory for matches.
    candidate_threshold: float | None = Field(default=None, ge=-1, le=1)
    calibration_reference: Ref | None = None
    calibration_model_id: Ref | None = None

    @field_validator("valid_until")
    @classmethod
    def aware(cls, value):
        if value.tzinfo is None:
            raise ValueError("timezone required")
        return value


class Consent(Strict):
    subject_id: UUID
    campaign_id: UUID
    states: list[str] = Field(min_length=1, max_length=51)
    scopes: list[Literal["enroll", "verify", "search"]] = Field(min_length=1, max_length=3)
    signed_release_reference: Ref
    notice_version: Ref
    policy_versions: dict[str, Ref]
    signed_at: datetime
    expires_at: datetime
    purpose: Literal["fraud_prevention"]
    biometric_notice_acknowledged: Literal[True]
    written_release_attested: Literal[True]

    @field_validator("signed_at", "expires_at")
    @classmethod
    def aware(cls, value):
        if value.tzinfo is None:
            raise ValueError("timezone required")
        return value
