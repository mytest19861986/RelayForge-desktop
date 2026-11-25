from pathlib import Path
from fastapi.testclient import TestClient
from main import create_app, dashboard_url, PORT

def test_quick_launch_preserves_auth(tmp_path):
    app=create_app(tmp_path,start_workers=False)
    key=app.state.service.admin_key
    assert PORT==8765
    assert dashboard_url(PORT,key)==f"http://127.0.0.1:8765/#rf-key={key}"
    assert dashboard_url(PORT,"a&b").endswith("a%26b")
    with TestClient(app,base_url="http://127.0.0.1:8765",client=("127.0.0.1",50000)) as c:
        html=c.get("/").text
        assert "ورود به مرکز فرمان" not in html and 'id="login-button"' not in html
        assert 'id="admin-key"' not in html
        assert key not in html
        js=c.get("/assets/main.js").text
        assert "history.replaceState" in js and "startupKey()" in js
        assert c.get("/api/status").status_code==401
        assert c.get("/api/status",headers={"X-Studio-Key":key}).status_code==200
        assert c.post("/api/engine/start",headers={"X-Studio-Key":key,"Origin":"https://evil.example"}).status_code==403
        assert not app.state.service.engine.active

def test_launch_scripts_no_questions():
    root=Path(__file__).resolve().parents[1]
    for name in ["START_PROJECT.bat","START_PROJECT_SOURCE_WINDOWS.bat"]:
        text=(root/name).read_text()
        assert "Read-Host" not in text and "--port" not in text and "--data-dir" not in text
    assert "#rf-key=" in (root/"OPEN_DASHBOARD.bat").read_text()
