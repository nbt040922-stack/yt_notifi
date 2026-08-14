from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app


def test_members_are_read_only(settings):
    client = TestClient(create_app(settings))
    response = client.get("/api/team-members")
    assert response.status_code == 200
    assert len(response.json()) == 4
    assert client.patch(
        "/api/team-members/member_1",
        json={"display_name": "Changed", "nas_folder": "Changed"},
    ).status_code == 404


def test_dashboard_has_no_member_edit_controls():
    html = Path("app/dashboard.html").read_text(encoding="utf-8")
    assert 'id="member-dialog"' not in html
    assert "edit-member" not in html
    assert "editingMemberId" not in html
