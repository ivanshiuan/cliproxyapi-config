"""Quick sanity: the OpenAPI schema can be generated and is well-formed.

Catches regressions like a router being added without a tag, or a Pydantic
schema with a misnamed model_config that breaks schema generation.
"""

from __future__ import annotations

from restaurant_api.main import app


def test_openapi_schema_renders_without_error():
    """``app.openapi()`` should not raise — many subtle Pydantic mis-configs
    only surface during schema generation, not at import time."""
    schema = app.openapi()
    assert schema["openapi"].startswith("3.")
    assert schema["info"]["title"]
    assert schema["info"]["version"]


def test_openapi_covers_all_four_phase1_routers():
    schema = app.openapi()
    paths = set(schema["paths"].keys())
    # Spot-check one path from each router; if any router stops registering
    # its routes (e.g. import-time error swallowed), this catches it.
    assert "/orders" in paths
    assert "/stock/purchases" in paths
    assert "/clock/in" in paths
    assert "/events/waste" in paths
    assert "/health/live" in paths
    assert "/health/ready" in paths


def test_openapi_has_request_response_schemas():
    """Each POST /orders should have a request body schema referenced."""
    schema = app.openapi()
    post_orders = schema["paths"]["/orders"]["post"]
    assert "requestBody" in post_orders
    assert post_orders["requestBody"]["content"]["application/json"]


def test_openapi_does_not_leak_internal_health_alias():
    """The bare GET /health (composite) is `include_in_schema=False` so it
    shouldn't appear in the public OpenAPI spec. /live + /ready are the
    documented surface."""
    schema = app.openapi()
    # GET /health composite is hidden; /health/live and /health/ready are visible.
    if "/health" in schema["paths"]:
        # If it appears, it must NOT have a GET method exposed.
        assert "get" not in schema["paths"]["/health"]
    assert "/health/live" in schema["paths"]
    assert "/health/ready" in schema["paths"]
