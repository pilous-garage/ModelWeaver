import tempfile
import unittest
from pathlib import Path

import modelweaver


class LiteLLMBridgeTests(unittest.TestCase):
    def test_generate_proxy_and_wrapper_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config_path = root / "litellm.yaml"
            wrapper_path = root / "opencode-wrapper.sh"

            modelweaver.write_litellm_proxy_config(
                {
                    "model_list": [
                        {
                            "model_name": "demo-model",
                            "litellm_params": {
                                "model": "openai/demo-model",
                                "api_key": "os.environ/OPENAI_API_KEY",
                            },
                        }
                    ]
                },
                config_path,
            )
            modelweaver.write_opencode_wrapper(
                wrapper_path,
                "http://127.0.0.1:8000/v1",
                "demo-model",
            )

            self.assertTrue(config_path.exists())
            self.assertIn("model_list:", config_path.read_text())
            self.assertIn("demo-model", config_path.read_text())
            self.assertTrue(wrapper_path.exists())
            self.assertIn("OPENAI_API_BASE", wrapper_path.read_text())
            self.assertIn("demo-model", wrapper_path.read_text())

    def test_build_route_plan_writes_trace(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            trace_path = Path(tmpdir) / "route.log"
            plan = modelweaver.build_route_plan(
                [
                    {"id": "groq", "label": "Groq", "models": ["llama3"]},
                    {"id": "openrouter", "label": "OpenRouter", "models": ["gpt-4o"]},
                ],
                trace_path=trace_path,
            )
            self.assertTrue(plan)
            self.assertEqual(plan[0]["provider"], "groq")
            self.assertEqual(plan[0]["fallback"], "openrouter")
            self.assertTrue(trace_path.exists())

    def test_wrapper_uses_existing_api_key_and_triggers_on_auth_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            wrapper_path = Path(tmpdir) / "opencode-wrapper.sh"
            modelweaver.write_opencode_wrapper(
                wrapper_path,
                "http://127.0.0.1:8000/v1",
                "demo-model",
                ["fallback-model"],
            )
            wrapper_text = wrapper_path.read_text()
            self.assertIn('OPENAI_API_KEY', wrapper_text)
            self.assertIn('OPENROUTER_API_KEY', wrapper_text)
            self.assertIn('incorrect api key|invalid api key|401|403', wrapper_text)

    def test_select_default_model_prefers_groq_then_openrouter(self) -> None:
        configured = [
            {"id": "groq", "label": "Groq", "models": ["llama-3.3-70b-versatile"]},
            {"id": "openrouter", "label": "OpenRouter", "models": ["openai/gpt-4o"]},
        ]
        self.assertEqual(modelweaver.select_default_model(configured), "llama-3.3-70b-versatile")


if __name__ == "__main__":
    unittest.main()
