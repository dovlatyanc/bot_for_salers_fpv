"""Интеграция с AI-прокси: запросы, ответы, логирование (вынесено из bot.py)."""
from .ai import answer_ai_with_feedback, ask_ai, handle_ai_request

__all__ = ["ask_ai", "answer_ai_with_feedback", "handle_ai_request"]
