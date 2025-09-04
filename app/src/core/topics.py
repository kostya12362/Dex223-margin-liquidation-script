from __future__ import annotations
from typing import Iterable, Optional
from eth_utils import keccak, to_hex


def _build_topics(abi: list[dict], event_names: Iterable[str]) -> dict[str, str]:
    topics: dict[str, str] = {}
    for name in event_names:
        ev = next(a for a in abi if a.get("name") == name and a["type"] == "event")
        sig = f"{name}(" + ",".join(i["type"] for i in ev["inputs"]) + ")"
        topics[name] = to_hex(keccak(text=sig))
    return topics


class TopicsRegistry:
    """Stores topic0 -> event name and provides lists for get_logs."""

    def __init__(
            self,
            mm_abi: list[dict],
            pool_abi: list[dict],
            asset_events: list[str],
            pool_events: list[str],
    ):
        self._asset = _build_topics(mm_abi, asset_events)  # name -> topic0
        self._pool = _build_topics(pool_abi, pool_events)  # name -> topic0
        # Reverse indices:
        self._by_topic_asset = {v: k for k, v in self._asset.items()}
        self._by_topic_pool = {v: k for k, v in self._pool.items()}

        self._all_topics: list[str] = list(set(self._asset.values()) | set(self._pool.values()))

    @staticmethod
    def topic0_of(log: dict) -> str:
        return log["topics"][0].to_0x_hex()

    @property
    def all_topics(self) -> list[str]:
        return self._all_topics

    def is_asset_topic(self, topic0: str) -> bool:
        return topic0 in self._by_topic_asset

    def is_pool_topic(self, topic0: str) -> bool:
        return topic0 in self._by_topic_pool

    def asset_event_name(self, topic0: str) -> Optional[str]:
        return self._by_topic_asset.get(topic0)

    def pool_event_name(self, topic0: str) -> Optional[str]:
        return self._by_topic_pool.get(topic0)
