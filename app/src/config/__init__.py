from typing import Optional
from web3 import Web3
from pathlib import Path
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_PATH = Path(__file__).resolve().parent.parent.parent


class Settings(BaseSettings):
    DEBUG: bool = True
    LOG_PATH: Path = ROOT_PATH / "logs"
    LOG_LEVEL: str = "INFO"
    HTTP_RPC_URL: str
    PRIVATE_KEY: str
    FEE_TIERS: list[int] = [500, 3000, 10000]
    MARGIN_MODULE_ADDRESS: str
    LIQUIDATE_ADDRESS: Optional[str] = None
    DATABASE_URL: str
    ABI_PATH: Path = ROOT_PATH / "src" / 'abi'
    model_config = SettingsConfigDict(
        env_file=ROOT_PATH / ".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        nested_model_default_partial_update=True
    )

    @model_validator(mode='before')
    @classmethod
    def validate_addresses(cls, values):
        if 'MARGIN_MODULE_ADDRESS' in values:
            values['MARGIN_MODULE_ADDRESS'] = Web3.to_checksum_address(values['MARGIN_MODULE_ADDRESS'])
        if 'LIQUIDATE_ADDRESS' in values and values['LIQUIDATE_ADDRESS']:
            values['LIQUIDATE_ADDRESS'] = Web3.to_checksum_address(values['LIQUIDATE_ADDRESS'])
        return values


settings = Settings()
