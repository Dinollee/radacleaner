"""Тесты для radacleaner."""
import pytest
import json
from unittest.mock import patch, MagicMock


class TestConfig:
    """Тесты конфігурації."""

    def test_groq_model_is_gpt_oss(self):
        """Модель має бути gpt-oss-120b."""
        from src.config import GROQ_MODEL
        assert GROQ_MODEL == "openai/gpt-oss-120b"

    def test_risk_prompt_has_required_fields(self):
        """Промпт має містити всі обов'язкові поля."""
        from src.config import RISK_ANALYSIS_PROMPT
        assert "risks" in RISK_ANALYSIS_PROMPT
        assert "category" in RISK_ANALYSIS_PROMPT
        assert "severity" in RISK_ANALYSIS_PROMPT
        assert "quote" in RISK_ANALYSIS_PROMPT
        assert "explanation" in RISK_ANALYSIS_PROMPT
        assert "summary" in RISK_ANALYSIS_PROMPT

    def test_risk_prompt_has_categories(self):
        """Промпт має містити всі категорії ризиків."""
        from src.config import RISK_ANALYSIS_PROMPT
        categories = ["Corruption", "Budgetary", "Legal Collision", "Ambiguity",
                      "Civil Rights", "Power Concentration", "Other"]
        for cat in categories:
            assert cat in RISK_ANALYSIS_PROMPT, f"Missing category: {cat}"


class TestRiskParsing:
    """Тесты парсингу результатів LLM."""

    def test_parse_valid_risks(self):
        """Парсинг валідного JSON з ризиками."""
        from src.rag_monitor import parse_llm_response
        raw = json.dumps({
            "summary": "Тестовий закон",
            "risks": [
                {
                    "category": "Corruption",
                    "severity": "High",
                    "quote": "Тестова цитата",
                    "explanation": "Тестове пояснення"
                }
            ]
        })
        result = parse_llm_response(raw)
        assert result is not None
        assert result["summary"] == "Тестовий закон"
        assert len(result["risks"]) == 1
        assert result["risks"][0]["category"] == "Corruption"

    def test_parse_empty_risks(self):
        """Парсинг JSON без ризиків."""
        from src.rag_monitor import parse_llm_response
        raw = json.dumps({
            "summary": "Без ризиків",
            "risks": []
        })
        result = parse_llm_response(raw)
        assert result is not None
        assert len(result["risks"]) == 0

    def test_parse_invalid_json(self):
        """Парсинг невалідного JSON."""
        from src.rag_monitor import parse_llm_response
        result = parse_llm_response("not json")
        assert result is None


class TestThreatLevel:
    """Тести рівня загрози."""

    def test_threat_level_from_severity(self):
        """Рівень загрози з severity."""
        from src.rag_monitor import calculate_threat_level
        assert calculate_threat_level([{"severity": "High"}]) == "critical"
        assert calculate_threat_level([{"severity": "Medium"}]) == "medium"
        assert calculate_threat_level([{"severity": "Low"}]) == "low"
        assert calculate_threat_level([]) == "none"
