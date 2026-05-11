def test_pdf_summary_normalizes_bare_openrouter_model():
    from consortium import pdf_summary

    assert pdf_summary._normalize_model_for_litellm("deepseek-chat") == "openrouter/deepseek/deepseek-chat"
    assert (
        pdf_summary._normalize_model_for_litellm("openrouter/deepseek/deepseek-chat")
        == "openrouter/deepseek/deepseek-chat"
    )
