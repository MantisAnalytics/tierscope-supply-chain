from scgraph.ingest.prompts import PROMPTS, load_prompt, render_schema


def test_core_slice_lists_its_types_and_excludes_other_domains():
    text = render_schema(["core"])
    assert "domain core" in text
    assert "entity Supplier, key (id)" in text and "supplies_material:str" in text
    assert "relation SUPPLIES: Supplier -> Component, identity (site: Site id (optional))" in text
    assert "relation CONTAINS: Component -> Component, identity (assembly: Assembly id)" in text
    assert "domain tiers" not in text and "PARENT_SUPPLIER" not in text and "RiskIndicator" not in text
    assert "business_exposure_pct" in text and "margin" in text


def test_prompt_files_exist_and_embed_schema():
    for name in ("table_classify", "table_map", "table_repair", "field_parser", "field_extract"):
        assert (PROMPTS / f"{name}.md").is_file()
    assert "domain risk" in load_prompt("table_map", ["risk"]) and "{schema}" not in load_prompt("table_map")
