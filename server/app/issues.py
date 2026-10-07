"""Catálogo de problemas (§13)."""

CATALOG: dict[str, tuple[str, str]] = {
    "AUDIO_TTS_FAILED": ("error", "TTS falhou após retentativas"),
    "TRANSCRIPT_DIVERGENCE": ("warning", "Áudio diverge do roteiro"),
    "TRANSCRIPT_FALLBACK": ("warning", "Whisper falhou; tempos vieram do SRT da Darkvi"),
    "SCENE_LOW_SCORE": ("warning", "Cena usou candidato abaixo do limiar"),
    "SCENE_MIGRATED": ("info", "Cena trocou de fonte"),
    "SCENE_NO_CANDIDATES": ("warning", "Nenhum candidato passou no filtro técnico numa fonte"),
    "YOUTUBE_QUOTA_FALLBACK": ("info", "Cena do YouTube foi para os bancos por falta de cota"),
    "VISION_FAILED": ("warning", "IA de visão falhou; escolha feita pelo ranking de texto"),
    "STYLE_REJECTED": ("info", "Candidatos descartados por estilo incompatível com a cena ou por franquia"),
    "VISION_QUOTA": ("warning", "Cota do Gemini esgotada; seleção pelo ranking de texto"),
    "VISION_FALLBACK": ("info", "Gemini sem cota; avaliações de imagem feitas pelo Claude"),
    "AI_IMAGE_REJECTED": ("warning", "Imagem gerada reprovou 2x na validação"),
    "QUERY_REWRITTEN": ("info", "Queries reescritas após reprovação"),
    "SCENE_REVIEW_REQUIRED": ("warning", "Cena precisa de revisão (identidade exata ou evidência sem confirmação)"),
    "INTERPRETATION_REVIEW": ("warning", "Interpretação do roteiro a revisar (ambiguidade, referência ou divisão)"),
    "SCRIPT_CONTRADICTION": ("warning", "O roteiro afirma e nega a mesma coisa"),
    "SCENE_GENERIC_FALLBACK": ("warning", "Usada uma foto genérica do assunto por falta de opção"),
    "LANGUAGE_MISMATCH": ("warning", "Idioma escolhido para o vídeo diverge do idioma detectado no roteiro"),
    "OVERLAY_LANGUAGE_FIXED": ("info", "Texto de overlay estava em outro idioma e foi corrigido"),
    "COMPOSITION_DEVIATION": ("warning", "Composição final desviou mais de 5% da meta"),
    "PROVIDER_QUOTA": ("warning", "Cota ou rate limit de provedor"),
    "BUDGET_EXCEEDED": ("warning", "Teto de gasto da produção impediu uma chamada paga (a produção continua)"),
    "PROVIDER_AUTH": ("warning", "Provedor recusou a credencial (não é falta de cota)"),
    "AI_GEN_FAILED": ("warning", "Geração de IA falhou na cena"),
    "IMAGE_QUOTA_EXHAUSTED": ("warning", "Saldo diário da Darkvi acabou"),
    "REFERENCE_NOT_ALLOWED": ("warning", "Plano Darkvi sem imagem de referência"),
    "MUSIC_FAILED": ("warning", "Sem música compatível; vídeo segue sem música"),
    "QUOTE_DROPPED": ("info", "Citação do planejamento não estava na narração e foi descartada"),
    "SFX_FALLBACK": ("info", "Freesound indisponível; efeitos sonoros sintetizados"),
    "AMF_FALLBACK": ("info", "Encode AMF falhou; render usou libx264"),
    "RENDER_FAILED": ("error", "Falha no FFmpeg"),
    "DRIVE_UPLOAD_FAILED": ("error", "Upload para o Drive falhou; arquivo mantido localmente"),
    "DRIVE_NOT_CONFIGURED": ("warning", "Conta Google não conectada; vídeo ficou só local"),
    "STEP_FAILED": ("error", "Etapa falhou"),
}


def severity_of(code: str) -> str:
    return CATALOG.get(code, ("warning", ""))[0]
