from datetime import datetime

from threading import Lock
import traceback
import re
import json
from typing import Optional
import uuid
from configuration import ELKLogRetriever, QDrantLogStore, LogEmbeddingProcessor, LangfuseLogger
from models.chatgpt import ChatGPTAnalyzer
from models.session import UserSession
from rl_query_optimizer import RLQueryOptimizer


class ELKChatbot:
    def __init__(self, es_config, embedding_model, qdrant_db, openai_config, langfuse_keys):
        self.log_retriever = ELKLogRetriever(**es_config)
        self.logger = LangfuseLogger(**langfuse_keys)

        self.embedding_processor = LogEmbeddingProcessor(embedding_model)
        self.qdrant_store = QDrantLogStore(**qdrant_db)
        self.analyzer = ChatGPTAnalyzer(**openai_config)

        self.lock = Lock()
        self.user_sessions: dict[str, UserSession] = {}
        self.rl_optimizer = RLQueryOptimizer()

    def update_session(self, session_id: str, **kwargs):
        with self.lock:
            if not session_id:
                session_id = str(uuid.uuid4())
            session = self.user_sessions.get(session_id) or UserSession(session_id=session_id)
            for key, value in kwargs.items():
                setattr(session, key, value)
            self.user_sessions[session_id] = session

    def get_session(self, session_id: str) -> Optional[UserSession]:
        return self.user_sessions.get(session_id)

    def extract_json_from_response(self, response_text):
        if "[ERROR]" in response_text or "quota" in response_text.lower():
            raise ValueError("❌ GPT call failed due to quota exhaustion or API error.")
        match = re.search(r'```json\s*(\{.*?\})\s*```', response_text, re.DOTALL)
        if not match:
            match = re.search(r'(\{.*\})', response_text, re.DOTALL)
        if not match:
            raise ValueError("No JSON found in GPT response.")
        json_text = re.sub(r'//.*', '', match.group(1))
        try:
            return json.loads(json_text)
        except json.JSONDecodeError as e:
            print("❌ GPT raw response (not valid JSON):", response_text)
            raise ValueError("Failed to extract a valid JSON query from GPT's response.") from e

    def process_query(self, query, session_id):
        try:
            strategy = self.rl_optimizer.choose_strategy()
            strategy_prompt = self.rl_optimizer.get_prompt(strategy)

            session = self.get_session(session_id)
            last_query = session.last_query if session else None
            last_response = session.last_response if session else None

            classification_prompt = f"""
            You are a strict classifier for a log analysis chatbot. Do not explain.

            Your task is to decide if we need to fetch new logs from Elasticsearch (RETRIEVE),
            or if we can answer the question using recent context or general knowledge (RESPOND).

            Respond ONLY with either RETRIEVE or RESPOND. No punctuation, no reasoning.

            Context:
            - Last user query: {last_query}
            - Last response: {last_response}
            - New user query: {query}
            """

            classification_raw = self.analyzer.analyze([classification_prompt]).strip()
            classification = classification_raw.split()[-1].upper()

            if classification == "RESPOND":
                response = self.analyzer.analyze(
                    [f"Provide a response based on previous conversation memory: {query}"]
                ).strip()
                self.update_session(
                    session_id,
                    last_query=query,
                    last_response=response,
                    last_strategy=strategy,
                    updated_at=datetime.utcnow()
                )
                return response

            es_query_prompt = f"""{strategy_prompt}\n\n
            Given the Elasticsearch index mapping below:
            {json.dumps(self.log_retriever.mapping, indent=2)}

            Convert the following natural language query into a valid Elasticsearch query:
            {query}

            Ensure:
            - Identify the most relevant fields from the index mapping dynamically.
            - If the query includes a user reference, include all possible user-related fields dynamically.
            - If no user is explicitly mentioned, default to the last identified prompt used as: {last_query}.
            - Use "term" instead of "match" for exact match fields (such as `keyword` or `long` type fields).
            - Use "term" for "loglevel.keyword" and ensure it is always uppercase.
            - Use "bool" query with "should" inside "must" for multiple user-related fields.
            - Identify the correct timestamp field dynamically (e.g., use "@timestamp" if available).
            - Apply range filtering to the correct timestamp field.
            - loglevel is upper (such as ERROR, WARN, TRACE, DEBUG, INFO).
            - Ensure proper JSON format.
            - Respond only with valid JSON inside a JSON code block.
            """


            # es_query_prompt = f"""{strategy_prompt}\n\nGiven the Elasticsearch index mapping below:
            # {json.dumps(self.log_retriever.mapping, indent=2)}\n\nQuery: {query}"""

            es_query_response = self.analyzer.analyze([es_query_prompt])
            es_query = self.extract_json_from_response(es_query_response)
            es_query["size"] = 10000

            if "query" not in es_query or "bool" not in es_query["query"]:
                raise ValueError("Invalid Elasticsearch query structure generated by GPT.")

            es_response = self.log_retriever.search_logs(es_query)
            if not es_response:
                self.rl_optimizer.log_feedback(
                    query=query,
                    strategy=strategy,
                    response=classification,
                    es_response=False,
                    logs=[],
                    user_feedback="👎"
                )
                return "No relevant logs found."

            logs = es_response if isinstance(es_response, list) else es_response.get("hits", {}).get("hits", [])
            aggregations = es_response.get("aggregations", {}) if isinstance(es_response, dict) else {}

            embeddings = self.embedding_processor.embed_logs(logs)
            self.qdrant_store.store_logs(logs, embeddings)

            query_embedding = self.embedding_processor.embed_query(query)
            similar_logs = self.qdrant_store.search_similar_logs(query_embedding, k=5)

            analysis_prompt = f"""
            You are a helpful and concise assistant.
        
            Analyze the following logs and summarize key user activities:
            {json.dumps(similar_logs, indent=2)}\n\n
            Rules:
            1. Extract and summarize user actions.
            2. Identify actions like LOGIN, NAVIGATION, API CALLS.
            3. Ignore irrelevant system noise.
            4. Summarize repeated actions.
            5. Use retrieved logs to enhance the answer.
            6. Final query: {query}\n\n

            Respond directly and professionally to the user's question below.

            🚫 DO NOT use phrases like:
            - "Based on the previous conversation"
            - "Based on the provided log"
            - "According to the context"
            - "From the earlier message"
            - Or any sentence that begins with "Based on..."""
            analysis = self.analyzer.analyze([analysis_prompt]).strip()

            self.logger.log_interaction(query, analysis)
            self.update_session(session_id, last_query=query, last_response=analysis, last_strategy=strategy, updated_at=datetime.utcnow())
            return analysis

        except Exception as e:
            print(traceback.format_exc())
            return f"An error occurred: {str(e)}"

    def process_feedback(self, session_id, feedback: str):
        session = self.get_session(session_id)
        if not session:
            return "Session not found."
        self.rl_optimizer.log_feedback(
            query=session.last_query,
            strategy=session.last_strategy,
            response=session.last_response,
            es_response=True,
            logs=[session.last_response],
            user_feedback=feedback
        )
        return "Feedback received. Thank you!"
