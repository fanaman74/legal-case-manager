import time

from app import worker


def test_healthcheck_reads_heartbeat(tmp_path, monkeypatch):
    hb = tmp_path / "hb"
    monkeypatch.setattr(worker, "HEARTBEAT", hb)
    assert worker.healthcheck() == 1
    hb.write_text(str(int(time.time())))
    assert worker.healthcheck() == 0
    hb.write_text(str(int(time.time()) - 600))
    assert worker.healthcheck() == 1
