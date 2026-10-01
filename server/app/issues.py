"""Catálogo de problemas (§13)."""

CATALOG: dict[str, tuple[str, str]] = {
    "AUDIO_TTS_FAILED": ("error", "TTS falhou após retentativas"),
    "TRANSCRIPT_DIVERGENCE": ("warning", "Áudio diverge do roteiro"),
    "TRANSCRIPT_FALLBACK": ("warning", "Whisper falhou; tempos vieram do SRT da Darkvi"),
    "SCENE_LOW_SCORE": ("warning", "Cena usou candidato abaixo do limiar"),
    "SCENE_MIGRATED": ("info", "Cena trocou de fonte"),
    "COMPOSITION_DEVIATION": ("warning", "Composição final desviou mais de 5% da meta"),
    "PROVIDER_QUOTA": ("warning", "Cota ou rate limit de provedor"),
    "AI_GEN_FAILED": ("warning", "Geração de IA falhou na cena"),
    "IMAGE_QUOTA_EXHAUSTED": ("warning", "Saldo diário da Darkvi acabou"),
    "REFERENCE_NOT_ALLOWED": ("warning", "Plano Darkvi sem imagem de referência"),
    "MUSIC_FAILED": ("warning", "Sem música compatível; vídeo segue sem música"),
    "AMF_FALLBACK": ("info", "Encode AMF falhou; render usou libx264"),
    "RENDER_FAILED": ("error", "Falha no FFmpeg"),
    "DRIVE_UPLOAD_FAILED": ("error", "Upload para o Drive falhou; arquivo mantido localmente"),
    "DRIVE_NOT_CONFIGURED": ("warning", "Conta Google não conectada; vídeo ficou só local"),
    "STEP_FAILED": ("error", "Etapa falhou"),
}


def severity_of(code: str) -> str:
    return CATALOG.get(code, ("warning", ""))[0]
