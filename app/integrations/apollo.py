from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from app.agent.lead_intelligence import enrich_profile_from_text, extract_industry, extract_role
from app.config.settings import get_settings

logger = logging.getLogger(__name__)

# Apollo requires POST + X-Api-Key header (query-string api_key is deprecated).
_APOLLO_PEOPLE_MATCH_URL = "https://api.apollo.io/api/v1/people/match"
_APOLLO_ORG_ENRICH_URL = "https://api.apollo.io/api/v1/organizations/enrich"
_TIMEOUT = 8.0
_IP_ENRICH_TIMEOUT = 3.0  # tight budget — must not delay first response


def _apollo_headers(api_key: str) -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "Cache-Control": "no-cache",
        "accept": "application/json",
        "X-Api-Key": api_key,
    }


def _apollo_match_body(lead_profile: dict[str, Any]) -> dict[str, str]:
    email = (lead_profile.get("email") or "").strip()
    company = (lead_profile.get("company") or "").strip()
    body: dict[str, str] = {}
    if email:
        body["email"] = email
    if company:
        body["organization_name"] = company
    return body


def _apollo_lookup_inputs(lead_profile: dict[str, Any]) -> tuple[str, dict[str, str]] | None:
    settings = get_settings()
    api_key = (settings.apollo_api_key or "").strip()
    body = _apollo_match_body(lead_profile)
    if not api_key or not body:
        return None
    return api_key, body


def _parse_person_response(data: dict[str, Any]) -> dict[str, Any]:
    person = data.get("person") or {}
    return person if isinstance(person, dict) else {}


def _log_apollo_error(resp: httpx.Response) -> None:
    detail = ""
    try:
        payload = resp.json()
        if isinstance(payload, dict):
            detail = str(payload.get("error") or payload.get("message") or "")[:200]
    except Exception:
        detail = (resp.text or "")[:200]
    logger.warning(
        "Apollo people/match failed status=%s detail=%s",
        resp.status_code,
        detail or "(no body)",
    )


def _build_hint(person: dict[str, Any], company_fallback: str) -> str:
    title = (person.get("title") or "").strip()
    org_name = ((person.get("organization") or {}).get("name") or company_fallback).strip()
    if title and org_name:
        return f"Visitor context: {title} at {org_name}."
    if org_name:
        return f"Visitor company: {org_name}."
    return ""


def _map_person_to_profile(person: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    """Map Apollo person/org fields into lead profile role/industry."""
    merged = dict(profile)
    title = str(person.get("title") or "")
    org = person.get("organization") or {}
    org_name = str(org.get("name") or "")
    industry = str(org.get("industry") or org.get("primary_industry") or "")

    if title:
        merged["role"] = extract_role(title, str(merged.get("role") or ""))
    if industry:
        merged["industry"] = extract_industry(industry, str(merged.get("industry") or ""))
    elif org_name:
        merged["industry"] = extract_industry(org_name, str(merged.get("industry") or ""))

    if merged.get("role") in {"founder", "ceo", "cto", "coo"}:
        merged["decision_maker"] = True

    if org_name and not merged.get("company"):
        merged["company"] = org_name

    return enrich_profile_from_text(merged, f"{title} {industry} {org_name}")


async def fetch_apollo_person(lead_profile: dict[str, Any]) -> dict[str, Any]:
    prepared = _apollo_lookup_inputs(lead_profile)
    if not prepared:
        return {}
    api_key, body = prepared

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                _APOLLO_PEOPLE_MATCH_URL,
                headers=_apollo_headers(api_key),
                json=body,
            )
            if resp.status_code != 200:
                _log_apollo_error(resp)
                return {}
            return _parse_person_response(resp.json())
    except Exception as exc:
        logger.warning("Apollo person fetch failed: %s", exc)
        return {}


def fetch_apollo_person_sync(lead_profile: dict[str, Any]) -> dict[str, Any]:
    prepared = _apollo_lookup_inputs(lead_profile)
    if not prepared:
        return {}
    api_key, body = prepared

    try:
        with httpx.Client(timeout=_TIMEOUT) as client:
            resp = client.post(
                _APOLLO_PEOPLE_MATCH_URL,
                headers=_apollo_headers(api_key),
                json=body,
            )
            if resp.status_code != 200:
                _log_apollo_error(resp)
                return {}
            person = _parse_person_response(resp.json())
            if person:
                org = (person.get("organization") or {}).get("name") or body.get("organization_name", "")
                logger.info(
                    "Apollo match ok email=%s org=%s title=%s",
                    body.get("email", ""),
                    org,
                    person.get("title") or "",
                )
            return person
    except Exception as exc:
        logger.warning("Apollo person fetch failed: %s", exc)
        return {}


def enrich_lead_profile_from_apollo(lead_profile: dict[str, Any]) -> dict[str, Any]:
    """Sync Apollo lookup for CRM dispatch — runs inside background worker only."""
    if not get_settings().apollo_api_key:
        return lead_profile
    try:
        person = fetch_apollo_person_sync(lead_profile)
        if not person:
            return lead_profile
        return _map_person_to_profile(person, lead_profile)
    except Exception as exc:
        logger.warning("Apollo profile enrich skipped: %s", exc)
        return lead_profile


async def enrich_lead_async(lead_profile: dict[str, Any]) -> str:
    """Async Apollo enrichment. Safe to await inside async FastAPI handlers."""
    person = await fetch_apollo_person(lead_profile)
    if not person:
        return ""
    company = (lead_profile.get("company") or "").strip()
    return _build_hint(person, company)


def enrich_lead_hint_sync(lead_profile: dict[str, Any]) -> str:
    """Blocking Apollo hint for answer generation (safe from sync and async callers)."""
    person = fetch_apollo_person_sync(lead_profile)
    if not person:
        return ""
    company = (lead_profile.get("company") or "").strip()
    return _build_hint(person, company)


def _is_private_ip(ip: str) -> bool:
    """Return True for loopback/private/reserved IPs that won't resolve to a company."""
    import ipaddress
    try:
        addr = ipaddress.ip_address(ip)
        return addr.is_private or addr.is_loopback or addr.is_reserved or addr.is_unspecified
    except ValueError:
        return True


async def enrich_company_from_ip(ip: str) -> dict[str, Any]:
    """Async IP-to-company enrichment via Apollo /organizations/enrich.

    Returns a dict with keys: company, industry, employee_count, website.
    Returns {} on any failure, timeout, or private IP — safe to fire-and-forget.
    """
    settings = get_settings()
    api_key = (settings.apollo_api_key or "").strip()
    if not api_key or not ip or _is_private_ip(ip):
        return {}

    try:
        async with httpx.AsyncClient(timeout=_IP_ENRICH_TIMEOUT) as client:
            resp = await client.get(
                _APOLLO_ORG_ENRICH_URL,
                headers=_apollo_headers(api_key),
                params={"ip": ip},
            )
            if resp.status_code != 200:
                logger.debug("Apollo IP enrich status=%s ip=%s", resp.status_code, ip)
                return {}
            data = resp.json()
            org = data.get("organization") or {}
            if not org:
                return {}
            result: dict[str, Any] = {}
            if org.get("name"):
                result["company"] = str(org["name"])
            if org.get("industry"):
                result["industry"] = extract_industry(str(org["industry"]))
            if org.get("estimated_num_employees"):
                result["employee_count"] = int(org["estimated_num_employees"])
            if org.get("website_url"):
                result["website"] = str(org["website_url"])
            if result.get("company"):
                logger.info(
                    "Apollo IP enrich hit ip=%s company=%s industry=%s",
                    ip,
                    result.get("company"),
                    result.get("industry"),
                )
            return result
    except Exception as exc:
        logger.debug("Apollo IP enrich skipped: %s", exc)
        return {}


def enrich_lead(lead_profile: dict[str, Any]) -> str:
    """Sync wrapper around enrich_lead_async.

    When called from an already-running event loop (streaming path), schedules
    enrichment as a background task and returns immediately so the response is
    never blocked. When called from a sync context (LangGraph graph.invoke),
    runs the coroutine to completion.
    """
    if not _apollo_lookup_inputs(lead_profile):
        return ""

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(enrich_lead_async(lead_profile))

    loop.create_task(enrich_lead_async(lead_profile))
    return ""
