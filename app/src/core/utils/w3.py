import json
from eth_utils import keccak, to_hex
from eth_utils.typing import HexStr

ADDRESS_ZERO = '0x0000000000000000000000000000000000000000'


def load_abi(abi_path: str) -> list[dict]:
    """
    Load ABI from a JSON file.
    :param abi_path: Path to the ABI JSON file.
    :return: List of ABI definitions.
    """
    with open(abi_path, "r") as f:
        return json.load(f)


def init_topics(abi: list[dict], events: list[str]) -> dict[str, HexStr]:
    """
    Initialize event topics from ABI for given event names.
    """
    topics = {}
    for name in events:
        ev_abi = next(e for e in abi if e.get("name") == name and e["type"] == "event")
        sig = f"{name}(" + ",".join(inp["type"] for inp in ev_abi["inputs"]) + ")"
        topics[name] = to_hex(keccak(text=sig))
    return topics
