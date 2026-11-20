#!/usr/bin/env python3
"""Exporta el contrato OpenAPI de la API a docs/api/openapi.json (N07).

    python3 herramientas/exportar_openapi.py            # reescribe docs/api/openapi.json
    python3 herramientas/exportar_openapi.py --revisar  # sale con 1 si lo versionado no está al día

El contrato sale de la propia app (FastAPI): no se escribe a mano. La prueba tests/test_openapi.py
exige que lo versionado coincida con lo que la app publica y que cada operación esté documentada.
Requiere las dependencias de requirements.txt; usa SQLite en memoria y ningún proveedor externo.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
DESTINO = RAIZ / "docs" / "api" / "openapi.json"


def contrato() -> str:
    sys.path.insert(0, str(RAIZ / "src"))
    # Importar el módulo de la app la crea con la configuración del entorno: sin archivos ni red.
    for clave, valor in {"DATABASE_URL": "sqlite://", "OLLAMA_ENABLED": "false", "TTS_ENABLED": "false",
                         "GROQ_API_KEY": "", "MOCK_ENABLED": "true"}.items():
        os.environ[clave] = valor
    from callforge.presentation.api.app import app

    return json.dumps(app.openapi(), indent=2, ensure_ascii=False) + "\n"


def main() -> int:
    actual = contrato()
    if "--revisar" in sys.argv[1:]:
        al_dia = DESTINO.is_file() and DESTINO.read_text(encoding="utf-8") == actual
        estado = "al día" if al_dia else "DESACTUALIZADO (corre sin --revisar)"
        print(f"{DESTINO.relative_to(RAIZ)}: {estado}")
        return 0 if al_dia else 1
    DESTINO.parent.mkdir(parents=True, exist_ok=True)
    DESTINO.write_text(actual, encoding="utf-8")
    print(f"{DESTINO.relative_to(RAIZ)}: {len(json.loads(actual)['paths'])} rutas exportadas")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
