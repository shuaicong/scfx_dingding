"""main.py 同步编排开关测试"""
from unittest import mock

import main as main_module
from main import do_sync


def _mock_engine():
    eng = mock.MagicMock()
    eng.sync_today.return_value = {"total": 0, "new": 0, "skipped": 0, "failed": 0}
    eng.sync_price_indices.return_value = {"total": 0, "success": 0, "skipped": 0, "failed": 0}
    eng.sync_big_data.return_value = {"total": 0, "new": 0, "skipped": 0, "failed": 0}
    eng.sync_grainmarket.return_value = {"total": 0, "new": 0, "updated": 0, "failed": 0}
    eng.sync_huanan.return_value = {"total": 0, "new": 0, "updated": 0, "skipped": 0, "failed": 0}
    return eng


def test_do_sync_skips_huanan_when_disabled(monkeypatch):
    monkeypatch.setattr(main_module, "HUANAN_ENABLED", False)
    eng = _mock_engine()
    monkeypatch.setattr(main_module, "SyncEngine", lambda: eng)
    do_sync()
    eng.sync_huanan.assert_not_called()


def test_do_sync_calls_huanan_when_enabled(monkeypatch):
    monkeypatch.setattr(main_module, "HUANAN_ENABLED", True)
    eng = _mock_engine()
    monkeypatch.setattr(main_module, "SyncEngine", lambda: eng)
    do_sync()
    eng.sync_huanan.assert_called_once()
