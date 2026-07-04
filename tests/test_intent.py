from app.agent.intent_classifier import classify_user_intent, classify_with_history


def test_sales_intent():
    r = classify_user_intent("We need a quote for an AI chatbot project")
    assert r.intent == "sales"


def test_booking_intent():
    r = classify_user_intent("Can I book a discovery call with your team?")
    assert r.intent == "booking"


def test_call_alone_not_booking():
    r = classify_user_intent("How do you typically run discovery calls with clients?")
    assert r.intent == "help"


def test_help_intent():
    r = classify_user_intent("What services does MobCoder offer?")
    assert r.intent == "help"


def test_project_sales_before_help():
    r = classify_user_intent(
        "We need to build an AI agent for customer support. Can you help?"
    )
    assert r.intent == "sales"


def test_ai_services_help_not_sales():
    r = classify_user_intent("What AI and agentic AI services does MobCoder offer?")
    assert r.intent == "help"


def test_rag_solution_sales_not_out_of_scope():
    r = classify_user_intent("I am lookin a soultion based out of RAG")
    assert r.intent == "sales"
    assert r.intent != "out_of_scope"


def test_rag_capabilities_help():
    r = classify_user_intent("Do you build RAG pipelines for enterprise knowledge bases?")
    assert r.intent in ("help", "sales")
    assert r.intent != "out_of_scope"


def test_pricing_is_sales():
    r = classify_user_intent("How does MobCoder approach pricing?")
    assert r.intent == "sales"
    assert r.page_category == "services"


def test_engagement_model_help():
    r = classify_user_intent(
        "What engagement models does MobCoder offer for long-term partnerships?"
    )
    assert r.intent == "help"


def test_contact_sales():
    r = classify_user_intent("How can I contact MobCoder sales?")
    assert r.intent == "sales"


def test_offshore_comparison_sales():
    r = classify_user_intent(
        "We're comparing vendors for an AI agent — why should we pick MobCoder?"
    )
    assert r.intent == "sales"


def test_multi_turn_help_stays_help():
    history = [
        {"role": "user", "content": "What services does MobCoder offer?"},
        {"role": "assistant", "content": "MobCoder offers AI and software services."},
    ]
    r = classify_with_history(
        "Do you work with enterprises on agentic AI?",
        history,
        prior_intent="help",
    )
    assert r.intent == "help"


def test_stage_compute():
    from app.agent.conversation_stage import compute_conversation_stage

    stage = compute_conversation_stage(
        "sales",
        [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}],
        {},
    )
    assert stage in ("discover", "educate", "qualify", "convert")
