import httpx
import pydantic
import pytest

from loveimage.settings import Settings

pytestmark = pytest.mark.anyio


def test_settings_read_neither_the_project_env_nor_the_shell():
    with pytest.raises(pydantic.ValidationError):
        Settings()


def test_providers_are_fake_in_tests(settings):
    assert (settings.kie_ai_api_key, settings.commandcode_api_key) == ("test-kie", "test-llm")
    assert settings.kie_base_url == "https://kie.test"
    assert settings.kie_upload_url == "https://upload.test"
    assert settings.llm_base_url == "https://llm.test/v1"
    assert settings.database_url.endswith("_test")


@pytest.mark.parametrize("url", ["https://api.kie.ai", "https://api.commandcode.ai/provider/v1"])
async def test_a_real_request_cannot_leave(url):
    async with httpx.AsyncClient() as http:
        with pytest.raises(AssertionError, match="a test tried to reach"):
            await http.get(url)
