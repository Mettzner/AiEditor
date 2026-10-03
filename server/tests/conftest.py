"""Testes isolados: banco e dados em pasta temporária, sem rede e sem chaves reais."""
import os
import tempfile

os.environ["AIEDITOR_DATA"] = tempfile.mkdtemp(prefix="aieditor_tests_")

from app.db import init_db  # noqa: E402  (depois de apontar AIEDITOR_DATA para a pasta temporária)

init_db()
