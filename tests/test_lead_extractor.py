from app.agent.lead_extractor import extract_from_message, get_missing_lead_fields


def test_extract_email():
    found = extract_from_message("Reach me at jane@acme.com for an AI project")
    assert found.get("email") == "jane@acme.com"


def test_missing_fields_sales():
    missing = get_missing_lead_fields({}, "sales")
    assert "project_need" in missing
    assert "email" in missing
