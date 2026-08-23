from guardrails import Guard
from guardrails_ai.detect_pii import DetectPII
from guardrails_ai.prompt_injection_detector import PromptInjectionDetector
from guardrails_ai.toxic_language import ToxicLanguage

from src.services.llm import openAI


class ProxiedPromptInjectionDetector(PromptInjectionDetector):
    """Run prompt-injection classification through the configured LLM client."""

    def get_llm_response(self, prompt: str) -> str:
        response = openAI["mini_llm"].invoke(prompt)
        content = response.content
        if isinstance(content, str):
            return content.strip(" .").lower().strip()
        if isinstance(content, list):
            return "".join(
                block.get("text", "") if isinstance(block, dict) else str(block)
                for block in content
            ).strip(" .").lower().strip()
        return str(content).strip(" .").lower().strip()

PII_ENTITIES = [
    "EMAIL_ADDRESS",
    "PHONE_NUMBER",
    "CREDIT_CARD",
    "US_SSN",
    "IP_ADDRESS",
]

output_guard = Guard(
    name="output-pii-safety",
    description="Blocks PII in generated responses.",
).use(
    DetectPII(
        pii_entities=PII_ENTITIES,
        on_fail="exception",
        use_local=True,
    )
)

input_toxic_guard = Guard(
    name="input-toxicity-safety",
    description="Blocks toxic language in user prompts.",
).use(
    ToxicLanguage(
        threshold=0.3,
        on_fail="exception",
        use_local=True,
    )
)


input_injection_guard = Guard(
    name="input-prompt-injection-safety",
    description="Blocks attempts to override or manipulate the agent.",
).use(
    ProxiedPromptInjectionDetector(
        llm_callable="gpt-5.6-luna",
        threshold=0.8,
        on_fail="exception",
    )
)
