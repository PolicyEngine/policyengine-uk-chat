"""Unit coverage for authoritative analysis-catalogue resolution."""

from analysis.catalogue import resolve_catalogue_term


def test_variable_search_passes_limit_without_setting_an_entity_filter():
    received: list[tuple[str, str | None, int]] = []

    def search(query: str, entity: str | None, limit: int) -> dict:
        received.append((query, entity, limit))
        return {
            "variables": [
                {
                    "name": "universal_credit",
                    "label": "Universal Credit",
                    "description": "Universal Credit entitlement",
                }
            ]
        }

    resolution = resolve_catalogue_term(
        "variable",
        "Universal Credit",
        variable_search=search,
        limit=7,
    )

    assert received == [("Universal Credit", None, 7)]
    assert resolution.unique_best_authoritative is not None
    assert resolution.unique_best_authoritative.identifier == "universal_credit"
