"""Estados de validação de cada cena (Fase A5).

    validated        uma IA de visão viu o asset (ou o trecho usado) e ele atingiu o limiar
    unvalidated      ninguém olhou: escolha pelo texto dos títulos, foto genérica, imagem sem validação
    review_required  há asset, mas falta confirmação que a cena exige (identidade exata, evidência, nota baixa)
    rejected         nenhuma opção serviu (a cena estende a vizinha ou vai para IA)

Nota de texto nunca vira aprovação visual, e as escalas não se misturam: `score_basis` diz de onde a nota veio.
Política (settings.selection.validation_policy):
    strict_for_exact_identity  cenas de identidade exata ou evidência sem confirmação → review_required
    lenient                    o material ilustrativo segue com aviso, sem bloquear
Miniaturas de API confirmam o candidato, não o trecho: em vídeo, identidade exata pede o trecho conferido.
"""
from __future__ import annotations

VALIDATED, UNVALIDATED, REVIEW_REQUIRED, REJECTED = "validated", "unvalidated", "review_required", "rejected"
STATUSES = (VALIDATED, UNVALIDATED, REVIEW_REQUIRED, REJECTED)
# identidades que miniatura nem IA genérica confirmam sozinhas (pessoa real, evento, espécie botânica exata...)
EXACT_IDENTITIES = {"exact_person", "exact_event", "exact_place", "exact_object", "species"}
EVIDENCE_ROLES = {"exact_evidence"}
VISION_METHODS = ("vision", "vision_tiebreak", "ai_image")


def policy(settings: dict | None) -> str:
    p = ((settings or {}).get("selection") or {}).get("validation_policy", "strict_for_exact_identity")
    return p if p in ("strict_for_exact_identity", "lenient") else "strict_for_exact_identity"


def needs_exact(scene: dict) -> bool:
    return scene.get("required_identity") in EXACT_IDENTITIES or scene.get("visual_role") in EVIDENCE_ROLES


def classify(entry: dict, scene: dict, min_score: float, mode: str = "strict_for_exact_identity") -> dict:
    """{status, score_basis, reasons}. Lê entradas antigas (sem campos novos) sem falhar."""
    source = entry.get("source") or ""
    if source in ("missing", "migrate_ai") or not entry.get("asset"):
        return {"status": REJECTED, "score_basis": "none", "reasons": [entry.get("reason") or "sem asset"]}
    method = entry.get("method") or ""
    score = entry.get("score")
    reasons: list[str] = []
    visual = method.startswith("vision") or (method == "ai_image" and score is not None)
    basis = "vision" if visual else ("text" if method in ("text", "text_fallback") else (method or "none"))
    if visual and isinstance(score, (int, float)) and score >= min_score:
        status = VALIDATED
    elif visual:
        status, _ = REVIEW_REQUIRED, reasons.append(f"nota visual {score} abaixo do limiar {min_score}")
    else:
        status = UNVALIDATED
        reasons.append("sem avaliação visual (escolha pelo texto)" if basis == "text" else "sem avaliação visual")
    exact = needs_exact(scene)
    if exact:
        if source == "ai_image" or str(entry.get("source_used", "")).startswith("ai"):
            # imagem gerada nunca é registro real de evento, pessoa ou espécie
            status, _ = REVIEW_REQUIRED, reasons.append("imagem gerada numa cena que pede registro real")
        elif not entry.get("is_image") and status == VALIDATED:
            check = (entry.get("segment_check") or {}).get("status")
            if check != "confirmed":
                status = REVIEW_REQUIRED
                reasons.append("miniatura aprovada, mas o trecho usado não foi conferido")
        if scene.get("required_identity") == "species" and status == VALIDATED:
            reasons.append("identificação de espécie por imagem não é definitiva: confira")
            status = REVIEW_REQUIRED
        if status in (UNVALIDATED,) and mode == "strict_for_exact_identity":
            status = REVIEW_REQUIRED
            reasons.append("cena de identidade exata sem confirmação visual")
    if mode == "lenient" and status == REVIEW_REQUIRED and not exact:
        status = UNVALIDATED
    return {"status": status, "score_basis": basis, "reasons": reasons}


def comparable_score(option_method: str, score: float) -> tuple[int, float]:
    """Ordem entre opções de cenas diferentes: nota visual antes de nota de texto (escalas distintas)."""
    return (1 if option_method.startswith("vision") else 0, float(score or 0))
