from click.testing import CliRunner

from minimum.cli import main


def test_cli_offline_smoke(tmp_path):
    r = CliRunner()
    db = str(tmp_path / "l.db")
    base = ["--db", db, "--offline", "--learner", "cli"]
    out = r.invoke(main, base + ["validate"])
    assert out.exit_code == 0 and "content OK" in out.output
    out = r.invoke(main, base + ["enroll"])
    assert out.exit_code == 0 and "Enrolled" in out.output
    out = r.invoke(main, base + ["curriculum"])
    assert "p1.analysis" in out.output and "gate1" in out.output
    out = r.invoke(main, base + ["start", "p1.ode"])
    assert "retrieval cards created" in out.output
    out = r.invoke(main, base + ["solution", "p1.ode.001"])
    assert out.exit_code == 2 and "LOCKED" in out.output
    f = tmp_path / "a.txt"
    f.write_text("ANSWER: exp(-t) - exp(-2*t)")
    out = r.invoke(main, base + ["submit", "p1.ode.001", str(f)])
    assert out.exit_code == 0 and "Score 4.0/8" in out.output and "UNVERIFIED" in out.output
    out = r.invoke(main, base + ["solution", "p1.ode.001"])
    assert out.exit_code == 0 and "ANSWER" in out.output
    out = r.invoke(main, base + ["status"])
    assert '"submissions": 1' in out.output
    out = r.invoke(main, base + ["exam", "start", "gate1.written", "--out", str(tmp_path / "w.md")])
    assert out.exit_code == 0 and "8 problems" in out.output
    out = r.invoke(main, base + ["kit"])
    assert "Budget" in out.output
    out = r.invoke(main, base + ["plan"])
    assert "retrieval" in out.output
