
import random
import json
import os

class RLQueryOptimizer:
    def __init__(self, feedback_file="feedback_store.json"):
        self.feedback_file = feedback_file
        self.strategies = {
            "strict": "Use only exact keyword matches for fields and no fuzziness.",
            "relaxed": "Use match and fuzziness for partial matches.",
            "hybrid": "Use keyword for user fields, but match for log message."
        }
        self.reward_history = {k: 0 for k in self.strategies}
        self.count_history = {k: 1 for k in self.strategies}  # Avoid division by zero

        self._load_feedback()

    def _load_feedback(self):
        if not os.path.exists(self.feedback_file):
            return
        with open(self.feedback_file, "r") as f:
            for line in f:
                try:
                    entry = json.loads(line)
                    strategy = entry.get("strategy")
                    reward = entry.get("reward", 0)
                    if strategy in self.reward_history:
                        self.reward_history[strategy] += reward
                        self.count_history[strategy] += 1
                except Exception:
                    continue

    def choose_strategy(self, epsilon=0.2):
        if random.random() < epsilon:
            return random.choice(list(self.strategies.keys()))
        avg_rewards = {k: self.reward_history[k] / self.count_history[k] for k in self.strategies}
        return max(avg_rewards, key=avg_rewards.get)

    def get_prompt(self, strategy_key):
        return self.strategies.get(strategy_key, "")

    def log_feedback(self, query, strategy, response, es_response, logs, user_feedback):
        reward = 0
        if es_response:
            reward += 1
        if logs:
            reward += 1
        if user_feedback == "👍":
            reward += 2
        elif user_feedback == "👎":
            reward -= 1

        entry = {
            "query": query,
            "strategy": strategy,
            "reward": reward,
            "feedback": user_feedback,
            "response": response
        }
        with open(self.feedback_file, "a") as f:
            f.write(json.dumps(entry) + "\n")
