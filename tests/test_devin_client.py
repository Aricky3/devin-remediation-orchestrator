import httpx
import pytest
import respx

from app.devin_client import DevinClient, DevinError

ORG = "org-123"
BASE = "https://api.devin.ai/v3"


@respx.mock
def test_create_session_posts_to_org_endpoint():
    route = respx.post(f"{BASE}/organizations/{ORG}/sessions").mock(
        return_value=httpx.Response(200, json={"session_id": "abc", "url": "u", "status": "new"})
    )
    client = DevinClient(api_key="cog_x", org_id=ORG)
    res = client.create_session("fix it", title="t", tags=["a"], max_acu_limit=10)
    assert res["session_id"] == "abc"
    assert route.called
    req = route.calls.last.request
    assert req.headers["Authorization"] == "Bearer cog_x"
    import json
    body = json.loads(req.content.decode())
    assert body["prompt"] == "fix it"
    assert body["max_acu_limit"] == 10
    assert body["title"] == "t"


@respx.mock
def test_get_session_hits_correct_url():
    respx.get(f"{BASE}/organizations/{ORG}/sessions/sid1").mock(
        return_value=httpx.Response(200, json={"session_id": "sid1", "status": "running"})
    )
    client = DevinClient(api_key="cog_x", org_id=ORG)
    res = client.get_session("sid1")
    assert res["status"] == "running"


@respx.mock
def test_error_raises_devinerror():
    respx.post(f"{BASE}/organizations/{ORG}/sessions").mock(
        return_value=httpx.Response(403, json={"detail": "Unauthorized"})
    )
    client = DevinClient(api_key="cog_x", org_id=ORG)
    with pytest.raises(DevinError):
        client.create_session("x")


def test_requires_credentials():
    with pytest.raises(DevinError):
        DevinClient(api_key="", org_id=ORG)
    with pytest.raises(DevinError):
        DevinClient(api_key="cog_x", org_id="")
