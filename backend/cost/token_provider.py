from abc import ABC, abstractmethod
class TokenProvider(ABC):
    @abstractmethod
    def count_tokens(self, text: str) -> int:
        pass
class HeuristicTokenProvider(TokenProvider):
    """
    Simple word-count based token estimator.
    Rule: 1 word approx 1.3 tokens.
    """
    MULTIPLIER = 1.3
    def count_tokens(self, text: str) -> int:
        if not text:
            return 0
        word_count = len(text.split())
        return int(word_count * self.MULTIPLIER)
# Singleton
token_provider = HeuristicTokenProvider()