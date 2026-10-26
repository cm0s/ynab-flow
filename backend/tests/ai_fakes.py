"""Fake Claude transport for tests: returns scripted parsed objects and usage, never calls the network."""
from src.ai.client import TokenUsage, TransportResult


class FakeTransport:
    def __init__(self, *results):
        self.results = list(results)
        self.requests = []

    def __call__(self, request, output_type):
        self.requests.append((request, output_type))
        item = self.results.pop(0) if len(self.results) > 1 else self.results[0]
        if isinstance(item, Exception):
            raise item
        if callable(item):
            item = item(request, output_type)
        if isinstance(item, TransportResult):
            return item
        return TransportResult(parsed=item, stop_reason="end_turn",
                               usage=TokenUsage(input_tokens=1000, output_tokens=200), fallbacks_enabled=True)


def refusal():
    return TransportResult(parsed=None, stop_reason="refusal", usage=TokenUsage(input_tokens=500))
