import hashlib
import json
import time
from datetime import datetime

from schema import STATES


class Denied(Exception):
    def __init__(self, reason):
        self.reason = reason


def timestamp(value):
    return datetime.fromisoformat(value).timestamp()


def state_policies(store, c, tenant, states, now):
    policies = {}
    if not states or not states <= STATES:
        raise Denied("jurisdiction_unknown")
    for state in sorted(states):
        p = store.config(c, tenant, "state:" + state)
        if not p.get("enabled"):
            raise Denied("state_disabled")
        if timestamp(p["valid_until"]) <= now:
            raise Denied("state_policy_expired")
        policies[state] = p
    return policies


def authorize(store, c, actor, context):
    """Execute before any decoding/inference AND again before emitting/storing results."""
    if not store.settings.enabled:
        raise Denied("globally_disabled")
    now = time.time()
    tenant = actor.tenant
    tenant_switch = store.config(c, tenant, "tenant")
    campaign_switch = store.config(c, tenant, "campaign:" + str(context.campaign_id))
    if not tenant_switch.get("enabled"):
        raise Denied("tenant_disabled")
    if not campaign_switch.get("enabled"):
        raise Denied("campaign_disabled")
    j = context.jurisdictions
    if any(x is None for x in (j.caller_location, j.caller_residence, j.agent_location)):
        raise Denied("jurisdiction_unknown")
    policies = state_policies(store, c, tenant, j.states(), now)
    row = c.execute(
        "SELECT * FROM consent WHERE tenant=? AND id=?", (tenant, str(context.consent_id))
    ).fetchone()
    consent = eligible_consent(row, context.operation, now)
    if row["subject"] != str(context.subject_id) or row["campaign"] != str(context.campaign_id):
        raise Denied("consent_scope_mismatch")
    validate_consent_policy(consent, policies)
    if context.operation == "search" and not all(p["allow_search"] for p in policies.values()):
        raise Denied("search_disabled")
    if context.operation == "verify" and context.target_subject_id is None:
        raise Denied("target_required")
    snapshot = hashlib.sha256(
        json.dumps([tenant_switch, campaign_switch, policies, consent], sort_keys=True).encode()
    ).hexdigest()
    return policies, snapshot


def eligible_consent(row, scope, now):
    if row is None or row["revoked"] or row["expires"] <= now:
        raise Denied("consent_missing_revoked_or_expired")
    consent = json.loads(row["body"])
    if scope not in consent["scopes"]:
        raise Denied("consent_scope_mismatch")
    return consent


def validate_consent_policy(consent, policies):
    for state, policy in policies.items():
        if (
            state not in consent["states"]
            or consent["policy_versions"].get(state) != policy["version"]
        ):
            raise Denied("consent_policy_mismatch")


def candidate_eligible(store, c, actor, row, operation, now):
    try:
        r = c.execute(
            "SELECT * FROM consent WHERE tenant=? AND id=?", (actor.tenant, row["consent"])
        ).fetchone()
        consent = eligible_consent(r, operation, now)
        policies = state_policies(store, c, actor.tenant, set(consent["states"]), now)
        validate_consent_policy(consent, policies)
        if operation == "search" and not all(p["allow_search"] for p in policies.values()):
            return False
        return policies
    except Denied:
        return False
