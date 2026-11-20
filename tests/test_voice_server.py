"""Regla V7: el micro-servicio de voz (voice_server.py) sólo usa voces que existen en voices/.

Corre sin GPU ni torch: el convertidor es un doble y la ruta de rechazo responde antes de importar torch.
"""

from __future__ import annotations

import sys
import types

import pytest
from fastapi import HTTPException

import voice_server as vs


class _ConvertidorDoble:
    def __init__(self) -> None:
        self.extraidos: list[list[str]] = []

    def extract_se(self, rutas: list[str]):
        self.extraidos.append(rutas)
        return "embedding"


@pytest.fixture
def voces(tmp_path, monkeypatch):
    carpeta = tmp_path / "voices"
    carpeta.mkdir()
    (carpeta / "ana.mp3").write_bytes(b"x")
    (carpeta / "ana.wav").write_bytes(b"x")
    (carpeta / "luis.flac").write_bytes(b"x")
    (tmp_path / "fuera.wav").write_bytes(b"x")  # existe, pero fuera de voices/
    monkeypatch.setattr(vs, "VOICES_DIR", carpeta)
    monkeypatch.setattr(vs, "_target_se_cache", {})
    return carpeta


def test_la_voz_se_resuelve_solo_contra_archivos_de_voices(voces):
    assert vs._referencia("ana") == voces / "ana.wav"  # .wav tiene precedencia sobre .mp3
    assert vs._referencia("luis") == voces / "luis.flac"
    assert vs._resolve_voice("luis") == str(voces / "luis.flac")
    for nombre in ("", None, "nadie", "../fuera", "fuera", "x/ana", "ana.wav", ".", ".."):
        assert vs._referencia(nombre) is None, nombre


def test_un_nombre_con_ruta_no_lee_ni_escribe_fuera_de_voices(voces, monkeypatch):
    # Si algo intentara usar torch en la ruta de rechazo, este módulo falso lo delataría.
    falso = types.ModuleType("torch")
    falso.load = falso.save = lambda *a, **k: pytest.fail("torch no debe tocarse al rechazar")
    monkeypatch.setitem(sys.modules, "torch", falso)
    antes = sorted(p.name for p in voces.parent.rglob("*"))
    for nombre in ("../fuera", "../../tmp/x", "/etc/passwd", "ana/../../fuera"):
        with pytest.raises(HTTPException) as err:
            vs.get_target_se(_ConvertidorDoble(), nombre)
        assert err.value.status_code == 404
    assert sorted(p.name for p in voces.parent.rglob("*")) == antes  # nada nuevo en disco
    assert vs._target_se_cache == {}


def test_el_cache_usa_el_nombre_del_disco(voces, monkeypatch):
    guardados: list[str] = []
    falso = types.ModuleType("torch")
    falso.load = lambda *a, **k: pytest.fail("no hay caché en disco todavía")
    falso.save = lambda obj, ruta: guardados.append(ruta)
    monkeypatch.setitem(sys.modules, "torch", falso)
    conv = _ConvertidorDoble()
    assert vs.get_target_se(conv, "ana") == "embedding"
    assert conv.extraidos == [[str(voces / "ana.wav")]]
    assert guardados == [str(voces / ".se_cache" / "ana.pt")]
    assert list(vs._target_se_cache) == ["ana"]
    vs.get_target_se(conv, "ana")  # segunda vez: de la memoria, sin extraer de nuevo
    assert len(conv.extraidos) == 1
