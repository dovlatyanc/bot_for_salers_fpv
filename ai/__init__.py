"""Фасад AI-ассистента: ответы, feedback-кнопки, логирование диалогов.

HTTP-взаимодействие с бэкендом вынесено в services/ai_providers;
провайдер выбирается переменной AI_PROVIDER в .env.
"""
from .ai import answer_ai_with_feedback, ask_ai, handle_ai_request

__all__ = ["ask_ai", "answer_ai_with_feedback", "handle_ai_request"]
