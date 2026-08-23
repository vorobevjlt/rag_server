from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from src.config.index import appConfig

openAI = {
    "embeddings": OpenAIEmbeddings(
        model="text-embedding-3-large",
        api_key=appConfig["openai_api_key"],
        dimensions=1536
    ),
    "chat_llm": ChatOpenAI(
        model="gpt-5.6-terra", api_key=appConfig["openai_api_key"]
    ),
    "mini_llm": ChatOpenAI(
        model="gpt-5.6-luna", api_key=appConfig["openai_api_key"]
    ),

    "resoning_chat_llm": ChatOpenAI(
        model="gpt-5.6-luna",
        api_key=appConfig["openai_api_key"],
        use_responses_api=True,
        use_previous_response_id=True,
        output_version="responses/v1",
        reasoning_effort="low",
    )
}
