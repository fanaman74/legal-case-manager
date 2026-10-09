import time

from app import settings, toolcheck, worker


def test_heartbeat_round_trip(tmp_path):
    cfg = settings.Settings(tmp_path, "local", "1.2.3")
    assert worker.healthcheck(cfg) == 1
    worker.beat(cfg)
    assert worker.healthcheck(cfg) == 0
    assert cfg.heartbeat_file.read_text().split()[1] == "1.2.3"
    cfg.heartbeat_file.write_text(f"{int(time.time()) - 600} 1.2.3\n")
    assert worker.healthcheck(cfg) == 1


def test_toolcheck_pst_loads(capsys):
    assert toolcheck.main(["pst"]) == 0
    assert capsys.readouterr().out.strip()


def test_toolcheck_rejects_unknown_probe():
    assert toolcheck.main(["rm"]) == 2
    assert toolcheck.main([]) == 2
