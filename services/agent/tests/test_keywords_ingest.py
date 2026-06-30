async def test_add_paper_merges_keywords_on_dedup(session):
    from app.repositories.library import add_paper

    p1 = await add_paper(
        session,
        {"title": "Keyword Merge", "doi": "10.1/kw", "keywords": "AI; civil engineering"},
    )
    p2 = await add_paper(
        session,
        {"title": "Keyword Merge", "doi": "10.1/kw", "keywords": "civil engineering; smart structures"},
    )

    assert p2.id == p1.id
    assert p2.keywords == "AI; civil engineering; smart structures"
