from threading import Lock
import traceback
import re
import json
from typing import Optional
import uuid
from configuration import ELKLogRetriever, QDrantLogStore, LogEmbeddingProcessor, LangfuseLogger
from models.chatgpt import ChatGPTAnalyzer
from models.session import UserSession


class ELKChatbot:
    def __init__(self, es_config, embedding_model, qdrant_db, openai_config, langfuse_keys):
        self.log_retriever = ELKLogRetriever(**es_config)
        self.logger = LangfuseLogger(**langfuse_keys)

        # Initialize embedding processor and pass it to QDrant
        self.embedding_processor = LogEmbeddingProcessor(embedding_model)
        self.qdrant_store = QDrantLogStore(**qdrant_db)
        self.analyzer = ChatGPTAnalyzer(**openai_config)

        # ✅ Store user-related context
        self.lock = Lock()
        self.user_sessions: dict[str, UserSession] = {}

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
        """Extract a valid JSON object from GPT's response."""
        if "[ERROR]" in response_text or "quota" in response_text.lower():
            raise ValueError("❌ GPT call failed due to quota exhaustion or API error.")

        # Try extracting from ```json fenced block
        match = re.search(r'```json\s*(\{.*?\})\s*```', response_text, re.DOTALL)
        if not match:
            # Fallback: extract first {...} block
            match = re.search(r'(\{.*\})', response_text, re.DOTALL)

        if not match:
            raise ValueError("No JSON found in GPT response.")

        json_text = match.group(1)

        # 🔧 Remove JS-style comments like `// ...`
        json_text = re.sub(r'//.*', '', json_text)

        try:
            return json.loads(json_text)
        except json.JSONDecodeError as e:
            print("❌ GPT raw response (not valid JSON):", response_text)
            raise ValueError("Failed to extract a valid JSON query from GPT's response.") from e


    def process_query(self, query, session_id):
        try:
            # Step 1: Use GPT to determine if ES data is needed
            classification_prompt = f"""
            Determine if the following query requires retrieving logs from Elasticsearch. 
            If yes, return 'RETRIEVE'. If not, return 'RESPOND'.
            Query: {query}
            """
            classification = self.analyzer.analyze([classification_prompt]).strip()

            if classification == "RESPOND":
                response = self.analyzer.analyze(
                    [f"Provide a response based on previous conversation memory: {query}"]
                ).strip()
                return response

            # Step 2: Retrieve Last Identified User from Context
            session = self.get_session(session_id)
            last_user = session.last_query if session else None

            # Step 3: Generate Elasticsearch Query using GPT
            es_query_prompt = f"""
            Given the Elasticsearch index mapping below:
            {json.dumps(self.log_retriever.mapping, indent=2)}

            Convert the following natural language query into a valid Elasticsearch query:
            {query}

            Ensure:
            - Identify the most relevant fields from the index mapping dynamically.
            - If the query includes a user reference, include all possible user-related fields dynamically.
            - If no user is explicitly mentioned, default to the last identified user: {last_user}.
            - Use "term" instead of "match" for exact match fields (such as `keyword` or `long` type fields).
            - Use "term" for "loglevel.keyword" and ensure it is always uppercase.
            - Use "bool" query with "should" inside "must" for multiple user-related fields.
            - Identify the correct timestamp field dynamically (e.g., use "@timestamp" if available).
            - Apply range filtering to the correct timestamp field.
            - loglevel is upper (such as ERROR, WARN, TRACE, DEBUG, INFO).
            - Ensure proper JSON format.
            - Respond only with valid JSON inside a JSON code block.
            """
            es_query_response = self.analyzer.analyze([es_query_prompt])
            es_query = self.extract_json_from_response(es_query_response)

            # Set no size limit (retrieve all relevant logs)
            es_query["size"] = 10000

            # Validate ES Query Structure
            if "query" not in es_query or "bool" not in es_query["query"]:
                raise ValueError("Invalid Elasticsearch query structure generated by GPT.")

            # Step 4: Query Elasticsearch for Logs
            es_response = self.log_retriever.search_logs(es_query)

            if not es_response:
                return "No relevant logs found."

            logs = es_response if isinstance(es_response, list) else es_response.get("hits", {}).get("hits", [])
            aggregations = es_response.get("aggregations", {}) if isinstance(es_response, dict) else {}

            # Step 5: Store Logs in QDrant for Semantic Search
            embeddings = self.embedding_processor.embed_logs(logs)
            self.qdrant_store.store_logs(logs, embeddings)

            # Step 6: Retrieve Similar Logs from QDrant
            query_embedding = self.embedding_processor.embed_query(query)
            similar_logs = self.qdrant_store.search_similar_logs(query_embedding, k=5)

            # Step 7: Analyze and Summarize Logs using GPT
            analysis_prompt = f"""
            Analyze the following logs and summarize key user activities:

            {json.dumps(similar_logs, indent=2)}

            Rules:
            1️⃣ Extract and summarize user actions based on logs, even if they are not explicitly listed in aggregations.
            2️⃣ Identify **explicit user actions** such as LOGIN, NAVIGATION, FORM SUBMISSION, API CALLS.
            3️⃣ Ignore system errors unless they are directly related to a user's action.
            4️⃣ If logs contain similar repeated actions, summarize them instead of listing each individually.
            5️⃣ Do **not** claim there is no activity if logs contain user actions.
            6️⃣ Enhance the analysis using the similar logs retrieved from QDrant.
            7️⃣ Answer the follow-up question in brief and professional:
            {query}
            """
            analysis = self.analyzer.analyze([analysis_prompt]).strip()

            self.logger.log_interaction(query, analysis)
            return analysis

        except Exception as e:
            error_message = f"An error occurred: {str(e)}"
            print(traceback.format_exc())
            return error_message
