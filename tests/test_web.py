from fastapi.testclient import TestClient

from minimum.web.app import create_app


def test_web_flow_offline(tmp_path):
    app = create_app(db=tmp_path / "w.db", learner="web", offline=True, token="")
    c = TestClient(app)
    hz = c.get("/healthz").json()
    assert hz["ok"] and hz["build"]
    sm = c.get("/smoke").json()
    assert sm["ok"] is False and "offline" in sm["error"]
    r = c.get("/")
    assert r.status_code == 200 and "Enroll" in r.text
    assert c.post("/enroll", follow_redirects=False).status_code == 303
    r = c.get("/")
    assert "p1 · The mathematical" in r.text and "Gate 1" in r.text
    assert "p1.analysis" in c.get("/curriculum").text
    assert c.post("/module/p1.ode/start", follow_redirects=False).status_code == 303
    r = c.get("/module/p1.ode")
    assert "Started" in r.text and "p1.ode.001" in r.text
    r = c.get("/problem/p1.ode.001")
    assert "unlocks after your first submission" in r.text
    c.post("/problem/p1.ode.001/submit", data={"content": "ANSWER: exp(-t)-exp(-2*t)"})
    r = c.get("/problem/p1.ode.001")
    assert "4.0/8" in r.text and "Reference solution" in r.text
    c.post("/tutor/p1.ode", data={"message": "help", "problem": "p1.ode.001"})
    assert "offline faculty" in c.get("/tutor/p1.ode?problem=p1.ode.001").text
    r = c.post("/exam/gate1.written/start", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/attempt/")
    att_url = r.headers["location"]
    page = c.get(att_url).text
    assert "Submit the whole exam" in page
    r = c.post(att_url + "/submit", data={})
    assert c.get(att_url).text.count("FAIL") >= 1
    r = c.get("/review")
    assert "Retrieval" in r.text
    assert "Today" in c.get("/plan").text
    assert "Lab kit" in c.get("/kit").text
    r = c.get("/oral/gate1.oral")
    assert "Question 1" in r.text
    c.post("/oral/gate1.oral", data={"answer": "a"})
    r = c.post("/oral/gate1.oral", data={"answer": "b"})
    assert "FAIL" in r.text


def test_web_token_auth(tmp_path):
    app = create_app(db=tmp_path / "t.db", learner="web", offline=True, token="s3cret")
    c = TestClient(app)
    assert c.get("/").status_code == 401
    assert c.get("/healthz").status_code == 200
    r = c.get("/?token=wrong")
    assert r.status_code == 401
    r = c.get("/?token=s3cret", follow_redirects=False)
    assert r.status_code == 303 and "minimum_token" in r.headers.get("set-cookie", "")
    assert c.get("/").status_code == 200
