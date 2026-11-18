"""Unit tests for app.utils.prompt_loader.load_prompt."""
import pytest

from app.utils.config import prompt_config
from app.utils.path_tool import get_abstract_path
from app.utils.prompt_loader import load_prompt


def test_config_has_known_prompt_keys():
    """prompt.yaml 只保留在线功能实际用到的模板（已下线功能的模板随之移除）。"""
    assert isinstance(prompt_config, dict)
    for key in (
        "main_prompt",
        "rag_context_prompt",
        "agentic_rag_planner_prompt",
        "agentic_rag_answerability_prompt",
        "entity_extraction_prompt",
        "query_entity_extraction_prompt",
    ):
        assert key in prompt_config


def test_known_keys_load_nonempty_text():
    assert prompt_config, "prompt.yaml must define at least one prompt key"
    for key in prompt_config:
        content = load_prompt(key)
        assert isinstance(content, str)
        assert content.strip(), f"prompt '{key}' loaded empty content"


def test_unknown_key_raises_keyerror():
    with pytest.raises(KeyError):
        load_prompt("this_key_does_not_exist")


def test_default_uses_main_prompt():
    assert load_prompt() == load_prompt("main_prompt")


def test_loaded_content_matches_file_on_disk():
    for key, rel_path in prompt_config.items():
        disk_content = open(get_abstract_path(rel_path), encoding="utf-8").read()
        assert load_prompt(key) == disk_content
